"""Tests de kobo_highlights.py.

Usan una base de datos inventada con la misma estructura que la del Kobo y
textos de ejemplo propios (no fragmentos de libros reales).
"""

import sqlite3

import pytest

import kobo_highlights as kh


# ---------------------------------------------------------------------------
# Base de datos de prueba
# ---------------------------------------------------------------------------

@pytest.fixture
def ruta_db(tmp_path):
    ruta = tmp_path / ".kobo" / "KoboReader.sqlite"
    ruta.parent.mkdir()
    con = sqlite3.connect(ruta)
    con.executescript("""
        CREATE TABLE content(
            ContentID TEXT PRIMARY KEY, ContentType INT, BookID TEXT, BookTitle TEXT,
            Title TEXT, Attribution TEXT, VolumeIndex INT);
        CREATE TABLE Bookmark(
            BookmarkID TEXT, VolumeID TEXT, ContentID TEXT, Text TEXT, Annotation TEXT,
            DateCreated TEXT, ChapterProgress REAL, StartContainerPath TEXT, Type TEXT);

        INSERT INTO content VALUES ('libroA', 6, NULL, NULL, 'Libro de prueba', 'Autora Inventada', -1);
        INSERT INTO content VALUES ('libroA#(2)c2.xhtml', 9, 'libroA', 'Libro de prueba', 'Capítulo dos', NULL, 2);
        INSERT INTO content VALUES ('libroA#(1)c1.xhtml', 9, 'libroA', 'Libro de prueba', 'Capítulo uno', NULL, 1);
        INSERT INTO content VALUES ('libroB', 6, NULL, NULL, 'Otro libro', NULL, -1);
        INSERT INTO content VALUES ('libroB#(4)x.xhtml', 9, 'libroB', 'Otro libro', NULL, NULL, NULL);
    """)
    con.executemany(
        "INSERT INTO Bookmark VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'highlight')",
        [
            # Resaltado primero en el tiempo, pero está en el capítulo 2
            ("1", "libroA", "libroA#(2)c2.xhtml", "Frase del capítulo\ndos.", "Mi nota", "2026-01-01", 0.5, "span#kobo\\.5\\.1"),
            # Mismo capítulo 1: el segundo en el tiempo va antes en el libro
            ("2", "libroA", "libroA#(1)c1.xhtml", "Segunda del capítulo uno.", None, "2026-01-02", 0.8, "span#kobo\\.30\\.1"),
            ("3", "libroA", "libroA#(1)c1.xhtml", "Primera del capítulo uno.", None, "2026-01-03", 0.1, "span#kobo\\.2\\.1"),
            ("4", "libroB", "libroB#(4)x.xhtml", "Un resalte sin título de capítulo.", None, "2026-02-01", 0.2, "/1/4/2"),
            # Resalte vacío: debe ignorarse
            ("5", "libroB", "libroB#(4)x.xhtml", "   ", None, "2026-02-01", 0.3, "/1/4/6"),
        ],
    )
    con.commit()
    con.close()
    return str(ruta)


@pytest.fixture
def con(ruta_db):
    conexion, carpeta_tmp = kh.abrir_copia_segura(ruta_db)
    yield conexion
    conexion.close()


# ---------------------------------------------------------------------------
# Normalización de texto
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entrada, esperado", [
    # Líneas cortadas por la maquetación
    ("una frase que sigue\nen la línea siguiente.", "una frase que sigue en la línea siguiente."),
    # Palabras partidas con guion
    ("estaba en silen-\ncio total.", "estaba en silencio total."),
    # Guion blando invisible y espacio no separable
    ("pala­bra con espacio", "palabra con espacio"),
    # Espacios repetidos y espacios antes de la puntuación
    ("mucho    espacio , y más .", "mucho espacio, y más."),
    # Espacios después de signos de apertura
    ("¿ Seguro ? ¡ Sí !", "¿Seguro? ¡Sí!"),
    # Salto real tras final de frase: se conserva
    ("Primera frase.\nSegunda frase.", "Primera frase.\nSegunda frase."),
    # Diálogo en línea nueva: se conserva
    ("dijo algo\n—Vale —respondió.", "dijo algo\n—Vale —respondió."),
    # Líneas en blanco y espacios al principio y al final
    ("\n\n   texto   \n\n", "texto"),
    ("", ""),
])
def test_normalizar_texto(entrada, esperado):
    assert kh.normalizar_texto(entrada) == esperado


def test_normalizar_sin_parrafos_une_todo():
    texto = "Primera frase.\nSegunda frase."
    assert kh.normalizar_texto(texto, conservar_parrafos=False) == "Primera frase. Segunda frase."


def test_no_une_guion_si_sigue_mayuscula():
    # "Norte-\nSur" es un compuesto, no una palabra partida
    assert kh.normalizar_texto("eje Norte-\nSur") == "eje Norte-Sur"


# ---------------------------------------------------------------------------
# Lectura de la base de datos
# ---------------------------------------------------------------------------

def test_obtener_libros(con):
    libros = kh.obtener_libros(con)
    assert [(l.titulo, l.autor, l.num_resaltes) for l in libros] == [
        ("Libro de prueba", "Autora Inventada", 3),
        ("Otro libro", "", 1),  # el resalte vacío no cuenta
    ]


def test_resaltes_en_orden_de_lectura(con):
    resaltes = kh.obtener_resaltes(con, "libroA")
    assert [r.texto for r in resaltes] == [
        "Primera del capítulo uno.",
        "Segunda del capítulo uno.",
        "Frase del capítulo dos.",
    ]
    assert [r.capitulo for r in resaltes] == ["Capítulo uno", "Capítulo uno", "Capítulo dos"]
    assert resaltes[2].nota == "Mi nota"


def test_capitulo_sin_titulo_usa_numero_de_seccion(con):
    (resalte,) = kh.obtener_resaltes(con, "libroB")
    assert resalte.capitulo == "Sección 4"


def test_la_copia_no_modifica_el_original(ruta_db, tmp_path):
    with open(ruta_db, "rb") as f:
        antes = f.read()
    conexion, carpeta_tmp = kh.abrir_copia_segura(ruta_db)
    conexion.execute("DELETE FROM Bookmark")
    conexion.commit()
    conexion.close()
    with open(ruta_db, "rb") as f:
        assert f.read() == antes


# ---------------------------------------------------------------------------
# Formato de salida
# ---------------------------------------------------------------------------

def test_formato_markdown(con):
    libro = kh.obtener_libros(con)[0]
    md = kh.formatear(libro, kh.obtener_resaltes(con, libro.volume_id), "md")
    assert md.startswith("# Libro de prueba — Autora Inventada\n")
    assert "3 resaltes" in md
    assert "## Capítulo uno" in md
    assert "> Primera del capítulo uno." in md
    assert "📝 Mi nota" in md
    assert "\n\n\n" not in md


def test_formato_txt(con):
    libro = kh.obtener_libros(con)[0]
    txt = kh.formatear(libro, kh.obtener_resaltes(con, libro.volume_id), "txt")
    assert "CAPÍTULO UNO" in txt
    assert "- Primera del capítulo uno." in txt
    assert "  Nota: Mi nota" in txt


def test_singular_con_un_resalte(con):
    libro = kh.obtener_libros(con)[1]
    assert "1 resalte ·" in kh.formatear(libro, kh.obtener_resaltes(con, libro.volume_id), "md")


def test_nombre_archivo_sin_caracteres_invalidos():
    libro = kh.Libro("x", 'Título: con "comillas" / barras?', "Autor", 1)
    assert kh.nombre_archivo(libro, "md") == "Título con comillas  barras - Autor.md"


# ---------------------------------------------------------------------------
# Menú y línea de comandos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entrada, esperado", [
    ("3", [2]),
    ("1,3", [0, 2]),
    ("2-4", [1, 2, 3]),
    ("4-2", [1, 2, 3]),
    ("1, 2-3", [0, 1, 2]),
])
def test_parsear_seleccion(entrada, esperado):
    assert kh.parsear_seleccion(entrada, total=5) == esperado


@pytest.mark.parametrize("entrada", ["0", "6", "abc", "1-9"])
def test_parsear_seleccion_invalida(entrada):
    with pytest.raises(ValueError):
        kh.parsear_seleccion(entrada, total=5)


def test_exportar_todos(ruta_db, tmp_path):
    salida = tmp_path / "salida"
    kh.main(["--db", ruta_db, "--output", str(salida), "--all", "--no-notion"])
    archivos = sorted(p.name for p in salida.iterdir())
    assert archivos == ["Libro de prueba - Autora Inventada.md", "Otro libro.md"]


def test_sin_kobo_sale_con_error(tmp_path):
    with pytest.raises(SystemExit):
        kh.main(["--db", str(tmp_path / "no_existe.sqlite"), "--output", str(tmp_path), "--no-notion"])
