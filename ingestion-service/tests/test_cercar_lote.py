"""Paso 0.6 — `procesar_clip` no puede dejar el lote colgado.

El bug de fondo, y por qué era silencioso:

`ffmpeg_utils.duracion()` llama a `ffprobe` con `check=True`, o sea que **lanza
excepción** si el clip no es legible. Al principio esa llamada estaba **fuera**
de cualquier try/except en `procesar_clip`, así que:

1. el `RuntimeError`/`CalledProcessError` salía de la tarea;
2. Dramatiq lo reintentaba hasta agotar `max_retries=3`;
3. la tarea moría en la carta muerta **sin llamar a `_cerrar_si_termino`**;
4. el clip se quedaba en `pendiente` para siempre, y como `_cerrar_si_termino`
   cuenta los clips `pendiente`, el lote se quedaba en `procesando` para
   siempre, sin error visible en `/lotes/{id}` y sin una línea de log que lo
   explicara.

Ojo con el paso 2: **nunca llegó a pasar**. La primera corrección (el
try/except/finally sin relanzar) arreglaba el cuelgue pero **se comía la
excepción**, así que Dramatiq no reintentaba nada y el `max_retries=3` era
decorativo: el plan daba por hecho unos reintentos que no existían. La decisión
actual es explícita y va en la dirección contraria: **`max_retries=0` y
`raise`**. El trabajo es medir la duración de un fichero que acabamos de
escribir nosotros, así que si `ffprobe` no lo lee no lo va a leer nunca más, y
reintesar solo retrasa el aviso. El `raise` manda el mensaje a la carta muerta,
que es donde queda el rastro.

**Consecuencia directa para estos tests:** `procesar_clip.fn(clip_id)` sobre un
clip corrupto **lanza excepción**. Por eso todo test con un clip ilegible va
envuelto en `_reventa_por_el_clip(...)` y comprueba **las dos** cosas:

- que el estado en BD es el correcto (clip `error`, lote `error`);
- que la excepción **sale** de la tarea.

Las dos importan y antes solo se comprobaba una: con el `except` tragándose la
excepción, el estado en BD seguía siendo perfecto y todos estos tests pasaban
mientras el bug estaba vivo. Si alguien vuelve a tragársela, `_reventa_por_el_clip`
falla, y ese ha sido el defecto dos veces seguidas.

Por lo demás, la corrección es la de siempre: try/except/finally, el clip pasa a
`error` y `_cerrar_si_termino` se llama **siempre**, en el `finally`. Y en
`_cerrar_si_termino` los clips en `error` cuentan para el veredicto del lote:
antes solo contaban los vídeos, así que un lote con un vídeo cortado bien pero un
clip corrupto se cerraba como `listo` y el fallo era invisible desde la API.

**Por qué `.fn`:** Dramatiq expone la función cruda en `actor.fn`. Llamarla
directamente ejecuta la tarea en línea, sin cola, sin Redis y sin worker, que es
lo único que hace falta: lo que se quiere probar es el try/except/finally, no la
cola.

**Por qué el test del Paso 0.4 (el log) vive aquí:** su vehículo más barato es
justo este recorrido —un clip ilegible, que es un lote que acaba en `error`— y
montarlo cuesta un `_montar()` que ya existe. Duplicar el helper en otro
fichero para tener un test de log "propio" no compensa.
"""

import logging
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Archivo, Clip, Lote, Video
from app.tasks import _cerrar_si_termino, procesar_clip

#: ffmpeg que genera un clip real: 2 s de vídeo de barras de color + un tono,
#: codificado en h264/aac, que es justo lo que produce `cortar_y_normalizar`.
_COMANDO_FFMPEG = [
    "ffmpeg", "-y", "-loglevel", "error",
    "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest",
]


def _clip_valido(ffmpeg_disponible, tmp_path: Path) -> str:
    """Un `.mp4` de verdad en disco, o `skip` si ffmpeg no está.

    `skip` y no fallo: el bug del Paso 0.6 está en el control de flujo de
    `procesar_clip`, no en ffmpeg. En un entorno sin ffmpeg, este test no puede
    decir nada, y declararlo es más honesto que inventarse un mock que no
    ejercite `ffprobe`.
    """
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe no disponible: no se puede fabricar un clip real")
    ruta = tmp_path / "clip_valido.mp4"
    proc = subprocess.run([*_COMANDO_FFMPEG, str(ruta)], capture_output=True, timeout=120)
    if proc.returncode != 0 or not ruta.exists() or ruta.stat().st_size == 0:
        pytest.skip(f"ffmpeg no ha podido generar el vídeo: {proc.stderr[-500:]!r}")
    return str(ruta)


def _clip_corrupto(tmp_path: Path) -> str:
    """Un fichero que existe pero que ffprobe no puede leer: un .mp4 de texto.

    Es el caso real (subida a medias, disco lleno, fichero truncado) y da
    exactamente el mismo `CalledProcessError` que un mp4 corrupto de verdad.
    """
    ruta = tmp_path / "clip_corrupto.mp4"
    ruta.write_bytes(b"esto no es un mp4, es un texto\n" * 40)
    return str(ruta)


def _montar(db, *, rutas_clips: list[str], estado_video: str = "cortado",
            estados_clip: list[str] | None = None) -> tuple[int, list[int]]:
    """Crea el lote → archivo → vídeo → clips y devuelve `(lote_id, clip_ids)`.

    Reproduce el estado que deja `procesar_video` **después** de cortar: el vídeo
    en `cortado` y un `Clip` por fichero producido. A partir de ahí lo único que
    corre es `procesar_clip`, que es lo que se quiere probar.
    """
    lote = Lote(estado="procesando")
    db.add(lote)
    db.flush()

    archivo = Archivo(lote_id=lote.id, tipo="video", nombre_original="original.mp4",
                      ruta=str(Path(rutas_clips[0]).parent / "original.mp4"),
                      tamano_bytes=1024)
    db.add(archivo)
    db.flush()

    video = Video(lote_id=lote.id, archivo_id=archivo.id, estado=estado_video,
                  total_clips=len(rutas_clips))
    db.add(video)
    db.flush()

    estados_clip = estados_clip or ["pendiente"] * len(rutas_clips)
    clips = [
        Clip(video_id=video.id, indice=i, ruta=ruta, estado=estado)
        for i, (ruta, estado) in enumerate(zip(rutas_clips, estados_clip))
    ]
    db.add_all(clips)
    db.commit()
    return lote.id, [c.id for c in clips]


def _reventa_por_el_clip(clip_id: int) -> None:
    """Ejecuta `procesar_clip` y **exige** que la excepción salga de la tarea.

    No es azúcar: es la mitad del contrato del Paso 0.6. Con el `except`
    tragándose la excepción, el estado en BD seguía siendo el correcto y todos
    estos tests pasaban, mientras el bug (no reintentar, no dejar rastro en la
    carta muerta) estaba vivo. Por eso se exige `raises` y no solo el estado.

    También se comprueba que la excepción viene de ffprobe leyendo el fichero, y
    no de cualquier otra cosa que también acabe en ese `except`.
    """
    with pytest.raises(subprocess.CalledProcessError) as exc:
        procesar_clip.fn(clip_id)
    assert exc.value.cmd[0] == "ffprobe", (
        f"la excepción viene de otro sitio, no de ffprobe leyendo el clip: {exc.value.cmd!r}"
    )


def _estado(clip_id: int) -> str:
    """Estado del clip leído en una sesión **nueva**.

    Imprescindible: `SessionLocal` usa `expire_on_commit=False`, así que la
    sesión del test conservaría el objeto viejo y el test pasaría sin ver lo que
    hizo la tarea.
    """
    with SessionLocal() as db:
        return db.get(Clip, clip_id).estado


def _estado_lote(lote_id: int) -> str:
    with SessionLocal() as db:
        return db.get(Lote, lote_id).estado


def _duracion_clip(clip_id: int) -> float | None:
    with SessionLocal() as db:
        return db.get(Clip, clip_id).duracion_seg


# --- La política de reintentos, fijada --------------------------------------
def test_procesar_clip_declara_que_no_reintenta():
    """El actor de clips declara `max_retries=0`, y la decisión queda fijada.

    No es un detalle de estilo. Con el `except` tragándose la excepción, los
    reintentos de Dramatiq **no existían** (el `max_retries=3` era decorativo) y
    el lote se quedaba colgado. Hoy la decisión es explícita y en la dirección
    contraria: cero reintentos y `raise`, porque el fichero es nuestro y si
    ffprobe no lo lee no lo va a leer nunca más; reintesar solo retrasa el aviso.
    La otra mitad de la decisión (que la excepción se propague) la fija
    `_reventa_por_el_clip`, que se usa en cada test de clip ilegible.

    El próximo que lea el `0` como un descuido y lo suba a 3 "por robustez"
    reintroduciría el bug sin que ningún test se enterara. Esta es la línea que
    se entera.

    OJO, y esto es lo importante: este 0 **no** es la tabla final de reintentos
    (la fija el Paso 7.5) y **tampoco es "el valor bueno"**. Lo único que fija es
    que `procesar_clip` es un caso particular: su trabajo es determinista sobre
    un fichero que acabamos de escribir nosotros, así que reintentar no arregla
    nada.

    Y **no se debe subir a los otros dos actores sin pensarlo**, porque
    `procesar_lote` (3) y `procesar_video` (2) hoy sí reintentan de verdad: sus
    rutas sin capturar (un fallo de Postgres, un `send` con Redis caído) escapan
    y los reintentos son reales. Por eso los dos llevan guarda de idempotencia y
    por eso subirles los reintentos no es inocuo: hoy, disparar esas tareas es
    corrupción de datos (`Video` duplicados, el mismo archivo cortado dos veces),
    no solo trabajo desperdiciado. Fijar aquí los otros dos dejaría
    institucionalizado un valor que se sabe equivocado.
    """
    assert procesar_clip.options["max_retries"] == 0


# --- El recorrido que pedía el paso ----------------------------------------
def test_clip_corrupto_no_deja_el_lote_colgado(db, tmp_path, ffmpeg_disponible):
    """Un clip ilegible deja el lote en `error`, no clavado en `procesando`.

    Este es el test del Paso 0.6 tal cual está escrito en el `TODO.md`, con las
    dos mitades del contrato comprobadas.

    Antes de la corrección esto se quedaba en `procesando` para siempre: el clip
    en `pendiente` y `_cerrar_si_termino` sin llegar a llamarse nunca.
    """
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe no disponible: no se puede provocar el fallo")

    corrupto = _clip_corrupto(tmp_path)
    # Comprobación previa, para que el test no pueda pasar sin ejercitar ffprobe:
    # si este fichero no hiciera fallar a ffprobe, el `except` no se ejecutaría
    # y el test estaría probando una ruta distinta de la que dice comprobar.
    sonda = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", corrupto],
        capture_output=True, timeout=60,
    )
    assert sonda.returncode != 0, "ffprobe ha leído un fichero de texto: el test no probaría nada"

    lote_id, (clip_id,) = _montar(db, rutas_clips=[corrupto])

    # (1) La excepción se propaga: sin esto, el `except` podría volver a
    #     tragársela y el lote cerrarse sin dejar rastro en la carta muerta.
    _reventa_por_el_clip(clip_id)

    # (2) Y el estado en BD es el correcto, leído en sesiones nuevas.
    assert _estado(clip_id) == "error"
    assert _estado_lote(lote_id) == "error"


def test_el_cierre_es_idempotente(db, tmp_path, ffmpeg_disponible):
    """Llamar a `_cerrar_si_termino` otra vez no cambia nada ni cuelga el lote.

    `_cerrar_si_termino` se llama desde `procesar_video` **y** desde el
    `finally` de `procesar_clip`, o sea que en un lote normal se ejecuta muchas
    veces para el mismo lote. Si el segundo cierre se colgara o degradara el
    estado, el lote se cerraría `listo` después de haberlo cerrado `error` (o al
    revés), y el fallo volvería a ser invisible.
    """
    lote_id, (clip_id,) = _montar(db, rutas_clips=[_clip_corrupto(tmp_path)])

    _reventa_por_el_clip(clip_id)
    assert _estado_lote(lote_id) == "error"

    for _ in range(3):
        _cerrar_si_termino(lote_id)
        assert _estado_lote(lote_id) == "error", "una re-evaluación ha cambiado el veredicto"

    # Y el clip sigue en `error`: el cierre no resucita nada.
    assert _estado(clip_id) == "error"


def test_un_clip_pendiente_impide_que_el_lote_se_cierre(db, tmp_path):
    """Con un clip en `pendiente`, `_cerrar_si_termino` no cierra el lote.

    Es la contraprueba de `test_clip_corrupto_no_deja_el_lote_colgado`: demuestra
    que el veredicto `error` de aquel test viene de que el clip pasó a `error` y
    no de que cualquier llamada a `_cerrar_si_termino` cierre el lote. Si esto
    fallara (el lote cerrándose con un `pendiente`), el Paso 0.6 se habría
    "arreglado" a costa de cerrar lotes antes de tiempo, que es peor: el fallo se
    vería de nuevo en cuanto el worker se cayera del todo.
    """
    lote_id, (clip_id,) = _montar(db, rutas_clips=[_clip_corrupto(tmp_path)])

    _cerrar_si_termino(lote_id)

    assert _estado(clip_id) == "pendiente"
    assert _estado_lote(lote_id) == "procesando", (
        "el lote se cerró con un clip todavía sin procesar: eso no debe pasar"
    )


def test_un_clip_pendiente_tras_otro_terminado_deja_el_lote_procesando(db, tmp_path, ffmpeg_disponible):
    """El caso real de cola: un clip va bien, el otro sigue en `pendiente`.

    Cuando el worker procesa el primero de dos clips, el lote no puede
    cerrarse todavía. Si aquí se cerrara, el segundo clip llegaría tarde a un
    lote ya cerrado como `listo`.
    """
    valido = _clip_valido(ffmpeg_disponible, tmp_path)
    lote_id, (bueno_id, pendiente_id) = _montar(
        db, rutas_clips=[valido, _clip_corrupto(tmp_path)]
    )

    procesar_clip.fn(bueno_id)

    assert _estado(bueno_id) == "listo"
    assert _estado(pendiente_id) == "pendiente"
    assert _estado_lote(lote_id) == "procesando"


# --- El camino feliz --------------------------------------------------------
def test_clip_valido_pasa_a_listo_y_cierra_el_lote(db, tmp_path, ffmpeg_disponible):
    """Un clip de verdad: `listo`, con su duración, y el lote se cierra `listo`."""
    valido = _clip_valido(ffmpeg_disponible, tmp_path)
    lote_id, (clip_id,) = _montar(db, rutas_clips=[valido])

    procesar_clip.fn(clip_id)

    assert _estado(clip_id) == "listo"
    duracion = _duracion_clip(clip_id)
    assert duracion is not None and 0.5 <= duracion <= 4.0, f"duración rara: {duracion}"
    assert _estado_lote(lote_id) == "listo"


def test_un_clip_valido_no_propaga_nada(db, tmp_path, ffmpeg_disponible):
    """La otra mitad del contrato: el camino feliz **no** lanza.

    Sin esto, un `raise` colocado sin querer (o un `pytest.raises` puesto en el
    sitio equivocado) dejaría la suite verde y el worker con todos los clips
    sanos en la carta muerta.
    """
    valido = _clip_valido(ffmpeg_disponible, tmp_path)
    _lote_id, (clip_id,) = _montar(db, rutas_clips=[valido])

    assert procesar_clip.fn(clip_id) is None, "un clip legible no debería lanzar"
    assert _estado(clip_id) == "listo"


def test_ejecutar_la_tarea_dos_veces_no_altera_el_veredicto(
    db, tmp_path, ffmpeg_disponible
):
    """Correr la tarea dos veces sobre el mismo clip deja el mismo veredicto.

    `procesar_video` borra los clips previos antes de reencolar, así que un
    reintento normal nace de nuevo en `pendiente`; aun así, si la cola entregara
    dos veces el mismo id, el lote no puede pasar de `error` a `listo` (ni al
    revés) entre las dos ejecuciones.
    """
    lote_id, (clip_id,) = _montar(db, rutas_clips=[_clip_corrupto(tmp_path)])

    _reventa_por_el_clip(clip_id)
    _reventa_por_el_clip(clip_id)

    assert _estado(clip_id) == "error"
    assert _estado_lote(lote_id) == "error"


# --- El segundo count de `_cerrar_si_termino` -------------------------------
def test_video_cortado_con_un_clip_en_error_cierra_el_lote_como_error(db, tmp_path):
    """Vídeo cortado bien + un clip en `error` → el lote se cierra `error`.

    Este es el fallo que cubre el segundo `count` de `_cerrar_si_termino`. Antes
    solo contaba vídeos en error, así que el lote se cerraba `listo` con clips
    rotos dentro: `GET /lotes/{id}` decía `listo` y el fallo era invisible.
    """
    lote_id, clip_ids = _montar(
        db,
        rutas_clips=[str(tmp_path / "ok.mp4"), _clip_corrupto(tmp_path)],
        estado_video="cortado",
        estados_clip=["listo", "error"],
    )

    _cerrar_si_termino(lote_id)

    assert [_estado(c) for c in clip_ids] == ["listo", "error"]
    assert _estado_lote(lote_id) == "error", (
        "un clip en error no puede cerrar el lote como 'listo'"
    )


def test_todos_los_clips_listo_cierra_el_lote_como_listo(db, tmp_path, ffmpeg_disponible):
    """La contraprueba del anterior: sin errores, `listo`."""
    valido = _clip_valido(ffmpeg_disponible, tmp_path)
    lote_id, clip_ids = _montar(
        db,
        rutas_clips=[valido, valido],
        estados_clip=["listo", "listo"],
    )

    _cerrar_si_termino(lote_id)

    assert [_estado(c) for c in clip_ids] == ["listo", "listo"]
    assert _estado_lote(lote_id) == "listo"


def test_recorrido_completo_un_clip_bueno_y_otro_corrupto(db, tmp_path, ffmpeg_disponible):
    """El escenario de la vida real: dos clips, uno bien y otro no.

    Sin ffmpeg no se puede fabricar el clip bueno, de ahí el `skip` (y no un
    mock): la mitad buena del recorrido es `ffprobe` leyendo un fichero real.
    """
    lote_id, (bueno_id, corrupto_id) = _montar(
        db, rutas_clips=[_clip_valido(ffmpeg_disponible, tmp_path), _clip_corrupto(tmp_path)]
    )

    procesar_clip.fn(bueno_id)
    # Con el segundo clip todavía en `pendiente`, el lote no se puede cerrar.
    assert _estado_lote(lote_id) == "procesando"

    _reventa_por_el_clip(corrupto_id)

    assert _estado(bueno_id) == "listo"
    assert _estado(corrupto_id) == "error"
    assert _estado_lote(lote_id) == "error"


def test_varios_clips_corruptos_no_dejan_pendientes(db, tmp_path, ffmpeg_disponible):
    """Todos los clips ilegibles: el lote se cierra y no queda ninguno pendiente.

    El fallo original no se cebaba con un solo clip: con cinco clips ilegibles,
    los cinco se quedaban en `pendiente` y el lote, colgado.
    """
    rutas = []
    for i in range(5):
        p = tmp_path / f"roto_{i}.mp4"
        p.write_bytes(f"basura {i}\n".encode() * 100)
        rutas.append(str(p))

    lote_id, clip_ids = _montar(db, rutas_clips=rutas)

    for clip_id in clip_ids:
        _reventa_por_el_clip(clip_id)

    with SessionLocal() as db2:
        estados = db2.scalars(select(Clip.estado).where(Clip.id.in_(clip_ids))).all()
    assert set(estados) == {"error"}
    assert "pendiente" not in estados
    assert _estado_lote(lote_id) == "error"


def test_clip_inexistente_no_revienta_la_tarea(db, tmp_path, ffmpeg_disponible):
    """Un `clip_id` que ya no está en la BD sale limpio, sin tocar ningún lote.

    `procesar_video` borra los clips antes de reencolar; si la cola entrega un id
    viejo, la tarea tiene que ser inocua y nologearse un error que el operador
    acabaría persiguiendo. Aquí **no** hay excepción: es el único camino en el
    que `procesar_clip` sale con `return` sin haber medido nada.
    """
    lote_id, (clip_id,) = _montar(db, rutas_clips=[_clip_corrupto(tmp_path)])

    assert procesar_clip.fn(999_999) is None

    assert _estado(clip_id) == "pendiente"
    assert _estado_lote(lote_id) == "procesando", (
        "un clip inexistente no debe poder cerrar un lote"
    )


# --- Paso 0.4: el log basta solo para diagnosticar el fallo -----------------
def test_lote_fallido_se_explica_en_el_log_sin_consultar_la_bd(
    db, tmp_path, ffmpeg_disponible, caplog
):
    """Un lote que acaba en `error` se diagnostica leyendo el log, y solo el log.

    Es la verificación del Paso 0.4, palabra por palabra: "un lote fallido
    produce líneas de log que dicen **por qué** y en qué paso, sin consultar la
    BD". Antes de ese paso, `tasks.py` definía `log` y no lo usaba nunca: aquí
    `caplog.records` saldría vacío.

    Las tres cosas que se piden, una por una:

    1. **en qué paso**: hay un registro que habla del clip (el paso que falló, la
       medición) y otro que habla del cierre del lote (el paso siguiente). Se
       distinguen por los ids que citan, no por su redacción, para que reescribir
       el mensaje no rompa el test.
    2. **por qué**: ese registro es un `log.exception`, o sea que lleva el
       traceback adjunto. Un log que dice "falló" sin decir por qué no cumple el
       paso: el operador tiene que ir a la BD a mirar, que es justo lo que este
       paso viene a quitar.
    3. **sin consultar la BD**: los mensajes llevan `clip_id`, `video_id` y
       `lote_id` de contexto, y el veredicto final del lote también está
       logueado. Sin eso, un `tail -f` en el worker no dice a qué lote pertenece
       una línea.

    **Sobre `caplog` y `configurar_logging(force=True)`:** `force=True` borra
    los handlers de la raíz, incluido el de pytest, así que este test podría
    colgarse de que la importación de `app.tasks` ocurra *antes* de que pytest
    añada el suyo. Se comprobó que no es el caso: `configurar_logging()` se
    ejecuta al importar el módulo, o sea en **tiempo de colección**, y pytest
    engancha su `LogCaptureHandler` a la raíz en cada test, después (los handlers
    que hay en la raíz durante el test son los de pytest, más el `basicConfig`).
    Por eso `caplog` ve los registros. `at_level` baja la barra a INFO
    explícitamente, para que el test no dependa del nivel con el que se
    configure el root.
    """
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe no disponible: no se puede provocar el fallo")

    lote_id, (clip_id,) = _montar(db, rutas_clips=[_clip_corrupto(tmp_path)])
    with SessionLocal() as db2:
        video_id = db2.scalar(select(Video.id).where(Video.lote_id == lote_id))

    with caplog.at_level(logging.INFO, logger="app.tasks"):
        _reventa_por_el_clip(clip_id)

    registros = [r for r in caplog.records if r.name == "app.tasks"]
    mensajes = [r.getMessage() for r in registros]
    assert mensajes, "app.tasks no ha logueado nada: el log no sirve para diagnosticar el fallo"

    # 1. En qué paso falló: el registro del clip, y el del cierre del lote.
    del_clip = [r for r in registros if f"clip {clip_id}" in r.getMessage()]
    assert del_clip, f"nada dice qué pasó con el clip {clip_id}: {mensajes}"
    del_lote = [
        r for r in registros
        if f"lote {lote_id}" in r.getMessage() and f"clip {clip_id}" not in r.getMessage()
    ]
    assert del_lote, f"nada dice qué pasó con el lote {lote_id}: {mensajes}"
    assert any("error" in r.getMessage() for r in del_lote), (
        f"el veredicto del lote no está en el log: {[r.getMessage() for r in del_lote]}"
    )

    # 2. Por qué: el fallo del clip es un `log.exception`, con traceback.
    fallos = [r for r in del_clip if r.levelno >= logging.ERROR]
    assert fallos, f"el fallo del clip no está logueado como error: {mensajes}"
    exc_info = fallos[0].exc_info
    assert exc_info is not None, (
        "el log dice que el clip falló pero no dice por qué (falta el traceback)"
    )
    assert "ffprobe" in " ".join(str(a) for a in (getattr(exc_info[1], "cmd", None) or [])), (
        f"el traceback no apunta a ffprobe: {exc_info[1]!r}"
    )

    # 3. Contexto: el mensaje del fallo lleva los tres ids, no solo el del clip.
    #
    # Ojo con el `f"lote {lote_id}"` de esta línea: en este fixture los tres ids
    # valen 1 (son secuencias recién reiniciadas por el `drop_all` por test), así
    # que la comprobación no puede separarlos por número. Lo que sí exige es que
    # el mensaje contenga la palabra `lote` con su id, que es justo lo que
    # desaparecería si alguien quitara el contexto del mensaje.
    mensaje_fallo = fallos[0].getMessage()
    assert f"clip {clip_id}" in mensaje_fallo
    assert f"lote {lote_id}" in mensaje_fallo
    assert f"vídeo {video_id}" in mensaje_fallo
