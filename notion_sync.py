"""
notion_sync.py — Envía los resaltes de Kobo a la página de cada libro en Notion.

Funcionamiento:
  - Busca la página del libro en tu base de datos de Notion por el título.
  - Si no existe, pregunta si crearla (con título y autor).
  - Si existe, lee lo que ya hay y añade solo los resaltes que falten, al final.
    Nunca modifica ni borra lo que ya está en la página.

Configuración (archivo .env junto al script, o variables de entorno):
  NOTION_TOKEN=ntn_...              token de tu integración interna
  NOTION_DATABASE_ID=...            ID o URL de la base de datos de libros
                                    (o de la página que la contiene)
  NOTION_AUTHOR_PROPERTY=Autor      (opcional) nombre de la columna del autor

Usa la API de Notion versión 2025-09-03 (bases de datos con "data sources")
y solo la biblioteca estándar de Python.
"""

import difflib
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date

API_URL = "https://api.notion.com/v1"
API_VERSION = "2025-09-03"
MAX_CARACTERES_TEXTO = 2000   # límite de Notion por fragmento de texto
MAX_BLOQUES_POR_PETICION = 100  # límite de Notion al añadir bloques
NOMBRES_AUTOR = ("autor", "autora", "autores", "author", "authors", "escritor")


class ErrorNotion(Exception):
    def __init__(self, codigo: int, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje

    def explicacion(self) -> str:
        """Mensaje en español con la causa más probable."""
        if self.codigo == 401:
            return "El token de Notion no es válido. Revisa NOTION_TOKEN en el archivo .env."
        if self.codigo == 404:
            return ("No encuentro la base de datos o la página. Comprueba NOTION_DATABASE_ID y que la "
                    "integración está conectada a la base de datos (menú ··· → Conexiones).")
        if self.codigo == 403:
            return "La integración no tiene permiso. Revisa sus capacidades (leer, insertar y actualizar contenido)."
        if self.codigo == 0:
            return f"No hay conexión con Notion: {self.mensaje}"
        return f"Notion respondió con un error ({self.codigo}): {self.mensaje}"


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def _leer_env(ruta: str) -> dict:
    valores = {}
    try:
        with open(ruta, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                clave, valor = linea.split("=", 1)
                valores[clave.strip()] = valor.strip().strip('"').strip("'")
    except OSError:
        pass
    return valores


def extraer_id(texto: str) -> str:
    """Acepta un ID (con o sin guiones) o la URL de la base de datos."""
    texto = texto.strip().split("?")[0]  # en la URL, lo que va tras '?v=' es la vista, no la base de datos
    encontrados = re.findall(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}", texto)
    if not encontrados:
        return ""
    limpio = encontrados[-1].replace("-", "").lower()
    return f"{limpio[:8]}-{limpio[8:12]}-{limpio[12:16]}-{limpio[16:20]}-{limpio[20:]}"


def cargar_config(carpetas: list[str]) -> dict | None:
    """Lee .env de las carpetas indicadas; las variables de entorno tienen prioridad.
    Devuelve None si Notion no está configurado."""
    valores = {}
    for carpeta in carpetas:
        valores.update(_leer_env(os.path.join(carpeta, ".env")))
    for clave in ("NOTION_TOKEN", "NOTION_DATABASE_ID", "NOTION_AUTHOR_PROPERTY", "NOTION_PROPIEDAD_AUTOR"):
        if os.environ.get(clave):
            valores[clave] = os.environ[clave]
    token = valores.get("NOTION_TOKEN", "")
    database_id = extraer_id(valores.get("NOTION_DATABASE_ID", ""))
    if not token or not database_id:
        return None
    autor = valores.get("NOTION_AUTHOR_PROPERTY") or valores.get("NOTION_PROPIEDAD_AUTOR")  # nombre antiguo
    return {"token": token, "database_id": database_id, "propiedad_autor": autor}


# ---------------------------------------------------------------------------
# Cliente HTTP
# ---------------------------------------------------------------------------

class ClienteNotion:
    def __init__(self, token: str, pausa: float = 0.35):
        self.token = token
        self.pausa = pausa  # Notion permite ~3 peticiones por segundo de media

    def peticion(self, metodo: str, ruta: str, cuerpo: dict | None = None) -> dict:
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        for intento in range(5):
            req = urllib.request.Request(
                API_URL + ruta, data=datos, method=metodo,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Notion-Version": API_VERSION,
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    respuesta = json.load(r)
                time.sleep(self.pausa)
                return respuesta
            except urllib.error.HTTPError as e:
                if (e.code == 429 or e.code >= 500) and intento < 4:
                    time.sleep(float(e.headers.get("Retry-After") or 1 + intento))
                    continue
                try:
                    mensaje = json.load(e).get("message", str(e))
                except Exception:
                    mensaje = str(e)
                raise ErrorNotion(e.code, mensaje) from None
            except urllib.error.URLError as e:
                raise ErrorNotion(0, str(e.reason)) from None
        raise ErrorNotion(429, "Demasiadas peticiones; inténtalo de nuevo en un rato.")


# ---------------------------------------------------------------------------
# Utilidades de texto y bloques
# ---------------------------------------------------------------------------

def clave_titulo(titulo: str) -> str:
    """Normaliza un título para comparar: sin tildes, mayúsculas ni signos."""
    t = unicodedata.normalize("NFKD", titulo or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def compactar(texto: str) -> str:
    """Huella de un texto para detectar duplicados aunque esté mal maquetado:
    une palabras partidas, quita signos, espacios y mayúsculas."""
    t = (texto or "").replace("­", "")
    t = re.sub(r"-\s*\n\s*", "", t)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return re.sub(r"[\W_]+", "", t)


def _rich_text(contenido: str, cursiva: bool = False) -> list[dict]:
    trozos = [contenido[i:i + MAX_CARACTERES_TEXTO] for i in range(0, len(contenido), MAX_CARACTERES_TEXTO)] or [""]
    return [{"type": "text", "text": {"content": t}, "annotations": {"italic": cursiva}} for t in trozos]


def _bloque(tipo: str, contenido: str, cursiva: bool = False) -> dict:
    return {"object": "block", "type": tipo, tipo: {"rich_text": _rich_text(contenido, cursiva)}}


def construir_bloques(resaltes, fecha: date | None = None) -> list[dict]:
    """Encabezado con la fecha, un subtítulo por capítulo, una cita por resalte y la nota debajo."""
    fecha = fecha or date.today()
    bloques = [_bloque("heading_2", f"Resaltes de Kobo · {fecha:%d/%m/%Y}")]
    capitulo = None
    for r in resaltes:
        if r.capitulo != capitulo:
            capitulo = r.capitulo
            bloques.append(_bloque("heading_3", capitulo))
        bloques.append(_bloque("quote", r.texto))
        if r.nota:
            bloques.append(_bloque("paragraph", f"📝 {r.nota}", cursiva=True))
    return bloques


def _texto_plano(rich_text: list[dict]) -> str:
    return "".join(t.get("plain_text") or t.get("text", {}).get("content", "") for t in rich_text or [])


def _trozos(lista: list, tamano: int):
    for i in range(0, len(lista), tamano):
        yield lista[i:i + tamano]


# ---------------------------------------------------------------------------
# Sincronización
# ---------------------------------------------------------------------------

@dataclass
class PaginaLibro:
    id: str
    titulo: str
    clave: str


@dataclass
class Resultado:
    titulo: str
    estado: str          # "creada", "actualizada", "sin_cambios", "omitida", "por_confirmar"
    anadidos: int = 0
    ya_estaban: int = 0


class SincronizadorNotion:
    def __init__(self, cliente, database_id: str, propiedad_autor: str | None = None,
                 prueba: bool = False, preguntar=input, mostrar=print):
        self.cliente = cliente
        self.database_id = database_id
        self.propiedad_autor_preferida = propiedad_autor
        self.prueba = prueba
        self.preguntar = preguntar
        self.mostrar = mostrar
        self._preparado = False

    # --- Lectura de la base de datos -------------------------------------

    def _preparar(self):
        if self._preparado:
            return
        try:
            base = self.cliente.peticion("GET", f"/databases/{self.database_id}")
        except ErrorNotion as e:
            # Es habitual copiar la URL de la página que contiene la tabla, no la de la tabla
            if e.codigo != 400 or "is a page" not in e.mensaje:
                raise
            self.database_id = self._buscar_base_en_pagina(self.database_id)
            base = self.cliente.peticion("GET", f"/databases/{self.database_id}")
        fuentes = base.get("data_sources") or []
        if not fuentes:
            raise ErrorNotion(400, "La base de datos no tiene ninguna fuente de datos.")
        self.data_source_id = fuentes[0]["id"]

        esquema = self.cliente.peticion("GET", f"/data_sources/{self.data_source_id}")
        propiedades = esquema.get("properties", {})
        self.prop_titulo = next(n for n, p in propiedades.items() if p.get("type") == "title")
        self.prop_autor, self.tipo_autor = None, None
        for nombre, p in propiedades.items():
            elegida = (nombre == self.propiedad_autor_preferida) if self.propiedad_autor_preferida \
                else nombre.strip().casefold() in NOMBRES_AUTOR
            if elegida and p.get("type") in ("rich_text", "select", "multi_select"):
                self.prop_autor, self.tipo_autor = nombre, p["type"]
                break

        self.paginas: list[PaginaLibro] = []
        cursor = None
        while True:
            cuerpo = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            r = self.cliente.peticion("POST", f"/data_sources/{self.data_source_id}/query", cuerpo)
            for pagina in r.get("results", []):
                titulo = _texto_plano(pagina.get("properties", {}).get(self.prop_titulo, {}).get("title"))
                self.paginas.append(PaginaLibro(pagina["id"], titulo, clave_titulo(titulo)))
            if not r.get("has_more"):
                break
            cursor = r.get("next_cursor")
        self._preparado = True

    def _buscar_base_en_pagina(self, page_id: str) -> str:
        """Busca las bases de datos que hay dentro de una página y devuelve el ID de la elegida."""
        bases, cursor = [], None
        while True:
            ruta = f"/blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
            r = self.cliente.peticion("GET", ruta)
            for bloque in r.get("results", []):
                if bloque.get("type") == "child_database":
                    bases.append((bloque["id"], bloque.get("child_database", {}).get("title") or "(sin título)"))
            if not r.get("has_more"):
                break
            cursor = r.get("next_cursor")

        if not bases:
            raise ErrorNotion(400, "La URL es de una página que no contiene ninguna base de datos propia. "
                                   "Abre la tabla de libros a pantalla completa y copia esa URL en NOTION_DATABASE_ID. "
                                   "(Si la tabla es una vista enlazada de otra, usa la URL de la original.)")
        if len(bases) == 1:
            elegida = bases[0]
        else:
            self.mostrar("   La página contiene varias bases de datos:")
            for i, (_, titulo) in enumerate(bases, 1):
                self.mostrar(f"     {i}. {titulo}")
            respuesta = self.preguntar("   ¿Cuál es la de libros? (número): ").strip()
            if not (respuesta.isdigit() and 1 <= int(respuesta) <= len(bases)):
                raise ErrorNotion(400, "No se ha elegido ninguna base de datos.")
            elegida = bases[int(respuesta) - 1]
        self.mostrar(f"   ℹ️  Usando la base de datos «{elegida[1]}» de esa página.")
        self.mostrar(f"      Para saltarte este paso, pon en .env: NOTION_DATABASE_ID={elegida[0]}")
        return elegida[0]

    def candidatas(self, titulo: str) -> tuple[list[PaginaLibro], list[PaginaLibro]]:
        """Devuelve (coincidencias exactas, títulos parecidos).
        Exacta: igual ignorando tildes, mayúsculas y signos.
        Parecida: uno contiene al otro (p. ej. con o sin subtítulo o saga) o se parecen mucho."""
        self._preparar()
        clave = clave_titulo(titulo)
        exactas = [p for p in self.paginas if p.clave == clave]
        if exactas:
            return exactas, []
        puntuadas = []
        for p in self.paginas:
            if not p.clave:
                continue
            contiene = min(len(p.clave), len(clave)) >= 4 and (p.clave in clave or clave in p.clave)
            parecido = difflib.SequenceMatcher(None, clave, p.clave).ratio()
            if contiene or parecido >= 0.6:
                puntuadas.append((parecido + (1 if contiene else 0), p))
        puntuadas.sort(key=lambda x: -x[0])
        return [], [p for _, p in puntuadas[:5]]

    def buscar_pagina(self, titulo: str) -> PaginaLibro | None:
        """Devuelve la página del libro. Si no hay una coincidencia exacta única,
        muestra las parecidas y deja elegir."""
        exactas, parecidas = self.candidatas(titulo)
        if len(exactas) == 1:
            return exactas[0]
        opciones = exactas or parecidas
        if not opciones:
            return None
        if exactas:
            self.mostrar(f"   Hay varias páginas llamadas «{titulo}»:")
        else:
            self.mostrar(f"   En Notion no hay una página llamada exactamente «{titulo}». Parecidas:")
        for i, p in enumerate(opciones, 1):
            self.mostrar(f"     {i}. {p.titulo}")
        respuesta = self.preguntar("   ¿Cuál es? (número, o 0 si ninguna): ").strip()
        if respuesta.isdigit() and 1 <= int(respuesta) <= len(opciones):
            return opciones[int(respuesta) - 1]
        return None

    def _textos_existentes(self, page_id: str) -> tuple[int, str]:
        """Devuelve cuántos bloques tiene la página y la huella de todo su texto."""
        total, huellas, cursor = 0, [], None
        while True:
            ruta = f"/blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
            r = self.cliente.peticion("GET", ruta)
            for bloque in r.get("results", []):
                total += 1
                contenido = bloque.get(bloque.get("type"), {})
                if isinstance(contenido, dict):
                    huellas.append(compactar(_texto_plano(contenido.get("rich_text"))))
            if not r.get("has_more"):
                break
            cursor = r.get("next_cursor")
        return total, "\n".join(huellas)

    # --- Escritura -------------------------------------------------------

    def _valor_autor(self, autor: str) -> dict | None:
        if not (self.prop_autor and autor):
            return None
        if self.tipo_autor == "rich_text":
            return {"rich_text": _rich_text(autor)}
        if self.tipo_autor == "select":
            return {"select": {"name": autor.replace(",", " ")[:100]}}  # Notion no admite comas en opciones
        nombres = [a.strip() for a in re.split(r",|&| y ", autor) if a.strip()]
        return {"multi_select": [{"name": n[:100]} for n in nombres]}

    def _crear_pagina(self, titulo: str, autor: str, bloques: list[dict]) -> str:
        propiedades = {self.prop_titulo: {"title": _rich_text(titulo)}}
        valor_autor = self._valor_autor(autor)
        if valor_autor:
            propiedades[self.prop_autor] = valor_autor
        primeros, resto = bloques[:MAX_BLOQUES_POR_PETICION], bloques[MAX_BLOQUES_POR_PETICION:]
        pagina = self.cliente.peticion("POST", "/pages", {
            "parent": {"type": "data_source_id", "data_source_id": self.data_source_id},
            "properties": propiedades,
            "children": primeros,
        })
        self._anadir_bloques(pagina["id"], resto)
        self.paginas.append(PaginaLibro(pagina["id"], titulo, clave_titulo(titulo)))
        return pagina["id"]

    def _anadir_bloques(self, page_id: str, bloques: list[dict]):
        for trozo in _trozos(bloques, MAX_BLOQUES_POR_PETICION):
            self.cliente.peticion("PATCH", f"/blocks/{page_id}/children", {"children": trozo})

    # --- Proceso de un libro --------------------------------------------

    def sincronizar_libro(self, titulo: str, autor: str, resaltes: list) -> Resultado:
        if self.prueba:
            exactas, parecidas = self.candidatas(titulo)
            if len(exactas) != 1 and (exactas or parecidas):
                self.mostrar(f"   (prueba) En Notion no hay una única página llamada exactamente «{titulo}».")
                self.mostrar("   En el envío real te preguntaría si es alguna de estas:")
                for p in exactas or parecidas:
                    self.mostrar(f"     · {p.titulo}")
                return Resultado(titulo, "por_confirmar")
            pagina = exactas[0] if exactas else None
        else:
            pagina = self.buscar_pagina(titulo)

        if pagina is None:
            total = len(self.paginas)
            if self.prueba:
                self.mostrar(f"   (prueba) «{titulo}» no se parece a ninguna de las {total} páginas de tu base "
                             f"de datos: se crearía una página nueva con {len(resaltes)} resaltes.")
                return Resultado(titulo, "creada", anadidos=len(resaltes))
            pregunta = (f"   «{titulo}» no está entre las {total} páginas de tu base de datos. "
                        f"¿Crear una página nueva? (s/n): ")
            if self.preguntar(pregunta).strip().lower() != "s":
                return Resultado(titulo, "omitida")
            self._crear_pagina(titulo, autor, construir_bloques(resaltes))
            return Resultado(titulo, "creada", anadidos=len(resaltes))

        num_bloques, existente = self._textos_existentes(pagina.id)
        nuevos, repetidos = [], 0
        for r in resaltes:
            huella = compactar(r.texto)
            # Los resaltes muy cortos solo cuentan como repetidos si coinciden con un bloque entero
            ya_esta = (huella in existente) if len(huella) >= 15 else (huella in existente.split("\n"))
            if huella and ya_esta:
                repetidos += 1
            else:
                nuevos.append(r)

        if not nuevos:
            return Resultado(titulo, "sin_cambios", ya_estaban=repetidos)
        if self.prueba:
            self.mostrar(f"   (prueba) Se añadirían {len(nuevos)} resaltes a «{pagina.titulo}» "
                         f"({repetidos} ya estaban).")
            return Resultado(titulo, "actualizada", anadidos=len(nuevos), ya_estaban=repetidos)
        if num_bloques:
            pregunta = (f"   «{pagina.titulo}» ya tiene contenido en Notion. Se añadirán {len(nuevos)} resaltes "
                        f"nuevos al final ({repetidos} ya estaban); lo que hay no se toca. ¿Continuar? (s/n): ")
            if self.preguntar(pregunta).strip().lower() != "s":
                return Resultado(titulo, "omitida", ya_estaban=repetidos)
        self._anadir_bloques(pagina.id, construir_bloques(nuevos))
        return Resultado(titulo, "actualizada", anadidos=len(nuevos), ya_estaban=repetidos)
