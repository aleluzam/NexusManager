"""Configuración del servicio, leída del entorno.

Todos los módulos leen estos valores **al importarse**, no al arrancar. Por eso
las validaciones tienen que vivir aquí y no en el `lifespan` de FastAPI: si
`API_KEY` no vale, el fallo tiene que matar el proceso al importar, no aparecer
a mitad de un lote cuando alguien ya ha subido un vídeo.
"""
import logging
import os

#: Valor de ejemplo que estaba publicado en el repo. Si llega al entorno, el
#: servicio arranca con una clave que cualquiera que lea el código conoce.
API_KEY_EJEMPLO = "cambia-esta-clave"


def _api_key() -> str:
    """Devuelve la API key o aborta el arranque.

    Mismo criterio que `validate_for_production` del backend principal: RuntimeError
    con un mensaje que dice qué hacer, no un warning que se puede ignorar.
    """
    key = (os.environ.get("API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "API_KEY no está definida. Copia .env.example a .env y pon una clave "
            'real, por ejemplo: python -c "import secrets; '
            'print(secrets.token_urlsafe(32))"'
        )
    if key == API_KEY_EJEMPLO:
        raise RuntimeError(
            f"API_KEY sigue con el valor de ejemplo {API_KEY_EJEMPLO!r}, que está "
            "publicado en el repositorio: cualquiera que lo lea puede llamar a la "
            "API. Genera una clave real."
        )
    return key


def configurar_logging() -> None:
    """Logging a stderr en los dos procesos (api y worker).

    Uvicorn corre en el 8000 y Dramatiq en el worker, y son procesos distintos,
    así que la configuración se llama desde los dos puntos de entrada en vez de
    dejar que la configure uno solo. `force=True` evita líneas duplicadas si
    acaba de llamarse dos veces.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


API_KEY = _api_key()
DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/storage")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "4096")) * 1024 * 1024
CLIP_SECONDS = int(os.environ.get("CLIP_SECONDS", "10"))
#: Cache de modelos de HuggingFace. NO la lee ningún código de este servicio:
#: faster-whisper y transformers leen la variable de entorno por su cuenta. Se
#: declara aquí solo para que quede visible su valor; lo que de verdad la fija es
#: el bloque `environment:` de docker-compose.yml, que pisa al `env_file`.
HF_HOME = os.environ.get("HF_HOME", "")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
DOC_EXT = {".txt", ".docx"}
