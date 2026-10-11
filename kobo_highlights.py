#!/usr/bin/env python3
"""
kobo_highlights.py — Extrae los resaltes de un eReader Kobo, normalizados y
listos para pegar (en Notion, Obsidian, un .txt...).

Para ver todas las opciones y ejemplos:
    python kobo_highlights.py --help
"""

import argparse
import glob
import os
import re
import shutil
import sqlite3
import string
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date

VERSION = "2.1.1"
CARPETA_SALIDA_POR_DEFECTO = os.path.join(os.path.expanduser("~"), "Documents", "kobo_highlights")

# ---------------------------------------------------------------------------
# Normalización de texto
# ---------------------------------------------------------------------------

INVISIBLES = ["\u00ad", "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"]  # guion blando, ancho cero, BOM
ESPACIOS_RAROS = ["\u00a0", "\u202f", "\u2007", "\u2009", "\u200a", "\u3000"]  # espacios no separables, finos...
FINAL_DE_FRASE = tuple('.!?…:»"”')
INICIO_DE_DIALOGO = tuple("—–-«“")


def _es_salto_de_parrafo_real(anterior: str, siguiente: str) -> bool:
    """Un salto de línea es un párrafo real si la línea anterior termina una
    frase o la siguiente empieza un diálogo. Si no, es un corte de maquetación."""
    return anterior.endswith(FINAL_DE_FRASE) or siguiente.startswith(INICIO_DE_DIALOGO)


def normalizar_texto(texto: str, conservar_parrafos: bool = True) -> str:
    """Limpia un resalte de Kobo para que se lea de corrido."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFC", texto)
    for c in INVISIBLES:
        t = t.replace(c, "")
    for c in ESPACIOS_RAROS:
        t = t.replace(c, " ")
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")

    # Palabras partidas con guion a final de línea: "pala-\nbra" -> "palabra"
    t = re.sub(r"(\w)-[ ]*\n\s*([a-záéíóúüñ])", r"\1\2", t)
    # Compuestos partidos a final de línea: "Norte-\nSur" -> "Norte-Sur"
    t = re.sub(r"(\w)-[ ]*\n\s*(?=\w)", r"\1-", t)

    # Unir líneas cortadas por la maquetación, conservando párrafos reales
    lineas = [re.sub(r" {2,}", " ", l).strip() for l in t.split("\n")]
    lineas = [l for l in lineas if l]
    parrafos: list[str] = []
    for linea in lineas:
        if parrafos and not (conservar_parrafos and _es_salto_de_parrafo_real(parrafos[-1], linea)):
            parrafos[-1] += " " + linea
        else:
            parrafos.append(linea)
    t = "\n".join(parrafos)

    # Espacios mal colocados alrededor de la puntuación
    t = re.sub(r"[ ]+([,.;:!?…)\]»”])", r"\1", t)
    t = re.sub(r"([¿¡(\[«“])[ ]+", r"\1", t)
    return t.strip()


# ---------------------------------------------------------------------------
# Acceso a la base de datos
# ---------------------------------------------------------------------------

def detectar_kobo() -> str | None:
    """Busca .kobo/KoboReader.sqlite en las unidades montadas."""
    if os.name == "nt":
        bases = [f"{letra}:\\" for letra in string.ascii_uppercase[2:]]  # de C: a Z:
    elif sys.platform == "darwin":
        bases = glob.glob("/Volumes/*")
    else:
        usuario = os.environ.get("USER", "")
        bases = glob.glob(f"/media/{usuario}/*") + glob.glob(f"/run/media/{usuario}/*") + glob.glob("/media/*")
    for base in bases:
        ruta = os.path.join(base, ".kobo", "KoboReader.sqlite")
        if os.path.isfile(ruta):
            return ruta
    return None


def abrir_copia_segura(ruta_db: str) -> tuple[sqlite3.Connection, str]:
    """Copia la base de datos a una carpeta temporal y la abre desde ahí.
    Así nunca se bloquea ni se modifica la base de datos del Kobo."""
    carpeta_tmp = tempfile.mkdtemp(prefix="kobo_")
    for sufijo in ("", "-wal", "-shm"):  # incluir el WAL por si hay cambios sin volcar
        if os.path.exists(ruta_db + sufijo):
            shutil.copy2(ruta_db + sufijo, os.path.join(carpeta_tmp, "KoboReader.sqlite" + sufijo))
    con = sqlite3.connect(os.path.join(carpeta_tmp, "KoboReader.sqlite"))
    return con, carpeta_tmp


def _columnas(con: sqlite3.Connection, tabla: str) -> set[str]:
    return {fila[1].lower() for fila in con.execute(f"PRAGMA table_info({tabla})")}


def _col(alias: str, nombre: str, existentes: set[str]) -> str:
    """Devuelve 'alias.nombre' si la columna existe en este firmware, o NULL."""
    return f"{alias}.{nombre}" if nombre.lower() in existentes else "NULL"


@dataclass
class Libro:
    volume_id: str
    titulo: str
    autor: str
    num_resaltes: int


@dataclass
class Resalte:
    texto: str
    nota: str
    capitulo: str
    orden: tuple


def obtener_libros(con: sqlite3.Connection) -> list[Libro]:
    cols_c = _columnas(con, "content")
    filas = con.execute(f"""
        SELECT b.VolumeID,
               COALESCE(libro.Title, MAX(cap.BookTitle), b.VolumeID) AS titulo,
               COALESCE({_col('libro', 'Attribution', cols_c)}, '') AS autor,
               COUNT(*) AS n
        FROM Bookmark b
        LEFT JOIN content libro ON libro.ContentID = b.VolumeID
        LEFT JOIN content cap   ON cap.ContentID   = b.ContentID
        WHERE b.Text IS NOT NULL AND TRIM(b.Text) <> ''
        GROUP BY b.VolumeID
        ORDER BY titulo COLLATE NOCASE
    """).fetchall()
    return [Libro(v, t, a, n) for v, t, a, n in filas]


def _numero_capitulo_en_id(content_id: str) -> int | None:
    """Respaldo para EPUB: el ContentID suele llevar '#(N)'."""
    m = re.search(r"\((\d+)\)", content_id or "")
    return int(m.group(1)) if m else None


def _clave_posicion(ruta: str | None) -> tuple:
    """'span#kobo\\.12\\.3' -> (12, 3); '/1/4/2/1:0' -> (1, 4, 2, 1, 0)"""
    return tuple(int(n) for n in re.findall(r"\d+", ruta or ""))


def obtener_resaltes(con: sqlite3.Connection, volume_id: str, conservar_parrafos: bool = True) -> list[Resalte]:
    cols_b = _columnas(con, "Bookmark")
    cols_c = _columnas(con, "content")
    filas = con.execute(f"""
        SELECT b.Text,
               {_col('b', 'Annotation', cols_b)},
               b.ContentID,
               cap.Title,
               {_col('cap', 'VolumeIndex', cols_c)},
               {_col('b', 'ChapterProgress', cols_b)},
               {_col('b', 'StartContainerPath', cols_b)},
               b.DateCreated
        FROM Bookmark b
        LEFT JOIN content cap ON cap.ContentID = b.ContentID
        WHERE b.VolumeID = ?
          AND b.Text IS NOT NULL AND TRIM(b.Text) <> ''
    """, (volume_id,)).fetchall()

    resaltes = []
    for texto, nota, content_id, titulo_cap, vol_idx, progreso, ruta, fecha in filas:
        indice = vol_idx if vol_idx is not None and vol_idx >= 0 else _numero_capitulo_en_id(content_id)
        orden = (
            indice is None, indice or 0,     # 1º capítulo (orden de lectura)
            progreso or 0.0,                 # 2º posición dentro del capítulo
            _clave_posicion(ruta),           # 3º desempate fino
            fecha or "",                     # 4º fecha, como último recurso
        )
        capitulo = (titulo_cap or "").strip() or (f"Sección {indice}" if indice is not None else "Sin capítulo")
        resaltes.append(Resalte(
            texto=normalizar_texto(texto, conservar_parrafos),
            nota=normalizar_texto(nota or "", conservar_parrafos),
            capitulo=capitulo,
            orden=orden,
        ))
    resaltes.sort(key=lambda r: r.orden)
    return resaltes


# ---------------------------------------------------------------------------
# Formato de salida
# ---------------------------------------------------------------------------

def formatear(libro: Libro, resaltes: list[Resalte], formato: str) -> str:
    """'md' se pega en Notion con títulos y citas ya formateados; 'txt' es texto plano."""
    out = []
    cabecera = libro.titulo + (f" — {libro.autor}" if libro.autor else "")
    n = len(resaltes)
    resumen = f"{n} {'resalte' if n == 1 else 'resaltes'} · exportado el {date.today():%d/%m/%Y}"
    out.append(f"# {cabecera}\n\n{resumen}\n" if formato == "md" else f"{cabecera}\n{resumen}\n")

    capitulo_actual = None
    for r in resaltes:
        if r.capitulo != capitulo_actual:
            capitulo_actual = r.capitulo
            out.append(f"\n## {r.capitulo}\n" if formato == "md" else f"\n{r.capitulo.upper()}\n")
        if formato == "md":
            out.append("\n".join(f"> {linea}" for linea in r.texto.split("\n")))
            if r.nota:
                out.append(f"\n📝 {r.nota}")
            out.append("")
        else:
            out.append("- " + r.texto.replace("\n", "\n  "))
            if r.nota:
                out.append(f"  Nota: {r.nota}")
            out.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def nombre_archivo(libro: Libro, formato: str) -> str:
    base = libro.titulo + (f" - {libro.autor}" if libro.autor else "")
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", base).strip().rstrip(".")[:80]
    return f"{base}.{formato}"


def copiar_al_portapapeles(texto: str) -> bool:
    try:
        if os.name == "nt":
            subprocess.run(["clip"], input=texto.encode("utf-16"), check=True)
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=texto.encode("utf-8"), check=True)
        elif shutil.which("wl-copy"):
            subprocess.run(["wl-copy"], input=texto.encode("utf-8"), check=True)
        elif shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard"], input=texto.encode("utf-8"), check=True)
        else:
            return False
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


# ---------------------------------------------------------------------------
# Interfaz de línea de comandos
# ---------------------------------------------------------------------------

def parsear_seleccion(entrada: str, total: int) -> list[int]:
    """Admite '3', '1,4,7', '2-5' o combinaciones. Devuelve índices base 0."""
    indices = set()
    for parte in entrada.replace(" ", "").split(","):
        if re.fullmatch(r"\d+-\d+", parte):
            a, b = map(int, parte.split("-"))
            indices.update(range(min(a, b), max(a, b) + 1))
        elif parte.isdigit():
            indices.add(int(parte))
        else:
            raise ValueError(parte)
    fuera = [i for i in indices if not 1 <= i <= total]
    if fuera:
        raise ValueError(f"fuera de rango: {fuera}")
    return sorted(i - 1 for i in indices)


def exportar(con, libro: Libro, args) -> str:
    resaltes = obtener_resaltes(con, libro.volume_id, conservar_parrafos=not args.sin_parrafos)
    contenido = formatear(libro, resaltes, args.formato)
    ruta = os.path.join(args.salida, nombre_archivo(libro, args.formato))
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    n = len(resaltes)
    print(f"  ✅ {libro.titulo} ({n} {'resalte' if n == 1 else 'resaltes'}) → {ruta}")
    return contenido


DESCRIPCION = """\
Exporta los resaltes y notas de tu Kobo, ordenados como en el libro y con
el texto limpio (sin saltos de línea a mitad de frase), listos para pegar.

Conecta el Kobo por USB y ejecuta el script. Si no indicas ninguna opción,
se abre un menú para elegir qué libros exportar."""

EJEMPLOS = """\
ejemplos:
  Abrir el menú para elegir libros (lo más habitual):
    python kobo_highlights.py

  Elegir libros y copiar el resultado al portapapeles para pegarlo en Notion:
    python kobo_highlights.py --copiar

  Exportar todos los libros de golpe:
    python kobo_highlights.py --todos

  Exportar todos en texto plano (.txt) en lugar de Markdown (.md):
    python kobo_highlights.py --todos --formato txt

  Cada resalte en un único párrafo, sin saltos de línea dentro:
    python kobo_highlights.py --sin-parrafos

  Guardar los archivos en otra carpeta:
    python kobo_highlights.py --salida "C:\\Users\\Jorge\\Desktop\\resaltes"

  Si el Kobo no se detecta solo, indicar la ruta de su base de datos:
    python kobo_highlights.py --db "F:\\.kobo\\KoboReader.sqlite"

  Combinar opciones con los atajos cortos (equivale a --todos --formato txt --copiar):
    python kobo_highlights.py -t -f txt -c

en el menú interactivo:
  3        exporta el libro número 3
  1,4,7    exporta los libros 1, 4 y 7
  2-5      exporta del libro 2 al 5
  t        exporta todos los libros
  h        muestra esta ayuda del menú
  q        sale del programa

formatos de salida:
  md   Markdown. Al pegarlo en Notion, el título, los capítulos y las citas
       aparecen ya formateados. Es el formato por defecto.
  txt  Texto plano con viñetas, para pegar en cualquier sitio.
"""

AYUDA_MENU = """
  3        exporta el libro número 3
  1,4,7    exporta varios libros
  2-5      exporta un rango de libros
  t        exporta todos
  h        muestra esta ayuda
  q        salir   (para ver todas las opciones: python kobo_highlights.py --help)"""


class _FormatoAyuda(argparse.RawDescriptionHelpFormatter):
    """Mantiene los saltos de línea de los ejemplos y traduce 'usage:'."""
    def add_usage(self, usage, actions, groups, prefix=None):
        return super().add_usage(usage, actions, groups, prefix="uso: ")


def crear_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kobo_highlights.py",
        description=DESCRIPCION,
        epilog=EJEMPLOS,
        formatter_class=_FormatoAyuda,
        add_help=False,
    )
    g_que = p.add_argument_group("qué exportar")
    g_que.add_argument("-t", "--todos", action="store_true",
                       help="exporta todos los libros sin mostrar el menú")

    g_como = p.add_argument_group("cómo exportar")
    g_como.add_argument("-f", "--formato", choices=["md", "txt"], default="md", metavar="{md,txt}",
                        help="formato del archivo: md (para Notion) o txt (texto plano). Por defecto: md")
    g_como.add_argument("-p", "--sin-parrafos", action="store_true",
                        help="deja cada resalte en un único párrafo, sin saltos de línea")
    g_como.add_argument("-c", "--copiar", action="store_true",
                        help="copia al portapapeles lo exportado, listo para pegar")

    g_donde = p.add_argument_group("rutas")
    g_donde.add_argument("-s", "--salida", default=CARPETA_SALIDA_POR_DEFECTO, metavar="CARPETA",
                         help=f"carpeta donde se guardan los archivos. Por defecto: {CARPETA_SALIDA_POR_DEFECTO}")
    g_donde.add_argument("-d", "--db", metavar="RUTA",
                         help="ruta a KoboReader.sqlite. Por defecto se busca el Kobo conectado")

    g_otros = p.add_argument_group("otros")
    g_otros.add_argument("-h", "--help", action="help", help="muestra esta ayuda y sale")
    g_otros.add_argument("-v", "--version", action="version", version=f"%(prog)s {VERSION}",
                         help="muestra la versión y sale")
    return p


def _copiar_y_avisar(textos: list[str]) -> None:
    ok = copiar_al_portapapeles("\n\n".join(textos))
    print("  📋 Copiado al portapapeles." if ok else "  ⚠️  No se pudo copiar al portapapeles.")


def main(argv=None):
    args = crear_parser().parse_args(argv)

    ruta_db = args.db or detectar_kobo()
    if not ruta_db or not os.path.isfile(ruta_db):
        sys.exit("❌ No encuentro el Kobo. Conéctalo o indica la ruta con --db.")
    os.makedirs(args.salida, exist_ok=True)

    con, carpeta_tmp = abrir_copia_segura(ruta_db)
    try:
        libros = obtener_libros(con)
        if not libros:
            print("No se encontraron libros con resaltes.")
            return

        if args.todos:
            textos = [exportar(con, libro, args) for libro in libros]
            if args.copiar:
                _copiar_y_avisar(textos)
            return

        while True:
            print(f"\n📚 Libros con resaltes ({len(libros)}):\n")
            for i, libro in enumerate(libros, 1):
                hecho = " ✓" if os.path.exists(os.path.join(args.salida, nombre_archivo(libro, args.formato))) else ""
                autor = f" — {libro.autor}" if libro.autor else ""
                print(f"{i:3d}. {libro.titulo}{autor}  [{libro.num_resaltes}]{hecho}")

            entrada = input("\nElige libro(s) (ej. 3 · 1,4 · 2-5 · t = todos · h = ayuda · q = salir): ").strip().lower()
            if entrada in ("q", ""):
                break
            if entrada in ("h", "?", "ayuda", "help"):
                print(AYUDA_MENU)
                continue
            try:
                seleccion = list(range(len(libros))) if entrada == "t" else parsear_seleccion(entrada, len(libros))
            except ValueError:
                print("⚠️  Selección no válida. Escribe 'h' para ver la ayuda.")
                continue

            textos = [exportar(con, libros[i], args) for i in seleccion]
            if args.copiar:
                _copiar_y_avisar(textos)
    finally:
        con.close()
        shutil.rmtree(carpeta_tmp, ignore_errors=True)
    print("¡Hasta luego!")


if __name__ == "__main__":
    main()
