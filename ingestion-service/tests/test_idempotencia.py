"""Idempotencia de `procesar_lote` y reentrada de `procesar_video`.

Este fichero nace de un comentario que era **falso**. Decía que `procesar_lote` (3)
y `procesar_video` (2) tampoco reintentaban de verdad, por el mismo `except` que
se come la excepción en `procesar_clip`. No era verdad: el `raise` del clip es el
único `raise` de todo `app/tasks.py`, así que los otros dos actores tienen rutas
**sin capturar** (Postgres caído, `procesar_video.send` / `procesar_clip.send` con
Redis caído) que escapan y las reintenta Dramatiq de verdad.

Y esa falsehood escondía bugs reales, ya corregidos en `app/`:

**(a) `procesar_lote` no era idempotente.** Sin guarda de estado, un reintento
volvía a recorrer los archivos y hacía `db.add(v)` otra vez por cada vídeo:
filas `Video` duplicadas y **el mismo archivo cortado dos veces**. Es corrupción
de datos, no trabajo desperdiciado. Ahora hay `if lote.estado != "pendiente":
return`.

**(b) `procesar_video` se colgaba a sí mismo en la reentrada.** Su `except` solo
cubría las llamadas a ffmpeg, así que un fallo de Redis en el bucle
`procesar_clip.send` escapaba y reintentaba. En el reintento encontraba
`v.estado == "cortado"` y hacía `return` seco: **los clips nunca se encolaban**,
se quedaban en `pendiente`, y como `_cerrar_si_termino` los cuenta, el lote se
quedaba en `procesando` para siempre. Ahora la reentrada reapunta los clips que
sigan en `pendiente` y vuelve a comprobar el cierre.

Los tests de aquí muerden: si se quita la guarda de `procesar_lote`, o se
sustituye el cuerpo de la reentrada por el `return` seco de antes, fallan. Está
comprobado con mutation testing (ver el informe de la sesión).

Y los otros dos pelearon con lo mismo desde el lado de los **`None`**: los tres
actores hacen `db.get(Model, id)` sobre un id que llega de la cola, y la cola no
garantiza que la fila siga ahí. Sin comprobarlo, el `lote.estado` / `lote_id` de
la línea siguiente revienta con `AttributeError`; como ninguno de esos tres
atributos está dentro de un `try/except`, Dramatiq se come los reintentos enteros
(id 3, vídeo 2) y tira el mensaje a la carta muerta sin haber hecho nada. Tres
vueltas por un id fantasma que no se puede recuperar.

**Por qué `.fn` y no la cola:** igual que en el resto de la suite, lo que se
quiere probar es el control de flujo y el estado en BD, no Redis ni el worker.
`.send` se parchea con `monkeypatch` para que el encolado sea observable sin
tocar Redis.
"""

import logging
from pathlib import Path

import pytest
from sqlalchemy import select

from app import ffmpeg_utils
from app.db import SessionLocal
from app.models import Archivo, Clip, Lote, Video
from app.tasks import procesar_clip, procesar_lote, procesar_video


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _espiar_envios(monkeypatch, *actores) -> list[tuple]:
    """Sustituye el `.send` de los actores dados por una lista de `args`.

    Lo que se comprueba es "esto se encoló", no "esto se ejecutó": `.send`
    tocaría Redis de verdad y esta suite no levanta worker.
    """
    enviados: list[tuple] = []

    for actor in actores:
        def _send(*args, **kwargs):
            enviados.append(args)

        monkeypatch.setattr(actor, "send", _send)
    return enviados


def _video_falso(tmp_path: Path, nombre: str) -> str:
    """Un `.mp4` que existe pero que nadie va a cortar.

    En estos tests de idempotencia no se corta nada: `procesar_video` solo se usa
    por su reentrada, que no llega a ffmpeg. El fichero hace falta porque la
    columna `archivos.ruta` es NOT NULL y para que el lote sea realista.
    """
    ruta = tmp_path / nombre
    ruta.write_bytes(b"no es un mp4, pero existe y tiene ruta")
    return str(ruta)


def _crear_lote_con_archivos(db, archivos: list[tuple[str, str, str]]) -> Lote:
    """Lote en `pendiente` con los `(tipo, nombre_original, ruta)` que se le pasen."""
    lote = Lote(estado="pendiente")
    db.add(lote)
    db.flush()
    for tipo, nombre, ruta in archivos:
        db.add(Archivo(lote_id=lote.id, tipo=tipo, nombre_original=nombre,
                       ruta=ruta, tamano_bytes=10))
    db.commit()
    return lote


def _lote(lote_id: int) -> Lote:
    """El lote leído en una sesión **nueva** (`expire_on_commit=False` engaña)."""
    with SessionLocal() as db:
        return db.get(Lote, lote_id)


def _videos(lote_id: int) -> list[Video]:
    with SessionLocal() as db:
        return list(db.scalars(select(Video).where(Video.lote_id == lote_id)))


def _clips(video_id: int) -> list[Clip]:
    with SessionLocal() as db:
        return list(db.scalars(select(Clip).where(Clip.video_id == video_id)
                               .order_by(Clip.indice)))


def _estado_lote(lote_id: int) -> str:
    with SessionLocal() as db:
        return db.get(Lote, lote_id).estado


def _ids_clips(video_id: int) -> list[int]:
    with SessionLocal() as db:
        return list(db.scalars(select(Clip.id).where(Clip.video_id == video_id)
                               .order_by(Clip.indice)))


def _montar_cortado(db, *, estados_clips: list[str], estado_lote: str = "procesando") -> tuple[int, int, list[int]]:
    """Lote → archivo → vídeo **ya cortado** → clips en los estados que se pidan.

    Reproduce exactamente el estado que deja `procesar_video` después de cortar
    (vídeo en `cortado`, un `Clip` por fichero producido) y que es desde donde
    arranca la reentrada: es el estado en el que se colgaba el lote.
    """
    lote = Lote(estado=estado_lote)
    db.add(lote)
    db.flush()

    archivo = Archivo(lote_id=lote.id, tipo="video", nombre_original="charla.mp4",
                      ruta="/tmp/charla.mp4", tamano_bytes=1024)
    db.add(archivo)
    db.flush()

    video = Video(lote_id=lote.id, archivo_id=archivo.id, estado="cortado",
                  duracion_seg=4.0, total_clips=len(estados_clips))
    db.add(video)
    db.flush()

    clips = [
        Clip(video_id=video.id, indice=i, ruta=f"/tmp/clip_{i}.mp4", estado=estado)
        for i, estado in enumerate(estados_clips)
    ]
    db.add_all(clips)
    db.commit()
    return lote.id, video.id, [c.id for c in clips]


# ==========================================================================
# (a) `procesar_lote`: la guarda de idempotencia
# ==========================================================================
def test_procesar_lote_dos_veces_no_duplica_los_videos(db, tmp_path, monkeypatch):
    """Llamado dos veces sobre el mismo lote: un `Video` por archivo, y nada más.

    Este es el bug (a). Sin la guarda, el segundo intento vuelve a recorrer los
    `archivos` de tipo `video` y hace `db.add(v)` otra vez: dos filas `Video` por
    archivo y, con ellas, **el mismo archivo cortado dos veces**.

    Se comprueban las tres consecuencias, porque cada una se rompe por su cuenta:

    1. el número de filas `Video` sigue siendo el de archivos de vídeo;
    2. `procesar_video` **no** se vuelve a encolar (el segundo intento no
       relanza el encolado);
    3. el `contexto_texto` no se reescribe (la guarda corta *antes* de releer
       los documentos: si alguien la moviera más abajo, este assert lo cantaría).

    El punto 3 se comprueba cambiando el fichero del documento entre las dos
    llamadas: si se relejera, el contexto guardado sería el nuevo.
    """
    ruta_doc = tmp_path / "notas.txt"
    ruta_doc.write_text("el guion original", encoding="utf-8")
    videos = [_video_falso(tmp_path, f"v{i}.mp4") for i in range(3)]
    lote = _crear_lote_con_archivos(db, [
        ("doc", "notas.txt", str(ruta_doc)),
        *[("video", f"v{i}.mp4", ruta) for i, ruta in enumerate(videos)],
    ])
    enviados = _espiar_envios(monkeypatch, procesar_video)

    # Primer intento: el trabajo de verdad.
    procesar_lote.fn(lote.id)

    assert len(_videos(lote.id)) == 3, "el primer intento no creó los vídeos"
    assert len(enviados) == 3, f"el primer intento no encoló los vídeos: {enviados}"
    assert _lote(lote.id).contexto_texto == "el guion original"

    # Cambiar el documento solo se ve si el segundo intento lo relee.
    ruta_doc.write_text("el guion reescrito", encoding="utf-8")

    # Segundo intento, con el lote ya en 'procesando': no debe hacer nada.
    procesar_lote.fn(lote.id)

    assert len(_videos(lote.id)) == 3, (
        f"la segunda pasada ha duplicado vídeos: "
        f"{[(v.id, v.archivo_id) for v in _videos(lote.id)]}"
    )
    assert len(enviados) == 3, (
        f"la segunda pasada ha vuelto a encolar los vídeos: {enviados}"
    )
    assert _lote(lote.id).contexto_texto == "el guion original", (
        "la segunda pasada ha releído los documentos: la guarda no corta antes"
    )
    # Y el lote sigue en curso, no cerrado: los vídeos aún no se han cortado.
    assert _estado_lote(lote.id) == "procesando"


@pytest.mark.parametrize("estado", ["procesando", "listo", "error"])
def test_procesar_lote_sobre_un_lote_ya_advanced_no_hace_nada(db, tmp_path, monkeypatch, estado):
    """Un lote que ya no está `pendiente` no se toca, sea cual sea su estado.

    `procesando` es el caso natural y el que más importa: es el estado en el que
    el lote queda **después** del primer intento, así que es exactamente lo que
    ve un reintento de Dramatiq. `listo` y `error` son los otros dos finales
    posibles, y también deben ser no-ops: reencolar un lote ya terminado
    significaría volver a cortar archivos de vídeos que ya se publicaron.

    El lote se monta con documentos legibles para que el assert de
    `contexto_texto` tenga valor: si la tarea leyera algo, pondría el texto.
    """
    ruta_doc = tmp_path / "notas.txt"
    ruta_doc.write_text("el guion", encoding="utf-8")
    lote = _crear_lote_con_archivos(db, [
        ("doc", "notas.txt", str(ruta_doc)),
        ("video", "charla.mp4", _video_falso(tmp_path, "charla.mp4")),
    ])
    lote.estado = estado
    db.commit()

    enviados = _espiar_envios(monkeypatch, procesar_video)
    with pytest.MonkeyPatch.context():
        procesar_lote.fn(lote.id)

    assert _estado_lote(lote.id) == estado, "la tarea ha cambiado el estado del lote"
    assert _videos(lote.id) == [], f"ha creado filas Video en un lote {estado}: {_videos(lote.id)}"
    assert enviados == [], f"ha encolado vídeos en un lote {estado}: {enviados}"
    assert _lote(lote.id).contexto_texto is None, (
        f"ha releído los documentos de un lote {estado}"
    )


# ==========================================================================
# (b) `procesar_video`: la reentrada
# ==========================================================================
def test_reentrada_reapunta_los_clips_pendientes(db, monkeypatch):
    """Vídeo ya cortado + clips en `pendiente`: se reapuntan **solo** esos.

    El bug (b) era un `return` seco: los clips nunca llegaban a la cola y el
    lote se quedaba en `procesando` para siempre. Aquí se exige lo contrario, y
    además que el trabajo sea el justo:

    - se encolan los ids de los clips `pendiente`, y en el orden de `indice`;
    - **no** se reenvían los que ya están `listo` o en `error`, que reprocesarlos
      sería trabajo desperdiciado (y en el caso de un clip `listo`, además,
      sobreescribir un resultado ya bueno);
    - no se vuelve a cortar (abajo, en `test_reentrada_no_vuelve_a_cortar`).
    """
    _lote_id, video_id, clip_ids = _montar_cortado(db, estados_clips=["listo", "pendiente", "pendiente"])
    enviados = _espiar_envios(monkeypatch, procesar_clip)

    procesar_video.fn(video_id)

    assert enviados == [(clip_ids[1],), (clip_ids[2],)], (
        f"la reentrada no ha reapuntado exactamente los clips pendientes: {enviados}"
    )


def test_reentrada_sin_clips_pendientes_no_encola_nada(db, monkeypatch):
    """Vídeo cortado y todos los clips ya resueltos: no se encola absolutamente nada.

    La contraprueba del anterior, y la que impide "arreglar" el bug (b)
    reenviando clips a lo bruto: aquí los tres clips están `listo` y el vídeo
    también, o sea que no queda nada que hacer.

    Se comprueba también que la reentrada **sí** vuelve a mirar el cierre: el
    lote estaba en `procesando` y con esta llamada se cierra como `listo`, que es
    justo el cierre que el `return` seco se comía.
    """
    lote_id, video_id, clip_ids = _montar_cortado(db, estados_clips=["listo", "listo"])
    enviados = _espiar_envios(monkeypatch, procesar_clip)

    procesar_video.fn(video_id)

    assert enviados == [], f"ha reencolado clips que ya estaban resueltos: {enviados}"
    assert _ids_clips(video_id) == clip_ids, "la reentrada ha tocado los clips"
    assert _estado_lote(lote_id) == "listo", (
        "la reentrada no ha vuelto a comprobar el cierre del lote"
    )


def test_reentrada_no_vuelve_a_cortar(db, tmp_path, monkeypatch):
    """La reentrada no repite el corte: ni ffmpeg ni cambio de filas.

    La mitad "el corte no se repite" de la idempotencia. Sin esto, un reintento
    podría reejecutar `cortar_y_normalizar` y dejar el vídeo con la fila de
    clips recreada (el `delete()` de clips previos borra los resultados), que
    es pérdida de trabajo ya hecho.

    Se parchea `ffmpeg_utils.cortar_y_normalizar` para que **reviente** si se
    llama: así el fallo no es un assert sobre un efecto colateral, es
    directamente "ha intentado cortar".
    """
    _lote_id, video_id, clip_ids = _montar_cortado(db, estados_clips=["pendiente", "pendiente"])

    def _no_cortar(*args, **kwargs):
        raise AssertionError("la reentrada de un vídeo cortado no puede volver a cortar")

    monkeypatch.setattr(ffmpeg_utils, "cortar_y_normalizar", _no_cortar)
    _espiar_envios(monkeypatch, procesar_clip)

    with SessionLocal() as db2:
        clips_antes = {c.id: (c.indice, c.ruta) for c in db2.scalars(
            select(Clip).where(Clip.video_id == video_id))}

    procesar_video.fn(video_id)

    with SessionLocal() as db2:
        clips_despues = {c.id: (c.indice, c.ruta) for c in db2.scalars(
            select(Clip).where(Clip.video_id == video_id))}
        video = db2.get(Video, video_id)

    assert clips_despues == clips_antes, (
        f"la reentrada ha reescrito los clips: antes {clips_antes}, después {clips_despues}"
    )
    assert video.estado == "cortado", f"el vídeo ha cambiado de estado a {video.estado!r}"
    assert video.duracion_seg == 4.0 and video.total_clips == 2, (
        "la reentrada ha reescrito la duración o el recuento de clips del vídeo"
    )


def test_el_lote_colgado_se_desatasca_al_reintentar_el_video(db, monkeypatch):
    """El bug (b) de punta a punta: clips pendientes + lote en `procesando` → listo.

    Este es el test que fija el bug tal y como se vio en producción. El estado
    inicial es el que dejaba el `return` seco: vídeo `cortado`, clips en
    `pendiente` porque nadie los encoló, y el lote clavado en `procesando` porque
    `_cerrar_si_termino` cuenta los clips `pendiente`.

    Aquí el worker está simulado: `procesar_clip.send` ejecuta el actor de verdad
    (`procesar_clip.fn`), con `ffmpeg_utils.duracion` parcheada para no depender
    de un fichero real ni de ffmpeg — lo que se prueba es el encolado y el
    cierre, que ya tienen sus propios tests. Con el `return` seco de antes, los
    clips nunca se ejecutarían y el lote se quedaría en `procesando`: eso es lo
    que este test afirma que ya no pasa.
    """
    lote_id, video_id, clip_ids = _montar_cortado(db, estados_clips=["pendiente", "pendiente"])
    monkeypatch.setattr(ffmpeg_utils, "duracion", lambda ruta: 2.0)

    _procesados: list[int] = []

    def _worker(*args, **kwargs):
        _procesados.append(args[0])
        procesar_clip.fn(args[0])

    monkeypatch.setattr(procesar_clip, "send", _worker)

    assert _estado_lote(lote_id) == "procesando", "el punto de partida no reproduce el cuelgue"
    assert [c.estado for c in _clips(video_id)] == ["pendiente", "pendiente"]

    procesar_video.fn(video_id)

    assert _procesados == clip_ids, f"no se han encolado los clips pendientes: {_procesados}"
    assert [c.estado for c in _clips(video_id)] == ["listo", "listo"]
    assert _estado_lote(lote_id) == "listo", (
        "el lote sigue en 'procesando': los clips se encolaron pero no se comprobó el cierre"
    )


def test_la_reentrada_avisa_en_el_log(db, monkeypatch, caplog):
    """La reentrada deja rastro en el log, como todo lo demás que no va a ser normal.

    Sin esto, un reintento es invisible: se ve que el lote tarda, pero no que
    un vídeo cortado se ha reapuntado. Con `max_retries=2` en `procesar_video`,
    la única forma de que el operador sepa qué pasó es el log.
    """
    _lote_id, video_id, clip_ids = _montar_cortado(db, estados_clips=["pendiente", "listo"])
    _espiar_envios(monkeypatch, procesar_clip)

    with caplog.at_level(logging.INFO, logger="app.tasks"):
        procesar_video.fn(video_id)

    mensajes = [r.getMessage() for r in caplog.records if r.name == "app.tasks"]
    del_reentrada = [
        m for m in mensajes
        if f"vídeo {video_id}" in m and str(clip_ids[0]) in m
    ]
    assert del_reentrada, f"la reentrada no ha dicho nada: {mensajes}"


# ==========================================================================
# (c) `db.get(...)` sobre un id que ya no está: los tres actores
# ==========================================================================
def _aviso_de_id_inexistente(caplog, actor: str, id_: int) -> str:
    """El aviso de `app.tasks` sobre `actor id_`, comprobado en la forma del Paso 0.4.

    Devuelve el mensaje para que el test pueda seguir usando `caplog`. Falla con
    el listado completo de lo que sí se ha logueado, que es la primera pregunta
    que se hace uno cuando un aviso no aparece.

    Se exige el formato de los tres actores a la vez —actor, id y motivo— porque
    es lo que hace falta para diagnosticar un reintento sin mirar la BD: un
    aviso que no cita el id no dice a qué trabajo pertenece, y uno que no dice
    por qué se saltó deja al operador adivinando.
    """
    registros = [r for r in caplog.records if r.name == "app.tasks"]
    mensajes = [r.getMessage() for r in registros]
    citados = [r for r in registros if f"{actor} {id_}" in r.getMessage()]
    assert citados, (
        f"nada dice qué pasó con {actor} {id_}: {mensajes}"
    )
    aviso = citados[0]
    # Y el motivo, que es la otra mitad del formato.
    assert "no existe" in aviso.getMessage(), (
        f"el aviso de {actor} {id_} no dice por qué se salta: {aviso.getMessage()!r}"
    )
    # A `WARNING` y no a `ERROR`: un id que ya no está es trabajo perdido, no un
    # fallo del sistema. Si esto fuese un error, el operador acabaría persiguiendo
    # un fallo que no ha pasado (y ya lo persiguió `procesar_clip` con su
    # `log.exception` de un clip ilegible, que sí lo merecía).
    assert aviso.levelno == logging.WARNING, (
        f"un id inexistente se ha logueado como {aviso.levelname}, no como WARNING: "
        f"{aviso.getMessage()!r}"
    )
    return aviso.getMessage()


def test_procesar_lote_con_un_lote_inexistente_deberia_salir_limpio(db, monkeypatch, caplog):
    """Un `lote_id` que no existe no tumba la tarea: sale limpio y avisa.

    El `db.get(Lote, lote_id)` devolvía `None` y el `lote.estado` de la línea
    siguiente reventaba con `AttributeError`. Como el único `except` de
    `procesar_lote` es el de los documentos, eso no lo tragaba nada: Dramatiq
    (`max_retries=3`) reintentaba tres veces un lote que no iba a aparecer nunca
    y lo mandaba a la carta muerta. Tres vueltas por un id fantasma que no se
    puede recuperar y ningún trabajo hecho.

    Lo que se exige, en el orden en que se rompe:

    1. **que no reviente**: sin `pytest.raises`, que la llamada levantee ya es un
       fallo. Es el `AttributeError` de antes, y es la aserción que más muerde.
    2. **que no encole nada**: no hay lote, así que no hay vídeos que lanzar. Sin
       este punto, "sale limpio" podría querer decir "sale limpio después de
       mandar un vídeo sin lote a la cola".
    3. **que avise con el formato del Paso 0.4**: actor, id y motivo, a `WARNING`
       (lo comprueba `_aviso_de_id_inexistente`). Sin el aviso, el reintento se
       consumiría en silencio y nadie sabría nunca que ese lote se perdió; y sin
       citar el id, el `tail -f` del worker no dice a qué trabajo pertenece la
       línea.
    """
    enviados = _espiar_envios(monkeypatch, procesar_video)

    with caplog.at_level(logging.INFO, logger="app.tasks"):
        procesar_lote.fn(999_999)

    assert enviados == [], f"un lote inexistente no puede encolar nada: {enviados}"
    _aviso_de_id_inexistente(caplog, "lote", 999_999)


def test_procesar_video_con_un_video_id_inexistente_deberia_salir_limpio(db, monkeypatch, caplog):
    """Un `video_id` que no existe no tumba la tarea: sale limpio y avisa.

    El gemelo exacto del de arriba, y el mismo bug por el mismo motivo: el
    `db.get(Video, video_id)` devolvía `None` y el `lote_id = v.lote_id` de la
    línea siguiente reventaba con `AttributeError`. El `except` de `procesar_video`
    solo cubre las llamadas a ffmpeg, así que la excepción escapaba, Dramatiq
    (`max_retries=2`) repetía el corte fallido y el mensaje iba a la carta muerta
    sin haber tocado ffmpeg ni la BD.

    Aquí la diferencia con el lote es que `procesar_video` es el que **manda
    clips a la cola**, así que el punto 2 pesa más: un vídeo fantasma no puede
    dejar clips encolados apuntando a una fila que no existe, porque ese mensaje
    lo consumiría un worker y `procesar_clip` a su vez se saldría por su propia
    guarda de `None` (testeada en `test_cercar_lote.py`). Es decir: sería
    inocuo, pero ensuciaría el log del worker con dos avisos falsos por cada
    reintento, que es justo lo que esta pareja de guards evita.

    Y se monta además un lote **real** con un vídeo cortado y clips pendientes,
    para que los asserts finales valgan: un id fantasma no puede cerrar el lote
    ni tocar la fila de otro vídeo. Igual que en
    `test_clip_inexistente_no_revienta_la_tarea`.
    """
    lote_id, video_id_real, _clip_ids = _montar_cortado(
        db, estados_clips=["pendiente", "pendiente"])
    enviados = _espiar_envios(monkeypatch, procesar_clip)

    with caplog.at_level(logging.INFO, logger="app.tasks"):
        procesar_video.fn(999_999)

    # 1. No ha reventado (si hubiera reventado, el test ya habría fallen antes).
    # 2. No ha encolado ningún clip.
    assert enviados == [], f"un vídeo inexistente no puede encolar clips: {enviados}"

    # Y el lote real que hay en la BD no se ha enterado de nada: sigue en curso
    # (con clips `pendiente` no puede cerrarse) y su vídeo sigue como estaba.
    assert _estado_lote(lote_id) == "procesando", (
        "un vídeo inexistente no debe poder cerrar un lote de verdad"
    )
    with SessionLocal() as db2:
        assert db2.get(Video, video_id_real).estado == "cortado"
    assert [c.estado for c in _clips(video_id_real)] == ["pendiente", "pendiente"], (
        "un vídeo inexistente no puede tocar los clips de otro vídeo"
    )

    # 3. El aviso, con el formato de los tres actores.
    _aviso_de_id_inexistente(caplog, "vídeo", 999_999)
