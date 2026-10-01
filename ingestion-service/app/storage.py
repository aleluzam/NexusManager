import os
import re
from uuid import uuid4

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


def nombre_seguro(nombre: str, sufijo: str | None = None) -> str:
    """Normaliza el nombre de un upload y le añade un token único.

    El nombre de origen ya se guarda aparte en `archivos.nombre_original`, así que
    aquí no hace falta que sea legible: lo que hace falta es que **dos archivos
    distintos nunca acaben en la misma ruta**. Antes `mi video.mp4` y
    `mi_video.mp4` normalizaban los dos a `mi_video.mp4`, el segundo upload
    pisaba el archivo del primero, y la fila del primer `archivos` quedaba
    apuntando a un archivo que ya no era el suyo.

    El token es aleatorio, no un hash del nombre: dos archivos con el mismo
    nombre colisionan igual bajo cualquier hash del nombre, y eso es
    precisamente el caso que hay que cubrir.

    `sufijo` existe para que los tests puedan fijar el resultado; el pipeline
    normal nunca lo pasa y se queda con el token aleatorio.
    """
    base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(nombre))
    stem, ext = os.path.splitext(base)
    # La normalización deja pasar los puntos, así que "." y ".." sobreviven y
    # `os.path.join(carpeta, "..")` saldría de la carpeta del lote. No deben
    # llegar al join. El mismo `strip` evita un nombre que empiece por "_" o "-".
    stem = stem.strip("._") or "archivo"
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", ext):
        ext = ""
    # Un nombre de 300 caracteres se pasa de los 255 bytes que admite un
    # componente de fichero en APFS y ext4, y el upload reventaría con
    # ENAMETOOLONG. El recorte va en el stem, nunca en la extensión.
    stem = stem[:200]
    return f"{stem}_{sufijo or uuid4().hex[:12]}{ext}"


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
