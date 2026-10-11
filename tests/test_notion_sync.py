"""Tests de notion_sync.py con un Notion simulado en memoria (no se llama a la API real)."""

import sqlite3
from datetime import date

import pytest

import kobo_highlights as kh
import notion_sync as ns


# ---------------------------------------------------------------------------
# Notion simulado
# ---------------------------------------------------------------------------

def _rt(texto):
    return [{"type": "text", "plain_text": texto, "text": {"content": texto}}]


class NotionFalso:
    """Imita las rutas de la API que usa el sincronizador."""

    def __init__(self, libros=None, tipo_autor="rich_text", nombre_autor="Autor", bases_en_pagina=None):
        # bases_en_pagina: si se indica, el ID configurado es una página que contiene estas bases de datos
        self.bases_en_pagina = bases_en_pagina
        self.tipo_autor = tipo_autor
        self.nombre_autor = nombre_autor
        self.paginas = {}   # id -> {"titulo": str, "props": dict}
        self.bloques = {}   # id -> [bloques]
        self.escrituras = []
        for i, (titulo, textos) in enumerate((libros or {}).items()):
            pid = f"pag{i}"
            self.paginas[pid] = {"titulo": titulo, "props": {}}
            self.bloques[pid] = [{"type": "paragraph", "paragraph": {"rich_text": _rt(t)}} for t in textos]

    def peticion(self, metodo, ruta, cuerpo=None):
        if metodo == "GET" and ruta.startswith("/databases/"):
            db_id = ruta.split("/")[2]
            if self.bases_en_pagina is not None and db_id == "db":
                raise ns.ErrorNotion(400, f"Provided database_id {db_id} is a page, not a database.")
            return {"data_sources": [{"id": "ds1", "name": "Libros"}]}
        if metodo == "GET" and ruta.startswith("/blocks/db/children"):
            return {"results": [{"id": i, "type": "child_database", "child_database": {"title": t}}
                                for i, t in self.bases_en_pagina] + [{"type": "paragraph", "paragraph": {}}],
                    "has_more": False}
        if metodo == "GET" and ruta == "/data_sources/ds1":
            return {"properties": {
                "Nombre": {"type": "title"},
                self.nombre_autor: {"type": self.tipo_autor},
                "Estado": {"type": "status"},
            }}
        if metodo == "POST" and ruta == "/data_sources/ds1/query":
            return {"results": [
                {"id": pid, "properties": {"Nombre": {"title": _rt(p["titulo"])}}}
                for pid, p in self.paginas.items()
            ], "has_more": False}
        if metodo == "GET" and ruta.startswith("/blocks/"):
            pid = ruta.split("/")[2]
            return {"results": self.bloques.get(pid, []), "has_more": False}
        if metodo == "POST" and ruta == "/pages":
            self.escrituras.append((metodo, ruta, cuerpo))
            assert cuerpo["parent"] == {"type": "data_source_id", "data_source_id": "ds1"}
            assert len(cuerpo["children"]) <= ns.MAX_BLOQUES_POR_PETICION
            pid = f"nueva{len(self.paginas)}"
            titulo = cuerpo["properties"]["Nombre"]["title"][0]["text"]["content"]
            self.paginas[pid] = {"titulo": titulo, "props": cuerpo["properties"]}
            self.bloques[pid] = list(cuerpo["children"])
            return {"id": pid}
        if metodo == "PATCH" and ruta.startswith("/blocks/"):
            self.escrituras.append((metodo, ruta, cuerpo))
            assert len(cuerpo["children"]) <= ns.MAX_BLOQUES_POR_PETICION
            self.bloques[ruta.split("/")[2]].extend(cuerpo["children"])
            return {"results": []}
        raise AssertionError(f"Ruta no simulada: {metodo} {ruta}")

    def textos(self, pid):
        return [ns._texto_plano(b[b["type"]]["rich_text"]) for b in self.bloques[pid]]


def R(texto, capitulo="Capítulo 1", nota=""):
    return kh.Resalte(texto=texto, nota=nota, capitulo=capitulo, orden=())


def sincronizador(notion, respuestas=(), prueba=False):
    respuestas = list(respuestas)
    preguntas = []

    def preguntar(texto):
        preguntas.append(texto)
        return respuestas.pop(0)

    sync = ns.SincronizadorNotion(notion, "db", prueba=prueba, preguntar=preguntar, mostrar=lambda *a: None)
    return sync, preguntas


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def test_extraer_id_de_url_ignora_la_vista():
    url = "https://www.notion.so/miespacio/Libros-1a2b3c4d5e6f70819a2b3c4d5e6f7081?v=ffffffffffffffffffffffffffffffff"
    assert ns.extraer_id(url) == "1a2b3c4d-5e6f-7081-9a2b-3c4d5e6f7081"


def test_extraer_id_con_guiones():
    assert ns.extraer_id("1a2b3c4d-5e6f-7081-9a2b-3c4d5e6f7081") == "1a2b3c4d-5e6f-7081-9a2b-3c4d5e6f7081"


def test_cargar_config_desde_env(tmp_path, monkeypatch):
    for v in ("NOTION_TOKEN", "NOTION_DATABASE_ID", "NOTION_AUTHOR_PROPERTY", "NOTION_PROPIEDAD_AUTOR"):
        monkeypatch.delenv(v, raising=False)
    (tmp_path / ".env").write_text(
        "# comentario\nNOTION_TOKEN=ntn_prueba\nNOTION_DATABASE_ID=\"1a2b3c4d5e6f70819a2b3c4d5e6f7081\"\n",
        encoding="utf-8")
    config = ns.cargar_config([str(tmp_path)])
    assert config["token"] == "ntn_prueba"
    assert config["database_id"] == "1a2b3c4d-5e6f-7081-9a2b-3c4d5e6f7081"


def test_sin_config_devuelve_none(tmp_path, monkeypatch):
    for v in ("NOTION_TOKEN", "NOTION_DATABASE_ID"):
        monkeypatch.delenv(v, raising=False)
    assert ns.cargar_config([str(tmp_path)]) is None


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def test_clave_titulo_ignora_tildes_y_signos():
    assert ns.clave_titulo("¡Cien años de soledad!") == ns.clave_titulo("cien anos de soledad")


def test_compactar_reconoce_texto_mal_maquetado():
    assert ns.compactar("estaba en silen-\ncio , total.") == ns.compactar("Estaba en silencio, total")


def test_textos_largos_se_dividen():
    largo = "a" * (ns.MAX_CARACTERES_TEXTO * 2 + 5)
    bloque = ns._bloque("quote", largo)
    assert [len(t["text"]["content"]) for t in bloque["quote"]["rich_text"]] == [2000, 2000, 5]


def test_construir_bloques():
    bloques = ns.construir_bloques([R("Uno."), R("Dos.", nota="Mi nota"), R("Tres.", capitulo="Capítulo 2")],
                                   fecha=date(2026, 10, 11))
    assert [b["type"] for b in bloques] == ["heading_2", "heading_3", "quote", "quote", "paragraph",
                                           "heading_3", "quote"]
    assert ns._texto_plano(bloques[0]["heading_2"]["rich_text"]) == "Resaltes de Kobo · 11/10/2026"


# ---------------------------------------------------------------------------
# Sincronización
# ---------------------------------------------------------------------------

def test_crea_pagina_si_no_existe_y_se_confirma():
    notion = NotionFalso()
    sync, preguntas = sincronizador(notion, respuestas=["s"])
    r = sync.sincronizar_libro("Libro nuevo", "Autora Inventada", [R("Uno."), R("Dos.")])
    assert r.estado == "creada" and r.anadidos == 2
    pid = next(iter(notion.paginas))
    assert notion.paginas[pid]["props"]["Autor"]["rich_text"][0]["text"]["content"] == "Autora Inventada"
    assert "Uno." in notion.textos(pid)


def test_no_crea_pagina_si_se_responde_no():
    notion = NotionFalso()
    sync, _ = sincronizador(notion, respuestas=["n"])
    assert sync.sincronizar_libro("Libro nuevo", "", [R("Uno.")]).estado == "omitida"
    assert notion.escrituras == []


@pytest.mark.parametrize("tipo, esperado", [
    ("select", {"select": {"name": "Ana  Luis"}}),
    ("multi_select", {"multi_select": [{"name": "Ana"}, {"name": "Luis"}]}),
])
def test_autor_segun_tipo_de_columna(tipo, esperado):
    notion = NotionFalso(tipo_autor=tipo)
    sync, _ = sincronizador(notion, respuestas=["s"])
    sync.sincronizar_libro("Libro", "Ana, Luis", [R("Uno.")])
    props = next(iter(notion.paginas.values()))["props"]
    assert props["Autor"] == esperado


def test_pagina_existente_solo_anade_los_que_faltan():
    # El primer resalte ya se pegó a mano, sin normalizar
    notion = NotionFalso({"El libro": ["La primera fra-\nse , de prueba completa."]})
    sync, preguntas = sincronizador(notion, respuestas=["s"])
    r = sync.sincronizar_libro("El libro", "", [R("La primera frase, de prueba completa."), R("Una frase nueva de prueba.")])
    assert (r.estado, r.anadidos, r.ya_estaban) == ("actualizada", 1, 1)
    assert "ya tiene contenido" in preguntas[0]
    textos = notion.textos("pag0")
    assert textos[0] == "La primera fra-\nse , de prueba completa."  # lo existente no se toca
    assert textos[-1] == "Una frase nueva de prueba."


def test_pagina_existente_no_se_toca_si_se_responde_no():
    notion = NotionFalso({"El libro": ["Algo que escribí yo."]})
    sync, _ = sincronizador(notion, respuestas=["n"])
    assert sync.sincronizar_libro("El libro", "", [R("Un resalte nuevo bastante largo.")]).estado == "omitida"
    assert notion.escrituras == []


def test_sin_cambios_si_todo_esta_ya():
    notion = NotionFalso({"El libro": ["Un resalte que ya está en la página."]})
    sync, preguntas = sincronizador(notion)
    r = sync.sincronizar_libro("El libro", "", [R("Un resalte que ya está en la página.")])
    assert r.estado == "sin_cambios" and preguntas == [] and notion.escrituras == []


def test_resalte_corto_no_se_da_por_repetido_por_estar_dentro_de_otro():
    notion = NotionFalso({"El libro": ["Dijo que sí sin pensarlo dos veces."]})
    sync, _ = sincronizador(notion, respuestas=["s"])
    assert sync.sincronizar_libro("El libro", "", [R("Sí.")]).anadidos == 1


def test_titulo_con_tildes_distintas_encuentra_la_pagina():
    notion = NotionFalso({"Cien años de soledad": []})
    sync, preguntas = sincronizador(notion)
    r = sync.sincronizar_libro("Cien Anos de Soledad", "", [R("Un resalte cualquiera.")])
    assert r.estado == "actualizada" and preguntas == []  # página vacía: no hace falta preguntar


def test_titulo_parecido_pregunta_cual_es():
    notion = NotionFalso({"Sapiens": [], "Otro": []})
    sync, preguntas = sincronizador(notion, respuestas=["1"])
    r = sync.sincronizar_libro("Sapiens: De animales a dioses", "", [R("Un resalte cualquiera.")])
    assert r.estado == "actualizada"
    assert "número" in preguntas[0]


def test_muchos_resaltes_se_envian_en_lotes_de_100():
    notion = NotionFalso({"El libro": []})
    sync, _ = sincronizador(notion)
    resaltes = [R(f"Resalte número {i} de una lista larga.") for i in range(250)]
    assert sync.sincronizar_libro("El libro", "", resaltes).anadidos == 250
    patches = [e for e in notion.escrituras if e[0] == "PATCH"]
    assert [len(e[2]["children"]) for e in patches] == [100, 100, 52]  # 250 citas + 2 encabezados


def test_modo_prueba_no_escribe_nada():
    notion = NotionFalso({"El libro": ["Algo que ya había."]})
    sync, preguntas = sincronizador(notion, prueba=True)
    assert sync.sincronizar_libro("El libro", "", [R("Un resalte nuevo de prueba.")]).anadidos == 1
    assert sync.sincronizar_libro("No existe", "", [R("Otro resalte.")]).estado == "creada"
    assert notion.escrituras == [] and preguntas == []


# ---------------------------------------------------------------------------
# Confirmaciones en el script principal
# ---------------------------------------------------------------------------

@pytest.fixture
def con_dos_libros(tmp_path):
    ruta = tmp_path / "KoboReader.sqlite"
    con = sqlite3.connect(ruta)
    con.executescript("""
        CREATE TABLE content(ContentID TEXT PRIMARY KEY, ContentType INT, BookID TEXT, BookTitle TEXT,
                             Title TEXT, Attribution TEXT, VolumeIndex INT);
        CREATE TABLE Bookmark(BookmarkID TEXT, VolumeID TEXT, ContentID TEXT, Text TEXT, Annotation TEXT,
                              DateCreated TEXT, ChapterProgress REAL, StartContainerPath TEXT, Type TEXT);
        INSERT INTO content VALUES ('a', 6, NULL, NULL, 'Libro A', '', -1);
        INSERT INTO content VALUES ('b', 6, NULL, NULL, 'Libro B', '', -1);
        INSERT INTO Bookmark VALUES ('1', 'a', 'a', 'Resalte del libro A.', NULL, '', 0.1, '', 'highlight');
        INSERT INTO Bookmark VALUES ('2', 'b', 'b', 'Resalte del libro B.', NULL, '', 0.1, '', 'highlight');
    """)
    con.commit()
    yield con
    con.close()


class _Args:
    dry_run = False
    no_paragraphs = False


def _preparar_envio(monkeypatch, respuestas):
    notion = NotionFalso({"Libro A": [], "Libro B": []})
    monkeypatch.setattr(ns, "ClienteNotion", lambda token: notion)
    respuestas = list(respuestas)
    monkeypatch.setattr("builtins.input", lambda texto="": respuestas.pop(0))
    return notion


def test_todos_pide_segunda_confirmacion_y_cancela(monkeypatch, con_dos_libros):
    notion = _preparar_envio(monkeypatch, ["s", "no"])
    kh.enviar_a_notion(con_dos_libros, kh.obtener_libros(con_dos_libros), _Args(), {"token": "t", "database_id": "db"},
                       todos=True)
    assert notion.escrituras == []


def test_todos_con_confirmacion_envia(monkeypatch, con_dos_libros):
    notion = _preparar_envio(monkeypatch, ["s", "si"])
    kh.enviar_a_notion(con_dos_libros, kh.obtener_libros(con_dos_libros), _Args(), {"token": "t", "database_id": "db"},
                       todos=True)
    assert len(notion.escrituras) == 2


def test_responder_no_a_notion_no_envia_nada(monkeypatch, con_dos_libros):
    notion = _preparar_envio(monkeypatch, ["n"])
    libro_a = kh.obtener_libros(con_dos_libros)[:1]
    kh.enviar_a_notion(con_dos_libros, libro_a, _Args(), {"token": "t", "database_id": "db"}, todos=False)
    assert notion.escrituras == []


# ---------------------------------------------------------------------------
# La URL configurada es la de una página que contiene la tabla
# ---------------------------------------------------------------------------

def test_url_de_pagina_usa_la_base_de_datos_que_contiene():
    notion = NotionFalso({"El libro": []}, bases_en_pagina=[("tabla1", "Libros")])
    sync, preguntas = sincronizador(notion)
    assert sync.sincronizar_libro("El libro", "", [R("Un resalte cualquiera.")]).estado == "actualizada"
    assert sync.database_id == "tabla1" and preguntas == []


def test_pagina_con_varias_bases_pregunta_cual():
    notion = NotionFalso({"El libro": []}, bases_en_pagina=[("t1", "Películas"), ("t2", "Libros")])
    sync, preguntas = sincronizador(notion, respuestas=["2"])
    sync.sincronizar_libro("El libro", "", [R("Un resalte cualquiera.")])
    assert sync.database_id == "t2" and "número" in preguntas[0]


def test_pagina_sin_bases_da_error_claro():
    notion = NotionFalso(bases_en_pagina=[])
    sync, _ = sincronizador(notion)
    with pytest.raises(ns.ErrorNotion) as error:
        sync.sincronizar_libro("El libro", "", [R("Un resalte cualquiera.")])
    assert "pantalla completa" in error.value.mensaje


def test_otros_errores_400_no_se_tratan_como_pagina():
    class NotionQueFalla(NotionFalso):
        def peticion(self, metodo, ruta, cuerpo=None):
            raise ns.ErrorNotion(400, "body failed validation")
    sync, _ = sincronizador(NotionQueFalla())
    with pytest.raises(ns.ErrorNotion) as error:
        sync.sincronizar_libro("El libro", "", [R("Uno.")])
    assert "validation" in error.value.mensaje


def test_nombre_antiguo_de_variable_de_autor_sigue_funcionando(tmp_path, monkeypatch):
    for v in ("NOTION_TOKEN", "NOTION_DATABASE_ID", "NOTION_AUTHOR_PROPERTY", "NOTION_PROPIEDAD_AUTOR"):
        monkeypatch.delenv(v, raising=False)
    (tmp_path / ".env").write_text("NOTION_TOKEN=t\nNOTION_DATABASE_ID=1a2b3c4d5e6f70819a2b3c4d5e6f7081\n"
                                   "NOTION_PROPIEDAD_AUTOR=Escritor\n", encoding="utf-8")
    assert ns.cargar_config([str(tmp_path)])["propiedad_autor"] == "Escritor"


# ---------------------------------------------------------------------------
# Títulos que no coinciden exactamente
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("titulo_kobo, titulo_notion", [
    ("El nombre del viento (Crónica del asesino de reyes 1)", "El nombre del viento"),  # con saga
    ("Hábitos atómicos: Cambios pequeños, resultados extraordinarios", "Hábitos Atómicos"),  # con subtítulo
    ("Crónica del asesino de reyes 1. El nombre del viento", "El nombre del viento"),  # saga delante
    ("El hombre en busca de sentido", "El hombre en busca del sentido"),  # casi igual
])
def test_titulos_parecidos_se_proponen(titulo_kobo, titulo_notion):
    notion = NotionFalso({titulo_notion: [], "Otro libro distinto": []})
    sync, preguntas = sincronizador(notion, respuestas=["1"])
    assert sync.sincronizar_libro(titulo_kobo, "", [R("Un resalte cualquiera.")]).estado == "actualizada"
    assert notion.escrituras and all(e[0] == "PATCH" for e in notion.escrituras)  # no crea página nueva


def test_prueba_con_titulo_parecido_no_dice_que_crearia():
    notion = NotionFalso({"El nombre del viento": []})
    mensajes = []
    sync = ns.SincronizadorNotion(notion, "db", prueba=True, preguntar=None, mostrar=mensajes.append)
    r = sync.sincronizar_libro("El nombre del viento (Crónica del asesino de reyes 1)", "", [R("Uno.")])
    assert r.estado == "por_confirmar"
    assert any("El nombre del viento" in m for m in mensajes)
    assert not any("se crearía" in m for m in mensajes)


def test_titulo_sin_parecido_dice_cuantas_paginas_hay():
    notion = NotionFalso({"Libro A": [], "Libro B": []})
    mensajes = []
    sync = ns.SincronizadorNotion(notion, "db", prueba=True, preguntar=None, mostrar=mensajes.append)
    assert sync.sincronizar_libro("Algo completamente diferente", "", [R("Uno.")]).estado == "creada"
    assert any("2 páginas" in m for m in mensajes)
