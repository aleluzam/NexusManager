from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, UploadFile
from sqlalchemy import func, select

from . import storage
from .config import API_KEY
from .db import SessionLocal, init_db
from .models import Archivo, Clip, Lote, Video
from .tasks import procesar_lote


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ingestion-service", lifespan=lifespan)


def verificar(x_api_key: str):
    if x_api_key != API_KEY:
        raise HTTPException(401, "API key inválida")


@app.post("/lotes", status_code=202)
async def crear_lote(files: list[UploadFile], x_api_key: str = Header()):
    verificar(x_api_key)
    if not files:
        raise HTTPException(400, "No se enviaron archivos")

    tipos = [storage.detectar_tipo(f.filename) for f in files]
    if None in tipos:
        malos = [f.filename for f, t in zip(files, tipos) if t is None]
        raise HTTPException(400, f"Formato no soportado: {malos}")
    if "video" not in tipos:
        raise HTTPException(400, "El lote debe incluir al menos un video")

    with SessionLocal() as db:
        lote = Lote()
        db.add(lote)
        db.commit()
        lote_id = lote.id

        for f, tipo in zip(files, tipos):
            ruta, tam = await storage.guardar_upload(lote_id, f)
            db.add(Archivo(lote_id=lote_id, tipo=tipo, nombre_original=f.filename,
                           ruta=ruta, tamano_bytes=tam))
        db.commit()

    procesar_lote.send(lote_id)  # encola y responde de inmediato
    return {"lote_id": lote_id, "estado": "pendiente"}


@app.get("/lotes/{lote_id}")
def estado_lote(lote_id: int, x_api_key: str = Header()):
    verificar(x_api_key)
    with SessionLocal() as db:
        lote = db.get(Lote, lote_id)
        if not lote:
            raise HTTPException(404, "Lote no encontrado")
        total = db.scalar(select(func.count()).select_from(Clip).join(Video)
                          .where(Video.lote_id == lote_id))
        listos = db.scalar(select(func.count()).select_from(Clip).join(Video)
                           .where(Video.lote_id == lote_id, Clip.estado == "listo"))
        videos = db.scalars(select(Video).where(Video.lote_id == lote_id)).all()
        return {
            "lote_id": lote_id,
            "estado": lote.estado,
            "clips": {"total": total, "listos": listos},
            "videos": [{"id": v.id, "estado": v.estado, "error": v.error} for v in videos],
        }


@app.get("/lotes/{lote_id}/clips")
def clips_lote(lote_id: int, x_api_key: str = Header()):
    verificar(x_api_key)
    with SessionLocal() as db:
        filas = db.scalars(select(Clip).join(Video).where(Video.lote_id == lote_id)
                           .order_by(Clip.video_id, Clip.indice)).all()
        return [{"id": c.id, "video_id": c.video_id, "indice": c.indice, "estado": c.estado,
                 "duracion_seg": c.duracion_seg, "ficha": c.ficha} for c in filas]
