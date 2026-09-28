import os
import re

import aiofiles
from fastapi import HTTPException, UploadFile

from .config import DOC_EXT, MAX_UPLOAD_BYTES, STORAGE_DIR, VIDEO_EXT


def detectar_tipo(nombre: str) -> str | None:
    ext = os.path.splitext(nombre)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in DOC_EXT:
        return "doc"
    return None


def nombre_seguro(nombre: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(nombre))


def dir_originales(lote_id: int) -> str:
    return os.path.join(STORAGE_DIR, "originales", f"lote_{lote_id}")


def dir_clips(lote_id: int, video_id: int) -> str:
    return os.path.join(STORAGE_DIR, "clips", f"lote_{lote_id}", f"video_{video_id}")


async def guardar_upload(lote_id: int, f: UploadFile) -> tuple[str, int]:
    """Guarda en disco por trozos (nunca carga el archivo entero en memoria)."""
    carpeta = dir_originales(lote_id)
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, nombre_seguro(f.filename))
    total = 0
    async with aiofiles.open(ruta, "wb") as out:
        while chunk := await f.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                await out.close()
                os.remove(ruta)
                raise HTTPException(413, f"{f.filename} supera el tamaño máximo")
            await out.write(chunk)
    return ruta, total
