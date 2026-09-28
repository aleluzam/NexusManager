"""Primitivas de seguridad: hash de contraseñas (Argon2id), JWT, cookies,
y generación/rotación de tokens de refresco y de reseteo."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import get_settings

# ---------------------------------------------------------------------------
# Helpers de tiempo
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    """UTC naive — SQLite devuelve datetimes sin tzinfo; se mantiene el
    estándar UTC naive para que todas las comparaciones sean consistentes
    entre bases de datos (en Postgres la columna es TIMESTAMP con tz)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Hash de contraseñas — Argon2id (OWASP-recommended)
# ---------------------------------------------------------------------------

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(password: str) -> str:
    # Argon2id no tiene límite de 72 bytes como bcrypt; validamos policy aparte.
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# ---------------------------------------------------------------------------
# Tokens de acceso (JWT, HS256, corta vida)
# ---------------------------------------------------------------------------

ALGORITHM = "HS256"


def create_access_token(user_id: str) -> tuple[str, int]:
    """Devuelve (token, expiry_unix). Claims: sub, type, iat, exp, jti."""
    settings = get_settings()
    ttl = timedelta(minutes=settings.access_token_minutes)
    now = utcnow()
    exp = now + ttl
    payload: dict[str, Any] = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": exp,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    return token, int(exp.timestamp())


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decodifica y valida un JWT de acceso. Devuelve None si es inválido."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp", "iat", "jti", "type"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    return payload


# ---------------------------------------------------------------------------
# Tokens de refresco (opacos, 256 bits, almacenados hasheados)
# ---------------------------------------------------------------------------

def generate_refresh_token() -> tuple[str, str]:
    """Devuelve (token_plano, token_hash_hex)."""
    token = secrets.token_urlsafe(48)  # 384 bits de entropía
    return token, sha256_hex(token)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_refresh_expiry(remember_me: bool) -> datetime:
    settings = get_settings()
    days = (
        settings.refresh_token_days_remember
        if remember_me
        else settings.refresh_token_days
    )
    return utcnow() + timedelta(days=days)


# ---------------------------------------------------------------------------
# Tokens de reseteo de contraseña
# ---------------------------------------------------------------------------

def generate_reset_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    return token, sha256_hex(token)


def new_reset_expiry() -> datetime:
    settings = get_settings()
    return utcnow() + timedelta(minutes=settings.reset_token_minutes)


# ---------------------------------------------------------------------------
# Cookies httpOnly para el refresh token
# ---------------------------------------------------------------------------

def _cookie_base_kwargs() -> dict[str, Any]:
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "key": settings.cookie_name,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/api/v1/auth",
    }
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    return kwargs


def build_refresh_cookie_kwargs(remember_me: bool) -> dict[str, Any]:
    """Parámetros para response.set_cookie()."""
    kwargs = _cookie_base_kwargs()
    if remember_me:
        kwargs["max_age"] = get_settings().refresh_token_days_remember * 24 * 3600
    # Sin max_age -> cookie de sesión (se elimina al cerrar el navegador).
    return kwargs


def build_refresh_cookie_delete_kwargs() -> dict[str, Any]:
    """Parámetros para response.delete_cookie() (sin max_age)."""
    return _cookie_base_kwargs()