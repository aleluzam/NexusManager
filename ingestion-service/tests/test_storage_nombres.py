"""Paso 0.3 — `nombre_seguro` sin colisiones.

Antes de este paso, `nombre_seguro` solo normalizaba el nombre:
`re.sub(r"[^A-Za-z0-9._-]", "_", nombre)`. Con eso, `mi video.mp4` y
`mi_video.mp4` acababan los dos en `mi_video.mp4` dentro de la misma carpeta de
lote: el segundo upload **pisaba** el fichero del primero y la fila del primer
`archivos` quedaba apuntando a un archivo que ya no era el suyo. La API no daba
ninguna señal: `/lotes/{id}/clips` seguía contando los clips previstos.

Dos síntomas distintos, un solo arreglo:
  - dos nombres que colisionaban por normalización;
  - dos uploads del **mismo** nombre (que colisionan bajo cualquier hash del
    nombre, y por eso el sufijo es `uuid4().hex[:12]`, no un hash del nombre).
"""

import asyncio
import io
import os
import re

import pytest
from sqlalchemy import select
from starlette.datastructures import UploadFile

from app import storage
from app.db import SessionLocal
from app.models import Archivo, Lote


# --- La pieza pura: `nombre_seguro` -----------------------------------------
def test_dos_nombres_que_colisionaban_ya_no_colisionan():
    """`mi video.mp4` y `mi_video.mp4` dan rutas distintas.

    Es el caso del enunciado del Paso 0.3. Antes los dos normalizaban a
    `mi_video.mp4` y el segundo upload se comía el primero.
    """
    a = storage.nombre_seguro("mi video.mp4")
    b = storage.nombre_seguro("mi_video.mp4")
    assert a != b, f"mismo nombre tras normalizar: {a}"
    # Y el prefijo legible se conserva: solo cambia el token.
    assert a.startswith("mi_video_") and b.startswith("mi_video_")


def test_dos_uploads_del_mismo_nombre_no_colisionan():
    """El caso que ningún hash del nombre arregla: el mismo nombre dos veces."""
    nombres = {storage.nombre_seguro("clip.mp4") for _ in range(50)}
    assert len(nombres) == 50, "el token no parece aleatorio"


def test_el_token_es_aleatorio_y_de_12_hex():
    """El sufijo tiene la forma prometida: 12 hex, siempre distintos del stem."""
    salida = storage.nombre_seguro("video.mp4")
    stem, ext = os.path.splitext(salida)
    token = stem.rsplit("_", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{12}", token), f"token con forma rara: {token!r}"


def test_el_sufijo_explicito_se_usa():
    """`nombre_seguro(nombre, sufijo=...)` pone ese sufijo tal cual.

    Con sufijo explícito **no** hay token aleatorio: la unicidad pasa a ser del
    llamante (los clips, por ejemplo, ya van numerados por `cortar_y_normalizar`).
    """
    assert storage.nombre_seguro("mi video.mp4", sufijo="clip0") == "mi_video_clip0.mp4"


def test_conserva_la_extension():
    """La extensión se conserva: `detectar_tipo` y ffmpeg dependen de ella.

    Si `nombre_seguro` devolviera `mi_video_<token>` sin `.mp4`, el fichero guardado
    ya no se detectaría como vídeo y `ffmpeg` no sabría qué es.
    """
    for nombre, esperado in [
        ("mi video.mp4", ".mp4"),
        ("clip.MOV", ".MOV"),
        ("promo.webm", ".webm"),
        ("sin_extension", ""),
    ]:
        salida = storage.nombre_seguro(nombre)
        assert salida.endswith(esperado), f"{nombre!r} -> {salida!r}"

    # Y el round-trip por el detector de tipos sigue funcionando tras el nombre.
    assert storage.detectar_tipo(storage.nombre_seguro("mi video.mp4")) == "video"
    assert storage.detectar_tipo(storage.nombre_seguro("notas.txt")) == "doc"


def test_extension_falsa_se_descarta():
    """Lo que no es una extensión se tira en vez de propagarse al nombre.

    La regla es `\\.[A-Za-z0-9]{1,10}`: `.mp4` pasa y `.exe` también (es un
    nombre legal), pero un punto final o una "extensión" con símbolos no. Si el
    filtro desapareciera, `video.` acabaría en `video_.<token>`, con el token
    pegado a un punto y sin extensión reconocible.
    """
    sin_ext = storage.nombre_seguro("video.")
    assert sin_ext.count(".") == 0, f"el punto final se ha colado: {sin_ext!r}"

    simbolo = storage.nombre_seguro("archivo.$")
    assert not simbolo.endswith(".$"), simbolo
    assert simbolo.count(".") == 0, simbolo

    # `video.mp4/largo` es un path: el basename se queda con `largo`, sin extensión.
    assert not storage.nombre_seguro("video.mp4/largo").endswith(".mp4")
    # Y una extensión válida se conserva aunque el nombre tenga varios puntos.
    assert storage.nombre_seguro("a.b.c.mp4").endswith(".mp4")
    for nombre in ("video.", "archivo.$", "a.b.c.mp4", "video.mp4/largo"):
        assert re.fullmatch(r"[A-Za-z0-9._-]+", storage.nombre_seguro(nombre))


@pytest.mark.parametrize(
    "nombre",
    [
        "..",
        ".",
        "../..",
        "./mi video.mp4",
        "../../etc/passwd",
        "....//....//etc/shadow",
        "/etc/passwd",
        "..\\..\\windows",
        "../",
    ],
)
def test_traversal_no_se_escapa_de_la_carpeta(nombre):
    """Nada de traversal: ni `.`/`..`, ni `/`, ni salir de la carpeta del lote.

    El ataque es real si `os.path.join(carpeta, nombre_seguro(".."))` devuelve
    `carpeta` (o su padre): el upload escribiría fuera del directorio del lote,
    que es lo único que lo separa del resto de `/storage`.
    """
    salida = storage.nombre_seguro(nombre)

    assert salida, f"nombre vacío para {nombre!r}"
    assert "/" not in salida, f"queda una barra en {salida!r}"
    assert "\\" not in salida, f"queda una barra invertida en {salida!r}"
    assert not salida.startswith("."), f"empieza por punto: {salida!r}"
    assert os.path.basename(salida) == salida, f"no es un único componente: {salida!r}"
    assert salida not in {".", ".."}, f"nombre de directorio, no de fichero: {salida!r}"

    # Y lo que de verdad importa: el join se queda dentro.
    carpeta = os.path.join("/srv/storage/originales/lote_7")
    ruta = os.path.join(carpeta, salida)
    assert os.path.dirname(ruta) == carpeta, f"el join se sale de la carpeta: {ruta}"
    assert ruta.startswith(carpeta + os.sep)
    # Un solo componente por encima de la carpeta: ni `..` ni un nombre con barras.
    assert os.path.basename(os.path.dirname(ruta)) == "lote_7"


def test_el_nombre_guardado_no_hereda_las_carpetas_del_cliente():
    """Del nombre que envía el cliente solo se queda el fichero.

    `os.path.basename` es la primera defensa, pero no la que evita el traversal:
    el `re.sub` ya convierte cada `/` en `_`, así que aunque `basename` no
    estuviera, ningún nombre con barras se escaparía. Lo que sí se pierde sin
    `basename` es la higiene: el nombre en disco arrastra la estructura de
    carpetas que mandó el cliente, y `mi video.mp4` subido desde
    `carpeta/subcarpeta/` acabaría como `carpeta_subcarpeta_mi_video_<token>.mp4`.

    Es un test de comportamiento, no de implementación: sigue siendo cierto sea
    cual sea la forma de conseguirlo (quitando directorios, con `Path.name`, o
    como sea), y se rompe si el nombre guardado deja de ser el del fichero.
    """
    salida = storage.nombre_seguro("carpeta/subcarpeta/mi video.mp4")
    assert salida.startswith("mi_video_"), f"arrastra las carpetas del cliente: {salida!r}"

    # Un cliente que manda una ruta absoluta tampoco puede decidir el nombre.
    assert storage.nombre_seguro("/home/usuario/videos/promo.mp4").startswith("promo_")

    # Y el nombre original sigue íntegro en la BD para poder mostrárselo al
    # usuario: es `main.crear_lote` quien lo guarda aparte, así que aquí solo
    # se comprueba que `nombre_seguro` no lo necesita para ser único.
    assert len({storage.nombre_seguro("carpeta/mi video.mp4"),
                storage.nombre_seguro("mi video.mp4")}) == 2


def test_nombre_vacio_o_con_bytes_no_imprimibles():
    """`""` y `"\xff\xfe"` dan algo utilizable, no una cadena vacía.

    Un nombre vacío acabaría siendo `_<token>.mp4` o, peor, `.mp4`: en APFS un
    fichero que empieza por punto está oculto, y `_leer_documento` / ffmpeg
    recibirían algo que no es un nombre.
    """
    for nombre in ("", "\xff\xfe", "  ", "....", "\n"):
        salida = storage.nombre_seguro(nombre)
        assert salida, f"cadena vacía para {nombre!r}"
        assert "/" not in salida
        assert not salida.startswith("."), f"oculto para {nombre!r}: {salida!r}"
        # Todo el resultado tiene que ser legal como componente de fichero.
        assert re.fullmatch(r"[A-Za-z0-9._-]+", salida), f"caracteres raros: {salida!r}"
        assert len(os.path.basename(salida).encode("utf-8")) <= 255


#: Nombres largos **ASCII**: es la condición para que lo que los hace caber en
#: 255 bytes sea el recorte del stem, y no el encogimiento de la normalización.
#: Ver el docstring del test de abajo.
NOMBRES_LARGOS = [
    "a" * 300 + ".mp4",
    "b" * 300,
    "c" * 300 + ".webm",
    "d" * 1000,
    # El caso más ajustado: 254 de stem + 4 de extensión = 258, tres por encima
    # del límite. Sin el recorte es el que revienta por poco, y por eso está.
    "e" * 254 + ".mp4",
]


@pytest.mark.parametrize("nombre", NOMBRES_LARGOS)
def test_el_nombre_largo_se_recorta_en_el_stem(nombre):
    """300 caracteres de nombre no reventan el upload con `ENAMETOOLONG`.

    Un componente de fichero se limita a 255 bytes en APFS y en ext4. El recorte
    va en el stem (a 200), nunca en la extensión: si recortara el nombre entero,
    `detectar_tipo` dejaría de reconocer el vídeo.

    Los nombres del `parametrize` son **ASCII a propósito**, y no por casualidad:

    - `re.sub(r"[^A-Za-z0-9._-]", "_", ...)` convierte cada `ñ` en `_`, luego
      `stem.strip("._")` se come los 300 caracteres y salta el `or "archivo"`.
      Con `"ñ" * 300` este test pasaba por el **fallback de stem vacío** y no por
      el recorte, y su nombre afirmaba una cosa que no ocurría (el fallback ya
      lo cubre `test_nombre_vacio_o_con_bytes_no_imprimibles`, con `"...."`).
      Con `"a" * 300` los 300 sobreviven al `re.sub` y al `strip`, así que lo
      único que puede hacer el nombre caber en 255 bytes es el `stem[:200]`.
    - y porque solo con salida ASCII 200 caracteres son 200 bytes, que es la
      invariante que hace el recorte seguro: si eso no se comprobara, un
      `len(...) <= 255` verde no probaría nada, porque la otra forma de que pasara
      es que el nombre encogiera por la normalización en lugar de recortarse.

    Por eso, además de mirar los bytes, se mira **el prefijo legible**: tiene que
    ser exactamente el nombre recortado a 200, y el stem completo tiene que medir
    200 + el separador + el token. Eso es lo que demuestra que el recorte ocurrió
    y no que el nombre se encogió por otro motivo. Y que la extensión sobreviva
    intacta, que es la otra mitad de "el recorte va en el stem, no en el nombre
    entero".
    """
    salida = storage.nombre_seguro(nombre)
    componente = os.path.basename(salida)
    stem, ext = os.path.splitext(salida)
    token = stem.rsplit("_", 1)[-1]

    # El prefijo legible es **exactamente** el recorte: los primeros 200 del
    # nombre, y ni uno más. Si el nombre se encogiera por normalización en lugar
    # de recortarse, esta línea no se cumpliría aunque el nombre encajara.
    assert salida.startswith(nombre[:200] + "_"), (
        f"el prefijo no es el recorte a 200 para {nombre[:20]!r}..."
    )
    # Y el stem que sale es 200 del prefijo + el separador + el token. O sea que
    # el nombre se acortó **por el recorte**, no por otra razón.
    assert len(stem) == 200 + 1 + len(token), (
        f"el stem mide {len(stem)}, no 200 + 1 + {len(token)} para {nombre[:20]!r}..."
    )
    # 200 de prefijo + 1 del separador + 12 del token + la extensión. Los 12 hex
    # del token los fija `test_el_token_es_aleatorio_y_de_12_hex`; aquí se
    # comprueba que el sitio que dejan los deja dentro del límite del fichero.
    assert len(componente.encode("utf-8")) <= 200 + 1 + 12 + len(ext), (
        f"{len(componente.encode('utf-8'))} bytes para {nombre[:20]!r}..."
    )
    # El recorte no puede haber comido la extensión: si el nombre entero se
    # recortara a 200, aquí `.mp4` desaparecería y el fichero dejaría de ser
    # detectable como vídeo.
    assert ext == os.path.splitext(nombre)[1].lower(), (
        f"la extensión no ha sobrevivido al recorte: {ext!r}"
    )
    # El round-trip completo: el nombre recortado se detecta igual que el original.
    assert storage.detectar_tipo(salida) == storage.detectar_tipo(nombre), (
        f"el recorte ha cambiado el tipo del fichero: {nombre[:20]!r} -> {salida[-20:]!r}"
    )


#: Nombres que la normalización tiene que comerse enteros. Incluyen cirílico y
#: CJK a propósito: un nombre así **pierde el prefijo legible completo** y solo
#: queda el token (`archivo_<12 hex>.mp4`), porque el `re.sub` lo convierte todo
#: a `_` y el `strip("._")` se lo come. Es correcto por diseño —para la unicidad
#: da igual, y `archivos.nombre_original` conserva el nombre que mandó el
#: cliente—, y por eso no se testea: lo que importa aquí es que la salida siga
#: siendo ASCII.
NOMBRES_RAROS = [
    "Mi Vídeo ÁÉ (final) — 2ºtake!!.mp4",
    "ñ" * 300 + ".mp4",
    "Привет, как дела.mp4",                    # cirílico
    "動画＿テスト.mp4",  # CJK de ancho completo
    "\U0001F3AC\U0001F4F7 promo.mp4",                       # emoji
    "combining e\u0301ste.mp4",                              # acento combinante suelto
    "ﬁnal take 2.mp4",                                     # ligadura: NFKC la parte en "fi"
    "ｍｐ４ fullwidth.mp4",                                  # fullwidth
    "a" * 300 + ".mp4",
    "  \t\n  ",
]


@pytest.mark.parametrize("nombre", NOMBRES_RAROS)
def test_la_salida_de_nombre_seguro_siempre_es_ascii(nombre):
    """`nombre_seguro` **siempre** devuelve ASCII. Es una invariante, no un detalle.

    De ella depende el recorte: el `stem[:200]` recorta 200 **caracteres**, y
    solo es un recorte de 200 **bytes** mientras la salida sea ASCII. Con salida
    no ASCII, un nombre cirílico o CJK de 200 caracteres mide 400 o 600 bytes y
    el upload revienta con `ENAMETOOLONG` sin que ningún otro test se entere.

    La forma más fácil de romperla sin querer, por orden de probabilidad:

    1. normalización NFKC/NFKD en la `re.sub` ("mejoramos" el nombre y
       "arreglamos" las ligaduras: `ﬁ` pasa a `fi` y el nombre **crece**);
    2. cambiar la clase de caracteres a `\\w` o `str.isalnum()`, que en Python
       incluyen Unicode;
    3. un `unicodedata.normalize` o un `casefold` sueltos por otro lado.

    Por eso el test mira la **salida**, no el código: cualquier camino que
    devuelva algo no ASCII lo rompe, se haya escrito como se haya escrito. Este
    es el test que se enteraría de que el `stem[:200]` ha dejado de ser un
    recorte en bytes.
    """
    salida = storage.nombre_seguro(nombre)
    assert salida.isascii(), (
        f"nombre_seguro no devuelve ASCII: {nombre[:20]!r} -> {salida[:60]!r}. "
        "Con salida no ASCII, el recorte de 200 caracteres deja de ser un "
        "recorte de 200 bytes y los uploads pueden reventar con ENAMETOOLONG."
    )
    # La misma invariante dicha sin `isascii()`, por si alguien lo relaja: en
    # ASCII los caracteres y los bytes son lo mismo, y por eso lo de arriba vale.
    assert len(salida) == len(salida.encode("utf-8"))



def test_no_deja_nada_raro_despues_de_normalizar():
    """Todo lo que sale de la normalización es legal como nombre de fichero."""
    salida = storage.nombre_seguro("Mi Vídeo ÁÉ (final) — 2ºtake!!.mp4")
    assert re.fullmatch(r"[A-Za-z0-9._-]+", salida), salida
    assert salida.endswith(".mp4")


# --- Extremo a extremo: el mismo lote, dos uploads --------------------------
@pytest.mark.asyncio
async def test_dos_archivos_del_mismo_lote_no_se_pisan(db, storage_dir):
    """El paso, tal como está escrito: 2 filas, 2 rutas, contenido intacto.

    `mi video.mp4` y `mi_video.mp4` al mismo lote. Antes de la corrección, ambas
    rutas eran `originales/lote_N/mi_video.mp4` y la segunda escritura machacaba
    la primera: las dos filas apuntaban al mismo fichero con el contenido del
    segundo upload.
    """
    lote = Lote()
    db.add(lote)
    db.commit()
    lote_id = lote.id

    r1, t1 = await storage.guardar_upload(
        lote_id, UploadFile(file=io.BytesIO(b"contenido-A"), filename="mi video.mp4")
    )
    r2, t2 = await storage.guardar_upload(
        lote_id, UploadFile(file=io.BytesIO(b"contenido-B"), filename="mi_video.mp4")
    )

    # 1. Dos rutas distintas.
    assert r1 != r2
    assert os.path.dirname(r1) == os.path.dirname(r2) == storage.dir_originales(lote_id)

    # 2. El contenido de cada una intacto (esto es lo que se perdía).
    with open(r1, "rb") as f:
        assert f.read() == b"contenido-A", "el primer upload fue sobrescrito"
    with open(r2, "rb") as f:
        assert f.read() == b"contenido-B"

    # 3. Dos filas en `archivos`, cada una con su ruta y su nombre original.
    for ruta, nombre_orig, tam in ((r1, "mi video.mp4", t1), (r2, "mi_video.mp4", t2)):
        db.add(
            Archivo(lote_id=lote_id, tipo="video", nombre_original=nombre_orig,
                    ruta=ruta, tamano_bytes=tam)
        )
    db.commit()
    filas = db.scalars(select(Archivo).where(Archivo.lote_id == lote_id).order_by(Archivo.id)).all()
    assert len(filas) == 2
    assert {f.nombre_original for f in filas} == {"mi video.mp4", "mi_video.mp4"}
    assert len({f.ruta for f in filas}) == 2
    assert {os.path.basename(f.ruta) for f in filas} == {os.path.basename(r1), os.path.basename(r2)}
    assert sum(f.tamano_bytes for f in filas) == t1 + t2

    # Y los dos ficheros existen a la vez en disco, con el nombre original
    # intacto en la BD: se conserva lo que el usuario climbed.
    assert sorted(os.listdir(storage_dir / "originales" / f"lote_{lote_id}")) == sorted(
        [os.path.basename(r1), os.path.basename(r2)]
    )


@pytest.mark.asyncio
async def test_dos_uploads_del_mismo_nombre_en_un_lote(db, storage_dir):
    """El otro caso: el mismo nombre dos veces en el mismo lote."""
    lote = Lote()
    db.add(lote)
    db.commit()

    r1, _ = await storage.guardar_upload(
        lote.id, UploadFile(file=io.BytesIO(b"primero"), filename="clip.mp4")
    )
    r2, _ = await storage.guardar_upload(
        lote.id, UploadFile(file=io.BytesIO(b"segundo"), filename="clip.mp4")
    )

    assert r1 != r2
    with open(r1, "rb") as f:
        assert f.read() == b"primero"
    with open(r2, "rb") as f:
        assert f.read() == b"segundo"


@pytest.mark.asyncio
async def test_upload_grande_se_guarda_entero(db, storage_dir):
    """`guardar_upload` escribe por trozos: nada se pierde por ir en trozos.

    Aunque `guardar_upload` no se haya tocado en este paso, el test extremo a
    extremo de arriba solo usaba 11 bytes: si la escritura por trozos estuviera
    rota (o el `MAX_UPLOAD_BYTES` mal calculado), un vídeo real de varios MB se
    guardaría truncado y ningún test se enteraría.
    """
    lote = Lote()
    db.add(lote)
    db.commit()

    contenido = os.urandom(3 * 1024 * 1024)  # 3 MB, varios trozos de 1 MB
    ruta, tam = await storage.guardar_upload(
        lote.id, UploadFile(file=io.BytesIO(contenido), filename="video grande.mp4")
    )
    assert tam == len(contenido)
    with open(ruta, "rb") as f:
        assert f.read() == contenido


def test_ningun_test_escribe_en_storage_real(db):
    """Guardarraíl: `STORAGE_DIR` de los tests nunca es el volumen de producción.

    El fixture `storage_dir` es quien lo garantiza, pero si alguien lo cambiara
    por un `monkeypatch` mal hecho o por un import de `app.config` en el orden
    equivocado, este test lo canta antes de que se escriba nada.
    """
    assert storage.STORAGE_DIR != "/storage", (
        "STORAGE_DIR apunta al volumen real: los tests escribirían fuera del tmp"
    )
    assert os.environ["STORAGE_DIR"] != "/storage"


def test_db_de_test_no_es_la_de_desarrollo():
    """Lo mismo para la base: ni `drop_all` sobre datos de desarrollo."""
    from app.db import engine

    assert engine.url.database == "ingesta_test", (
        f"la sesión de test apunta a {engine.url.database!r}"
    )


def test_sesion_usable(db):
    """Humo: el fixture `db` abre sesión contra el Postgres de test.

    Si el motor, la extensión `vector` o el aislamiento estuvieran rotos, este
    es el primer test que se entera y el resto de fallos serían ruido.
    """
    from sqlalchemy import func

    lote = Lote(estado="procesando")
    db.add(lote)
    db.commit()
    assert db.get(Lote, lote.id).estado == "procesando"

    # El commit llegó a Postgres de verdad: lo ve otra sesión.
    with SessionLocal() as otra:
        assert otra.scalar(select(Lote).where(Lote.id == lote.id)) is not None

    # Y un insert que se deshace con rollback no deja fila.
    db.add(Lote(estado="procesando"))
    db.rollback()
    with SessionLocal() as otra:
        assert otra.scalar(select(func.count()).select_from(Lote)) == 1


# --- `main.crear_lote`: el nombre original también cabe en su columna -------
def test_crear_lote_recorta_el_nombre_original(db, storage_dir, monkeypatch):
    """Un `nombre_original` de 300 caracteres no revienta la subida por la mitad.

    `archivos.nombre_original` es `String(255)` y el nombre lo pone el cliente, o
    sea que el input no es de fiar. Sin el recorte, un nombre largo daba un
    `value too long for type character varying(255)` de Postgres **a mitad del
    bucle de subida**: el lote ya estaba commiteado y en disco, y solo una parte
    de los ficheros. Un 500 sin manejar, con la BD en un estado que nadie pidió.

    Por eso el test mete **dos** ficheros: el largo y uno normal. Que los dos
    acaben en `archivos` es la mitad de lo que se comprueba, porque el bug no era
    "el nombre largo se guarda mal", era "el segundo fichero no se sube nunca".
    El orden importa (largo primero): es el que dispara el fallo en el bucle.

    Se llama a `crear_lote` directamente en vez de con `TestClient` porque en la
    imagen no hay `httpx` (no está en `requirements.txt`): lo que se prueba es la
    función, no el transporte HTTP.
    """
    from app import main as app_main
    from app.config import API_KEY

    # `procesar_lote.send` encolaría en el Redis del stack, donde el worker del
    # `docker compose` **sí** está escuchando: sin esto, este test dispararía un
    # lote de verdad contra la base de desarrollo. Se sustituye por una función
    # que no hace nada.
    encolados = []
    monkeypatch.setattr(app_main.procesar_lote, "send", lambda *a, **kw: encolados.append(a))

    largo = "a" * 300 + ".mp4"
    corto = "charla.mp4"
    respuesta = asyncio.run(app_main.crear_lote(
        files=[
            UploadFile(file=io.BytesIO(b"contenido-largo"), filename=largo),
            UploadFile(file=io.BytesIO(b"contenido-corto"), filename=corto),
        ],
        x_api_key=API_KEY,
    ))

    assert respuesta["estado"] == "pendiente"
    lote_id = respuesta["lote_id"]
    assert isinstance(lote_id, int)

    filas = db.scalars(
        select(Archivo).where(Archivo.lote_id == lote_id).order_by(Archivo.id)
    ).all()
    # 1. Los dos archivos están, no solo el primero: el bucle no se rompió.
    assert len(filas) == 2, f"solo se guardaron {[f.nombre_original for f in filas]}"
    assert [f.tipo for f in filas] == ["video", "video"]

    # 2. El nombre largo se ha recortado a 255, no se ha descartado ni ha reventado.
    assert filas[0].nombre_original == largo[:255]
    assert len(filas[0].nombre_original) == 255
    # 3. El nombre corto queda intacto: el recorte es del largo, no de todos.
    assert filas[1].nombre_original == corto

    # 4. Y los dos ficheros están en disco, con el nombre normalizado de siempre.
    assert sorted(os.listdir(storage_dir / "originales" / f"lote_{lote_id}")) == sorted(
        os.path.basename(f.ruta) for f in filas
    )
    assert encolados == [(lote_id,)], f"el lote no se encoló: {encolados}"
