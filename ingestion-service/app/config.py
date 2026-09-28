import os

API_KEY = os.environ.get("API_KEY", "cambia-esta-clave")
DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/storage")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "4096")) * 1024 * 1024
CLIP_SECONDS = int(os.environ.get("CLIP_SECONDS", "10"))

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
DOC_EXT = {".txt", ".docx"}
