"""Tareas en segundo plano (Dramatiq). Cada paso es una función pequeña e idempotente."""
import logging

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from sqlalchemy import func, select

from . import ffmpeg_utils, storage
from .config import REDIS_URL
from .db import SessionLocal
from .models import Archivo, Clip, Lote, Video

dramatiq.set_broker(RedisBroker(url=REDIS_URL))
log = logging.getLogger(__name__)


# ---------- 1. Lote: lee documentos y lanza los videos ----------
@dramatiq.actor(queue_name="ingesta", max_retries=3)
def procesar_lote(lote_id: int):
    with SessionLocal() as db:
        lote = db.get(Lote, lote_id)
        lote.estado = "procesando"

        # Documentos -> contexto del lote (por ahora texto crudo; luego lo resumirá el LLM)
        textos = []
        for a in db.scalars(select(Archivo).where(Archivo.lote_id == lote_id, Archivo.tipo == "doc")):
            textos.append(_leer_documento(a.ruta))
        lote.contexto_texto = "\n\n".join(t for t in textos if t) or None

        # Un registro Video por cada archivo de video
        videos = []
        for a in db.scalars(select(Archivo).where(Archivo.lote_id == lote_id, Archivo.tipo == "video")):
            v = Video(lote_id=lote_id, archivo_id=a.id)
            db.add(v)
            videos.append(v)
        db.commit()
        ids = [v.id for v in videos]

    if not ids:
        _cerrar_si_termino(lote_id)
    for vid in ids:
        procesar_video.send(vid)


def _leer_documento(ruta: str) -> str:
    if ruta.lower().endswith(".txt"):
        with open(ruta, encoding="utf-8", errors="ignore") as f:
            return f.read()
    if ruta.lower().endswith(".docx"):
        from docx import Document
        return "\n".join(p.text for p in Document(ruta).paragraphs)
    return ""


# ---------- 2. Video: corta y normaliza ----------
@dramatiq.actor(queue_name="ingesta", max_retries=2)
def procesar_video(video_id: int):
    with SessionLocal() as db:
        v = db.get(Video, video_id)
        if v.estado == "cortado":  # idempotencia: no repetir trabajo
            return
        v.estado = "cortando"
        db.commit()
        archivo = db.get(Archivo, v.archivo_id)
        lote_id = v.lote_id

    try:
        dur = ffmpeg_utils.duracion(archivo.ruta)
        rutas = ffmpeg_utils.cortar_y_normalizar(archivo.ruta, storage.dir_clips(lote_id, video_id))
    except Exception as e:  # video corrupto, ffmpeg falló, etc.
        with SessionLocal() as db:
            v = db.get(Video, video_id)
            v.estado, v.error = "error", str(e)[:1000]
            db.commit()
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

    for cid in clip_ids:
        procesar_clip.send(cid)
    _cerrar_si_termino(lote_id)


# ---------- 3. Clip: aquí irán transcripción, visión, JSON y embedding ----------
@dramatiq.actor(queue_name="ingesta", max_retries=3)
def procesar_clip(clip_id: int):
    with SessionLocal() as db:
        clip = db.get(Clip, clip_id)
        lote_id = db.get(Video, clip.video_id).lote_id

        # TODO (siguientes pasos):
        #   transcribir(clip)         -> faster-whisper
        #   describir_frames(clip)    -> modelo de visión (Ollama), usando lote.contexto_texto
        #   generar_ficha(clip)       -> JSON estructurado
        #   generar_embedding(clip)   -> bge-m3 / multilingual-e5
        clip.duracion_seg = ffmpeg_utils.duracion(clip.ruta)
        clip.estado = "listo"
        db.commit()

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
        errores = db.scalar(
            select(func.count()).select_from(Video)
            .where(Video.lote_id == lote_id, Video.estado == "error"))
        lote = db.get(Lote, lote_id)
        lote.estado = "error" if errores else "listo"
        db.commit()
