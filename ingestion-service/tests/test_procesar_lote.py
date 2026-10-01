"""Un documento ilegible no puede dejar el lote colgado (`procesar_lote`).

El mismo bug de cuelgue del Paso 0.6, un nivel más arriba. `procesar_lote`
recorre los `archivos` de tipo `doc` y llama a `_leer_documento(ruta)`, que para
un `.docx` hace `Document(ruta)`. Con un `.docx` corrupto eso **lanza**:

1. la excepción escapaba de `procesar_lote` sin que nada la recogiera;
2. Dramatiq agotaba los reintentos y la tarea moría en la carta muerta;
3. como el `with SessionLocal()` de `procesar_lote` se salía **sin commit**, el
   `lote.estado = "procesando"` tampoco se guardaba: el lote se quedaba en
   `pendiente` **para siempre**, sin error, sin log y sin nada que lo explicara;
4. y los vídeos del lote —que son el trabajo de verdad— no se procesaban nunca,
   porque `procesar_video.send()` está al final de la función.

La decisión actual: el contexto textual es **auxiliar**, así que un documento
ilegible no tumba el lote. `_leer_documento` va envuelto en un `try/except` por
documento que loguea y sigue sin ese documento. Lo que estos tests comprueban es
que ese "sigue" sea de verdad: que el lote avance, que el vídeo se encole igual
y que quede un aviso que diga **qué** documento se perdió.

**Por qué las rutas son ficheros de `tmp_path` y no de `STORAGE_DIR`:** a
`procesar_lote` solo le llega la cadena de `archivos.ruta` y se la pasa tal cual
a `open()` / `Document()`. La disposición de carpetas no forma parte de lo que se
prueba aquí, así que se evita el fixture de storage por un detalle.
"""

import logging
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Archivo, Lote, Video
from app.tasks import procesar_lote, procesar_video


def _docx_valido(ruta: Path, texto: str = "primer parrafo\nsegundo parrafo") -> str:
    """Un `.docx` de verdad, con el texto que luego se leerá.

    Se genera con `python-docx` (dependencia de producción, no de test) en vez de
    con un zip a mano: lo que se quiere comprobar es que el camino bueno de
    `_leer_documento` sigue leyendo el texto, no construir un OOXML válido.
    """
    from docx import Document

    documento = Document()
    for parrafo in texto.split("\n"):
        documento.add_paragraph(parrafo)
    documento.save(str(ruta))
    return str(ruta)


def _docx_corrupto(ruta: Path) -> str:
    """Un fichero que existe, con extensión `.docx`, y que no es un paquete OOXML.

    Es el caso real (subida a medias, fichero truncado, HTML guardado con
    extensión `.docx`) y da un `PackageNotFoundError` de `python-docx`, no un
    error de la escritura en disco.
    """
    ruta.write_bytes(b"esto no es un docx, es un texto\n" * 40)
    return str(ruta)


def _espiar_envios(monkeypatch, *actores) -> list[dict]:
    """Sustituye el `.send` de los actores dados por una lista, y devuelve esa lista.

    Hace falta porque lo que se comprueba es "el vídeo se encola", no "el vídeo se
    corta": `.send` tocaría Redis de verdad y ningún worker (esta suite no
    levanta ninguno) consumiría el mensaje. Parchear `.send` en el actor —y no
    `procesar_video.fn`— es lo que hace equivalente el resultado a lo que hace el
    worker, en vez de saltarse un paso del pipeline.
    """
    enviados: list[dict] = []

    for actor in actores:
        def _send(*args, _actor=actor, **kwargs):
            enviados.append({"actor": _actor, "args": args, "kwargs": kwargs})

        monkeypatch.setattr(actor, "send", _send)
    return enviados


def _crear_lote(db, archivos: list[tuple[str, str, str]]) -> Lote:
    """Crea el lote y sus `archivos` y lo deja commiteado.

    `archivos` es una lista de `(tipo, nombre_original, ruta)`, que es
    literalmente lo que `main.crear_lote` deja escrito tras subir un lote.
    """
    lote = Lote(estado="pendiente")
    db.add(lote)
    db.flush()
    for tipo, nombre, ruta in archivos:
        db.add(Archivo(lote_id=lote.id, tipo=tipo, nombre_original=nombre,
                       ruta=ruta, tamano_bytes=10))
    db.commit()
    return lote


def _lote_fresco(lote_id: int) -> Lote:
    """El lote leído en una sesión **nueva**, ya cerrada.

    Imprescindible leerlo en otra sesión: `SessionLocal` usa
    `expire_on_commit=False`, así que la sesión del test seguiría viendo el
    objeto viejo y los asserts pasarían sin ver lo que hizo la tarea. Lo que
    devuelve está *desacoplado* pero con todos los atributos cargados (`db.get`
    los lee enteros y aquí no hay ningún commit), así que se pueden leer campos sueltos.
    """
    with SessionLocal() as db:
        return db.get(Lote, lote_id)


def _videos_del_lote(lote_id: int) -> list[Video]:
    """Los `Video` del lote, leídos en una sesión nueva (mismo criterio que arriba)."""
    with SessionLocal() as db:
        return list(db.scalars(select(Video).where(Video.lote_id == lote_id)))


def test_un_docx_corrupto_no_tumba_el_lote(db, tmp_path, monkeypatch, caplog):
    """Un vídeo válido + un `.docx` corrupto: el vídeo manda y el lote no se cuelga.

    Las tres cosas que importan, en el orden en que se rompen:

    1. **El lote sale de `pendiente`.** Antes la excepción de `Document(ruta)`
       salía de `procesar_lote` antes del commit, y el `lote.estado =
       "procesando"` se iba con ella: el lote se quedaba en `pendiente` para
       siempre, sin error ni log. Aquí tiene que haber avanzado a `procesando`:
       no se cierra todavía porque el vídeo sigue pendiente, y está bien que sea
       así (un lote con el vídeo todavía en curso no puede darse por cerrado).
    2. **El vídeo se procesa igual.** Se crea su fila `Video` y se llama a
       `procesar_video` con ese id, o sea que el trabajo de verdad no se pierde
       por un `.docx` roto.
    3. **Queda un aviso que menciona el documento que falló**, con la ruta
       concreta: sin eso, un lote con el contexto a medias es indistinguible de
       uno al que no le hubieran subido documento ninguno.

    El aviso sale por `log.exception`, o sea a nivel `ERROR` (no `WARNING`), así
    que se comprueba con `>= WARNING` para no atar el test a esa elección.
    """
    video = tmp_path / "charla.mp4"
    video.write_bytes(b"un mp4 de mentira, aqui no se corta nada")
    docx_malo = _docx_corrupto(tmp_path / "notas_corruptas.docx")
    lote = _crear_lote(db, [
        ("doc", "notas_corruptas.docx", docx_malo),
        ("video", "charla.mp4", str(video)),
    ])
    archivo_video = db.scalars(
        select(Archivo).where(Archivo.lote_id == lote.id, Archivo.tipo == "video")
    ).one()
    enviados = _espiar_envios(monkeypatch, procesar_video)

    with caplog.at_level(logging.INFO, logger="app.tasks"):
        procesar_lote.fn(lote.id)

    # 1. El lote no se queda en `pendiente`.
    assert _lote_fresco(lote.id).estado == "procesando", (
        "el lote se ha quedado en 'pendiente': el documento roto lo ha tumbado"
    )

    # 2. El vídeo se procesa igual: fila creada y `procesar_video` encolado con su id.
    videos = _videos_del_lote(lote.id)
    assert [v.archivo_id for v in videos] == [archivo_video.id], (
        f"no se ha creado el Video del archivo de vídeo: {videos}"
    )
    assert [v.estado for v in videos] == ["pendiente"]
    assert [e["args"] for e in enviados] == [(videos[0].id,)], f"el vídeo no se encoló: {enviados}"
    assert all(e["actor"] is procesar_video for e in enviados)

    # 3. El aviso menciona el documento que falló.
    del_docx = [
        r for r in caplog.records
        if r.name == "app.tasks" and docx_malo in r.getMessage()
    ]
    assert del_docx, (
        "el aviso no dice qué documento se perdió: "
        f"{[r.getMessage() for r in caplog.records]}"
    )
    # Y es un aviso de verdad, no una línea de INFO: el lote se quedó sin
    # contexto y eso tiene que verse como problema, no como una nota.
    assert del_docx[0].levelno >= logging.WARNING, (
        f"el documento perdido se ha logueado como {del_docx[0].levelname}"
    )
    # El motivo viene en el `log.exception`: sin traceback, el aviso no explica
    # nada y el operador tiene que ir a reproducirlo a mano.
    assert del_docx[0].exc_info is not None, "el aviso no lleva el motivo del fallo"
    # Y se descarta el motivo equivocado: si `python-docx` no estuviera, el
    # `try/except` también lo tragaría y el test pasaría sin haber probado nada.
    assert not issubclass(del_docx[0].exc_info[0], ImportError), (
        f"el fallo es {del_docx[0].exc_info[0].__name__}, no un .docx corrupto: "
        "python-docx no está instalado y el test no probaría nada"
    )

    # Sin documentos legibles, `contexto_texto` es `None` y no una cadena vacía:
    # el "sigue sin él" tiene que notarse en la BD, no solo en el log.
    assert _lote_fresco(lote.id).contexto_texto is None


def test_un_documento_ilegible_no_arrastra_a_los_otros(db, tmp_path, monkeypatch, caplog):
    """Un `.docx` roto no tira el texto de los que sí se pudieron leer.

    Es la otra mitad de la decisión ("loguea un aviso y **sigue sin ese
    documento**"): si el `except` se tragara el error pero el texto se perdiera
    igualmente, el lote avanzaría sin contexto y nadie se enteraría hasta que el
    LLM del paso siguiente produjera garbage con la mitad de las notas.

    También cubre que el aviso señala al culpable y no a la víctima: con dos
    documentos en el mismo lote, un `log.exception` con la ruta equivocada (la
    última del bucle, o la de otro) se vería aquí.
    """
    bueno = _docx_valido(tmp_path / "notas.docx", "el guion de la charla")
    malo = _docx_corrupto(tmp_path / "notas_2.docx")
    video = tmp_path / "charla.mp4"
    video.write_bytes(b"otro mp4 de mentira")
    lote = _crear_lote(db, [
        ("doc", "notas.docx", bueno),
        ("doc", "notas_2.docx", malo),
        ("video", "charla.mp4", str(video)),
    ])
    enviados = _espiar_envios(monkeypatch, procesar_video)

    with caplog.at_level(logging.INFO, logger="app.tasks"):
        procesar_lote.fn(lote.id)

    contexto = _lote_fresco(lote.id).contexto_texto
    assert contexto == "el guion de la charla", (
        f"el texto del documento legible se ha perdido o mezclado: {contexto!r}"
    )
    # El aviso señala al roto, y solo a él.
    avisos = [r for r in caplog.records if r.name == "app.tasks" and r.levelno >= logging.WARNING]
    assert [r.getMessage() for r in avisos if malo in r.getMessage()], (
        f"ningún aviso menciona el .docx roto: {[r.getMessage() for r in avisos]}"
    )
    assert not [r for r in avisos if bueno in r.getMessage()], (
        f"el aviso señala el documento que sí se leyó: {[r.getMessage() for r in avisos]}"
    )
    # Y el lote sigue adelante: el vídeo se encola y el lote queda en curso.
    assert _lote_fresco(lote.id).estado == "procesando"
    assert len(enviados) == 1, f"el vídeo no se encoló: {enviados}"
