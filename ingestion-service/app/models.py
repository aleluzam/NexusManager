from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def ahora():
    return datetime.utcnow()


class Lote(Base):
    __tablename__ = "lotes"
    id: Mapped[int] = mapped_column(primary_key=True)
    # pendiente | procesando | listo | error
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    contexto_texto: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)


class Archivo(Base):
    __tablename__ = "archivos"
    id: Mapped[int] = mapped_column(primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey("lotes.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(10))  # video | doc
    nombre_original: Mapped[str] = mapped_column(String(255))
    ruta: Mapped[str] = mapped_column(String(500))
    tamano_bytes: Mapped[int] = mapped_column(Integer, default=0)


class Video(Base):
    __tablename__ = "videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey("lotes.id"), index=True)
    archivo_id: Mapped[int] = mapped_column(ForeignKey("archivos.id"))
    # pendiente | cortando | cortado | error
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    duracion_seg: Mapped[float | None] = mapped_column(Float)
    total_clips: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class Clip(Base):
    __tablename__ = "clips"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    indice: Mapped[int] = mapped_column(Integer)
    ruta: Mapped[str] = mapped_column(String(500))
    duracion_seg: Mapped[float | None] = mapped_column(Float)
    # pendiente | listo | error
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    ficha: Mapped[dict | None] = mapped_column(JSON)  # el JSON del clip (guion, tags, tono...)
    embedding = mapped_column(Vector(1024), nullable=True)  # 1024 = bge-m3
