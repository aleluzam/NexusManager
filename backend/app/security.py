"""Primitivas de seguridad: hash de contraseñas (Argon2id), JWT, cookies,
generación/rotación de tokens de refresco y códigos de verificación."""

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import Settings, get_settings

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
# Códigos de verificación de un solo uso (6 dígitos)
# ---------------------------------------------------------------------------
#
# Flujos: "email_verification" | "password_reset". El discriminante `purpose`
# viaja en el hash, así que un mismo código sirve para ambos sin colisionar.
#
# CONTRATO DE HASH (fijado con database-admin, ver docstring de
# app.models.VerificationCode): el hash se calcula sobre el código YA
# NORMALIZADO (solo dígitos) e incluye el usuario como sal:
#
#     code_hash = sha256(f"{purpose}:{user_id}:{code_normalizado}").hexdigest()
#
# - El usuario actúa de sal: dos cuentas que reciben "042731" producen hashes
#   distintos, que es lo que hace correcto el UNIQUE global de `code_hash`
#   (6 dígitos = 1.000.000 de combinaciones, las colisiones serían constantes
#   sin sal).
# - Se verifica SIEMPRE por el par (purpose, code_hash), nunca en claro.
# - El código que se hashea es el NORMALIZADO por el usuario, no el que se
#   imprimió en el email: por eso normalizar es obligatorio antes de hashear.

VERIFICATION_CODE_LENGTH = 6
#: Separador solo para presentación en el email ("042 731"): como la
#: normalización descarta lo que no es dígito, el usuario puede teclear el
#: código con espacios, sin ellos o con guion.
VERIFICATION_CODE_DISPLAY_SEPARATOR = " "
_CODE_DIGITS_RE = re.compile(r"\D")


def generate_verification_code() -> str:
    """Devuelve un código de 6 dígitos como string, siempre con esa longitud
    ("042731", nunca "42731"): el relleno con ceros a la izquierda es lo que
    permite que el email, el log y el hash usen la misma representación."""
    return str(secrets.randbelow(10**VERIFICATION_CODE_LENGTH)).zfill(
        VERIFICATION_CODE_LENGTH
    )


def normalize_verification_code(raw: str) -> str:
    """Deja solo dígitos y recorta a 6: acepta "042731", "042 731", "042-731"
    o "abc042731xyz". Lo que no se puede reducir a 6 dígitos se considera
    inválido (ver `is_valid_verification_code`)."""
    return _CODE_DIGITS_RE.sub("", raw or "")[:VERIFICATION_CODE_LENGTH]


def is_valid_verification_code(raw: str) -> bool:
    """True solo si `raw` normaliza a exactamente 6 dígitos."""
    return len(normalize_verification_code(raw)) == VERIFICATION_CODE_LENGTH


def format_verification_code_for_display(code: str) -> str:
    """Agrupa en tercios para leerlo de un vistazo ("042731" -> "042 731").
    Solo presentación: el usuario puede teclear el código con o sin espacios."""
    return VERIFICATION_CODE_DISPLAY_SEPARATOR.join(
        code[i : i + 3] for i in range(0, len(code), 3)
    )


def hash_verification_code(purpose: str, user_id: str, code: str) -> str:
    """sha256 hex de f"{purpose}:{user_id}:{code}". `code` DEBE venir ya
    normalizado (ver normalize_verification_code) para que el hash sea
    reproducible entre el email recibido y lo que teclea el usuario."""
    return sha256_hex(f"{purpose}:{user_id}:{code}")


def new_verification_expiry(purpose: str, *, settings: Settings) -> datetime:
    """Vencimiento del código.

    `settings` es OBLIGATORIO y va explícito (y no `get_settings()`) por el
    mismo motivo que en `app.api.auth._issue_code_and_email`: el `lru_cache` de
    la configuración es global al proceso, así que leerlo desde aquí haría que
    `create_app(Settings(...))` — que es como los tests ajustan el TTL — no
    surtiera efecto, sin ningún aviso. Con el parámetro, la lectura del global es
    un error de firma y no un bug silencioso.

    `purpose` se acepta para poder diferenciar el TTL por flujo más adelante; hoy
    todos comparten `verification_code_ttl_minutes`."""
    del purpose  # hoy el TTL es único; se mantiene por contrato de firma
    return utcnow() + timedelta(minutes=settings.verification_code_ttl_minutes)


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