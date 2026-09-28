import glob
import json
import os
import subprocess

from .config import CLIP_SECONDS


def duracion(ruta: str) -> float:
    """Usa ffprobe. Lanza excepción si el video no es legible."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", ruta],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def cortar_y_normalizar(origen: str, carpeta_salida: str) -> list[str]:
    """Corta en clips de CLIP_SECONDS y normaliza todos (mismo códec, 720p, 30fps)
    para que luego el concat de FFmpeg funcione sin re-encodear."""
    os.makedirs(carpeta_salida, exist_ok=True)
    plantilla = os.path.join(carpeta_salida, "clip_%05d.mp4")
    vf = ("scale=1280:720:force_original_aspect_ratio=decrease,"
          "pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1")
    subprocess.run([
        "ffmpeg", "-y", "-i", origen,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-force_key_frames", f"expr:gte(t,n_forced*{CLIP_SECONDS})",
        "-f", "segment", "-segment_time", str(CLIP_SECONDS), "-reset_timestamps", "1",
        plantilla,
    ], check=True, capture_output=True)
    return sorted(glob.glob(os.path.join(carpeta_salida, "clip_*.mp4")))
