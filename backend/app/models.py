"""Modelos ORM de autenticación."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(16), default="local", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    verification_codes: Mapped[list["VerificationCode"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # SHA-256 hex del token (nunca almacenamos el token en claro).
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remember_me: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class VerificationCode(Base):
    """Códigos numéricos de un solo uso (verificación de email, reseteo de contraseña).

    La tabla es unificada: el discriminante ``purpose`` separa los flujos
    ("email_verification" | "password_reset" | futuro "email_change").

    Unicidad de ``code_hash``: un código de 6 dígitos solo tiene 1.000.000 de
    combinaciones, así que dos usuarios distintos reciben el mismo código con
    probabilidad no despreciable. La restricción UNIQUE es global, NO por
    código en claro; es correcta porque el hash es *salado por usuario y
    propósito*. Contrato esperado de la capa de aplicación
    (``app/security.py``, ver ``hash_verification_code``):

        code_hash = sha256(f"{purpose}:{user_id}:{code_normalizado}".encode()).hexdigest()

    - ``code`` es el dígitos tal cual lo escribió el usuario tras normalizar
      (p. ej. "042 731" -> "042731"), nunca el que se le envió al email.
    - ``user_id`` (o el email asociado) actúa como sal, de modo que el mismo
      código para dos usuarios produce hashes distintos y la colisión global
      de UNIQUE no se disparan nunca.
    - Verificar SIEMPRE por ``(purpose, code_hash)``: nunca por código en claro
      ni aceptando el primer hash que coincida.

    Si alguna vez el hash dejara de depender del usuario, el UNIQUE global
    pasaría a ser un bug (IntegrityError en INSERT) y la app debería reintentar
    la generación; el modelo no debe "arreglarlo" con un UNIQUE compuesto,
    porque entonces un mismo usuario con dos purposes distintos podría colisionar
    y, sobre todo, se perdería la detección de reuso de hash.
    """

    __tablename__ = "verification_codes"
    __table_args__ = (
        # Cubre tanto la revocación de códigos previos de un usuario para un
        # propósito como el límite "N códigos por hora". No hace falta un
        # índice (user_id, purpose, created_at): created_at solo aparece en
        # ordenaciones/decisiones de la fila ya filtrada por este prefijo, y el
        # conjunto de filas vivas por par (user, purpose) es de 1 (se revoca al
        # emitir uno nuevo).
        Index("ix_verification_codes_user_id_purpose", "user_id", "purpose"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # "email_verification" | "password_reset" (futuro: "email_change")
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    # SHA-256 hex del código NORMALIZADO (ver nota de seguridad). Nunca el código en claro.
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="verification_codes")