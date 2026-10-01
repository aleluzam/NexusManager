"""Tareas en segundo plano (Dramatiq). Cada paso es una función pequeña e idempotente."""
import logging

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from docx import Document
from sqlalchemy import func, select

from . import ffmpeg_utils, storage
from .config import REDIS_URL, configurar_logging
from .db import SessionLocal
from .models import Archivo, Clip, Lote, Video

configurar_logging()  # este módulo es el punto de entrada del worker

dramatiq.set_broker(RedisBroker(url=REDIS_URL))
log = logging.getLogger(__name__)


# ---------- 1. Lote: lee documentos y lanza los videos ----------
# max_retries=3, y aquí los reintentos SÍ se activan: el único `except` de esta
# función es el de los documentos, así que un fallo de Postgres, un lote
# inexistente o un `procesar_video.send` con Redis caído escapan y reintentan de
# verdad. Por eso la guarda de idempotencia de abajo no es opcional: sin ella un
# reintento vuelve a recorrer los archivos y duplica las filas `Video`.
@dramatiq.actor(queue_name="ingesta", max_retries=3)
def procesar_lote(lote_id: int):
    with SessionLocal() as db:
        lote = db.get(Lote, lote_id)
        if lote is None:
            # El id llega desde la cola, que no garantiza que el lote siga ahí.
            # Sin esta comprobación, `lote.estado` revienta con AttributeError y
            # Dramatiq reintenta tres veces un lote que no va a aparecer nunca.
            log.warning("lote %s: no existe, nada que procesar", lote_id)
            return
        if lote.estado != "pendiente":
            # Idempotencia: no repetir trabajo, ni duplicar filas.
            log.info("lote %s: ya no está pendiente (estado '%s'), no se repite",
                     lote_id, lote.estado)
            return
        lote.estado = "procesando"

        # Documentos -> contexto del lote (por ahora texto crudo; luego lo resumirá el LLM)
        textos = []
        for a in db.scalars(select(Archivo).where(Archivo.lote_id == lote_id, Archivo.tipo == "doc")):
            # Un .docx corrupto no puede tumbar el lote: el contexto es auxiliar y
            # los vídeos son el trabajo de verdad. Antes, la excepción de
            # `Document(ruta)` escapaba, Dramatiq agotaba los reintentos y el lote
            # se quedaba en 'pendiente' para siempre, sin error ni log.
            try:
                textos.append(_leer_documento(a.ruta))
            except Exception:
                log.exception("lote %s: no se pudo leer el documento %s, se sigue sin él",
                              lote_id, a.ruta)
        lote.contexto_texto = "\n\n".join(t for t in textos if t) or None

        # Un registro Video por cada archivo de video
        videos = []
        for a in db.scalars(select(Archivo).where(Archivo.lote_id == lote_id, Archivo.tipo == "video")):
            v = Video(lote_id=lote_id, archivo_id=a.id)
            db.add(v)
            videos.append(v)
        db.commit()
        ids = [v.id for v in videos]

    log.info("lote %s: %s documento(s) leídos, %s vídeo(s) encolados",
             lote_id, len(textos), len(ids))
    if not ids:
        _cerrar_si_termino(lote_id)
    for vid in ids:
        procesar_video.send(vid)


def _leer_documento(ruta: str) -> str:
    if ruta.lower().endswith(".txt"):
        with open(ruta, encoding="utf-8", errors="ignore") as f:
            return f.read()
    if ruta.lower().endswith(".docx"):
        return "\n".join(p.text for p in Document(ruta).paragraphs)
    return ""


# ---------- 2. Video: corta y normaliza ----------
# max_retries=2, y el `except` de ffmpeg solo cubre el corte: un fallo al
# encolar los clips escapa y reintenta de verdad. Por eso la reentrada de abajo
# reapunta los clips pendientes en vez de limitarse a salir.
@dramatiq.actor(queue_name="ingesta", max_retries=2)
def procesar_video(video_id: int):
    with SessionLocal() as db:
        v = db.get(Video, video_id)
        if v is None:
            # Misma comprobación que en `procesar_lote` y `procesar_clip`: el id
            # viene de la cola y no garantiza que la fila siga existiendo.
            log.warning("vídeo %s: no existe, nada que procesar", video_id)
            return
        lote_id = v.lote_id
        if v.estado == "cortado":
            # Reentrada: el corte no se repite (idempotencia), pero sí se reapuntan
            # los clips que sigan en 'pendiente'. Con un `return` seco, un fallo de
            # Redis en el bucle de encolado colgaba el lote en 'procesando' para
            # siempre: el reintento se encontraba 'cortado' y se iba sin encolar.
            pendientes = list(db.scalars(
                select(Clip.id).where(Clip.video_id == video_id, Clip.estado == "pendiente")))
        else:
            pendientes = None
            v.estado = "cortando"
            db.commit()
            archivo = db.get(Archivo, v.archivo_id)

    if pendientes is not None:
        log.info("vídeo %s (lote %s): ya estaba cortado, reapuntan %s clip(s) pendiente(s)",
                 video_id, lote_id, len(pendientes))
        for cid in pendientes:
            procesar_clip.send(cid)
        _cerrar_si_termino(lote_id)
        return

    log.info("vídeo %s (lote %s): cortando %s", video_id, lote_id, archivo.ruta)

    try:
        dur = ffmpeg_utils.duracion(archivo.ruta)
        rutas = ffmpeg_utils.cortar_y_normalizar(archivo.ruta, storage.dir_clips(lote_id, video_id))
    except Exception as e:  # video corrupto, ffmpeg falló, etc.
        with SessionLocal() as db:
            v = db.get(Video, video_id)
            v.estado, v.error = "error", str(e)[:1000]
            db.commit()
        log.exception("vídeo %s (lote %s): corte fallido, queda en error", video_id, lote_id)
        _cerrar_si_termino(lote_id)
        return

    with SessionLocal() as db:
        v = db.get(Video, video_id)
        # Si se reintenta, borra clips previos para no duplicar
        db.query(Clip).filter(Clip.video_id == video_id).delete()
        clips = [Clip(video_id=video_id, indice=i, ruta=r) for i, r in enumerate(rutas)]
        db.add_all(clips)
        v.duracion_seg, v.total_clips, v.estado = dur, len(clips), "cortado"
        db.commit()
        clip_ids = [c.id for c in clips]
    log.info("vídeo %s (lote %s): %.2fs -> %s clip(s)", video_id, lote_id, dur, len(clip_ids))

    for cid in clip_ids:
        procesar_clip.send(cid)
    _cerrar_si_termino(lote_id)


# ---------- 3. Clip: aquí irán transcripción, visión, JSON y embedding ----------
#
# max_retries=0, y no por descuido: el trabajo es medir la duración de un fichero
# que acabamos de escribir nosotros, así que si ffprobe no lo lee es que no lo va
# a leer nunca más. Reintentar no arregla un clip corrupto, solo retrasa el aviso.
#
# El `raise` de abajo es lo que hace que esto llegue a la carta muerta: sin él,
# `after_process_message` sale por `if exception is None: return`, Dramatiq hace
# ACK y el mensaje desaparece sin dejar rastro. Ojo con el TTL de esa cola:
# `DEFAULT_DEAD_MESSAGE_TTL` son 7 días, así que el consumidor de la Fase 8 tiene
# que mirar antes de que el mantenimiento de Redis la limpie.
#
# OJO: este 0 NO es la tabla final de reintentos, la fija el Paso 7.5. Y no lo
# copies a los otros dos actores sin pensarlo: `procesar_lote` (3) y
# `procesar_video` (2) sí reintentan de verdad hoy, porque tienen rutas sin
# capturar. Ese 0 no es "el valor bueno", es el valor que corresponde a un trabajo
# determinista sobre un fichero que acabamos de escribir.
@dramatiq.actor(queue_name="ingesta", max_retries=0)
def procesar_clip(clip_id: int):
    with SessionLocal() as db:
        clip = db.get(Clip, clip_id)
        if clip is None:
            log.warning("clip %s: no existe, nada que procesar", clip_id)
            return
        video_id = clip.video_id
        lote_id = db.get(Video, video_id).lote_id

    # TODO (siguientes pasos):
    #   transcribir(clip)         -> faster-whisper
    #   describir_frames(clip)    -> modelo de visión (Ollama), usando lote.contexto_texto
    #   generar_ficha(clip)       -> JSON estructurado
    #   generar_embedding(clip)   -> bge-m3 / multilingual-e5
    try:
        with SessionLocal() as db:
            clip = db.get(Clip, clip_id)
            # `duracion` usa ffprobe con check=True: lanza si el clip no es legible.
            clip.duracion_seg = ffmpeg_utils.duracion(clip.ruta)
            clip.estado = "listo"
            db.commit()
            dur = clip.duracion_seg  # se lee DENTRO de la sesión, a propósito (ver _cerrar_si_termino)
        log.info("clip %s (vídeo %s, lote %s): listo, %.2fs",
                 clip_id, video_id, lote_id, dur)
    except Exception:
        # Un clip corrupto no puede quedarse en 'pendiente': mientras exista uno,
        # `_cerrar_si_termino` lo cuenta y el lote no se cierra nunca.
        with SessionLocal() as db:
            clip = db.get(Clip, clip_id)
            clip.estado = "error"
            db.commit()
        log.exception("clip %s (vídeo %s, lote %s): no se pudo medir, queda en error",
                      clip_id, video_id, lote_id)
        raise
    finally:
        # Siempre, incluso si la escritura del estado de error falla: es lo
        # único que cierra el lote. Sin este finally, Dramatiq agota max_retries,
        # la tarea muere en la carta muerta y el lote se queda en 'procesando'
        # para siempre sin que nadie entienda por qué.
        _cerrar_si_termino(lote_id)


# ---------- Cierre del lote (el estado vive en Postgres, no en la cola) ----------
def _cerrar_si_termino(lote_id: int):
    with SessionLocal() as db:
        pendientes_v = db.scalar(
            select(func.count()).select_from(Video)
            .where(Video.lote_id == lote_id, Video.estado.in_(["pendiente", "cortando"])))
        pendientes_c = db.scalar(
            select(func.count()).select_from(Clip).join(Video, Clip.video_id == Video.id)
            .where(Video.lote_id == lote_id, Clip.estado == "pendiente"))
        if pendientes_v or pendientes_c:
            return
        errores_v = db.scalar(
            select(func.count()).select_from(Video)
            .where(Video.lote_id == lote_id, Video.estado == "error"))
        # Los clips en error cuentan para el veredicto del lote. Sin este segundo
        # count, un vídeo que se corta bien pero tiene un clip corrupto cerraba
        # el lote como 'listo' y el fallo quedaba invisible desde la API.
        errores_c = db.scalar(
            select(func.count()).select_from(Clip).join(Video, Clip.video_id == Video.id)
            .where(Video.lote_id == lote_id, Clip.estado == "error"))
        lote = db.get(Lote, lote_id)
        # El estado se calcula en una variable y el log va FUERA del `with`, así que
        # el valor se lee dentro de la sesión. Leer `lote.estado` después del cierre
        # solo funciona porque SessionLocal usa expire_on_commit=False; si alguien
        # cambia ese flag, esto pasa a lanzar DetachedInstanceError, y en
        # procesar_clip ese except markaría como error un clip que estaba bien.
        estado = "error" if (errores_v or errores_c) else "listo"
        lote.estado = estado
        db.commit()
    log.info("lote %s: cerrado como '%s' (%s vídeo(s) y %s clip(s) en error)",
             lote_id, estado, errores_v, errores_c)
