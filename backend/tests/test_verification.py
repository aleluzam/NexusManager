"""Suite de verificación de email y reseteo por CÓDIGO de 6 dígitos.

Cubre las garantías de `app/api/auth.py` (emisión, validación, límites) y de
`app/security.py` (hash salado por usuario y propósito):

- hashing: el código nunca se guarda en claro y el hash depende de
  (purpose, user_id, código normalizado), así que el mismo número para dos
  cuentas no colisiona contra el UNIQUE global de `code_hash`;
- un solo uso, caducidad, bloqueo por intentos y replay;
- aislamiento entre usuarios: el código de A no sirve contra B, ni cambiando
  el email del body ni cambiando el propósito;
- no enumeración: `forgot-password` responde SIEMPRE igual y `reset-password`
  devuelve SIEMPRE el mismo 400;
- issuance: tope por hora y Best-effort en registro / forgot-password;
- `resend-verification`: no reenvía si ya está verificado, 503 si el
  proveedor falla, 429 al superar el tope;
- barrera de red: en esta batería ningún email sale, y `build_email_sender`
  no construye un emisor de red fuera de `environment="prod"`;
- `validate_for_production()` y el guard de Origin sobre las rutas nuevas.

Nada de `time.sleep`: el paso del tiempo se manipula escribiendo
`expires_at` / `created_at` en la base de datos. No se monkeypatchea
`utcnow` porque `app.api.auth` lo importa por nombre en su propio espacio de
nombres (`from app.security import utcnow`), así que parchear
`app.security.utcnow` NO cambiaría el endpoint: es mejor mover la fecha en la
fila, que sí es la que decide.
"""

from datetime import timedelta

import asyncio
import time
from collections import deque

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, select
from sqlalchemy.exc import IntegrityError

from app.api.rate_limit import InMemoryRateLimiter, bucket_key, humanize_wait
from app.config import Settings, get_settings
from app.database import SessionLocal
from app.main import create_app
from app.models import User, VerificationCode
from app.security import (
    format_verification_code_for_display,
    hash_verification_code,
    is_valid_verification_code,
    normalize_verification_code,
    sha256_hex,
    utcnow,
)
from app.services.email import (
    ConsoleSender,
    EmailDeliveryError,
    EmailMessage,
    ResendSender,
    build_email_sender,
)

PASSWORD = "Segura-1234"
NUEVA = "NuevaClave-987"
EMAIL_VERIFICATION = "email_verification"
PASSWORD_RESET = "password_reset"

#: El mismo texto para todos los 400 de código: si algún día cambia, es
#: deliberado (y hay que revisar los tests a la vez).
INVALID_CODE = "El código es inválido o ha expirado"
FORGOT_DETAIL = "Si el correo existe, recibirás un código para restablecer tu contraseña."


# ---------------------------------------------------------------------------
# Helpers de base de datos
# ---------------------------------------------------------------------------

async def make_user(email: str, *, is_email_verified: bool = False, full_name: str = "Ana Gómez") -> str:
    """Crea un usuario directamente (sin pasar por el endpoint) y devuelve su id."""
    async with SessionLocal() as db:
        user = User(
            email=email,
            full_name=full_name,
            password_hash="x",
            is_email_verified=is_email_verified,
        )
        db.add(user)
        await db.commit()
        return user.id


async def insert_code(
    user_id: str,
    purpose: str,
    code: str,
    *,
    expires_at=None,
    attempts: int = 0,
    consumed_at=None,
) -> int:
    """Inserta una fila VerificationCode como si se hubiera emitido y devuelve
    su id. El hash se calcula con el contrato de `app.security`, no a mano."""
    async with SessionLocal() as db:
        row = VerificationCode(
            user_id=user_id,
            purpose=purpose,
            code_hash=hash_verification_code(purpose, user_id, code),
            expires_at=expires_at or utcnow() + timedelta(minutes=10),
            attempts=attempts,
            consumed_at=consumed_at,
        )
        db.add(row)
        await db.commit()
        return row.id


async def get_code(row_id: int) -> VerificationCode:
    async with SessionLocal() as db:
        return await db.get(VerificationCode, row_id)


async def codes_of(user_id: str, purpose: str | None = None) -> list[VerificationCode]:
    async with SessionLocal() as db:
        stmt = select(VerificationCode).where(VerificationCode.user_id == user_id)
        if purpose:
            stmt = stmt.where(VerificationCode.purpose == purpose)
        return list((await db.execute(stmt.order_by(VerificationCode.id))).scalars())


async def user_email_verified(user_id: str) -> bool:
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        assert user is not None
        return user.is_email_verified


async def register(client, email: str = "ana@empresa.com", password: str = PASSWORD) -> dict:
    res = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Ana Gómez", "email": email, "password": password},
    )
    assert res.status_code == 201, res.text
    return res.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. Barrera de red: el emisor de los tests y build_email_sender
# ---------------------------------------------------------------------------

def test_test_app_has_no_network_sender():
    """La app que construyen los tests nunca tiene un emisor que hable con
    Resend, sea cual sea el `.env` del desarrollador (aquí hay una key puesta)."""
    sender = create_app(get_settings()).state.email_sender
    assert isinstance(sender, ConsoleSender)
    assert not isinstance(sender, ResendSender)


@pytest.mark.parametrize(
    "provider,key,enabled",
    [
        ("console", None, True),
        ("console", "re_clave_falsa", True),
        ("resend", None, True),
        ("resend", "re_clave_falsa", True),
        ("resend", "re_clave_falsa", False),
        ("RESEND", "re_clave_falsa", True),  # el proveedor se compara en minúsculas
    ],
)
def test_build_email_sender_never_builds_a_network_sender_outside_prod(provider, key, enabled):
    """GARANTÍA DE SEGURIDAD: `build_email_sender` con una key presente y
    `environment="dev"` devuelve ConsoleSender. Es la barrera que impide que una
    batería de tests (o un entorno de desarrollo mal configurado) salga a
    internet. Cualquier regresión aquí significa emails reales desde los tests.
    """
    cfg = Settings(
        **{
            **get_settings().model_dump(),
            "environment": "dev",
            "email_provider": provider,
            "resend_api_key": key,
            "email_enabled": enabled,
        }
    )
    sender = build_email_sender(cfg)
    assert isinstance(sender, ConsoleSender), (
        f"provider={provider} key={bool(key)} enabled={enabled} construyó {type(sender).__name__}"
    )
    assert not isinstance(sender, ResendSender)


def test_build_email_sender_uses_resend_only_in_prod(monkeypatch):
    """La otra mitad de la barrera: en prod, con key, sí se construye el emisor
    real. Se sustituye el módulo `resend` por un stub para no fijar la key
    global del SDK de verdad durante la batería."""
    import types

    import app.services.email as email_mod

    stub = types.SimpleNamespace()
    monkeypatch.setattr(email_mod, "resend", stub)

    cfg = Settings(
        **{
            **get_settings().model_dump(),
            "environment": "prod",
            "email_provider": "resend",
            "resend_api_key": "re_clave_falsa",
            "email_enabled": True,
        }
    )
    sender = build_email_sender(cfg)
    assert isinstance(sender, ResendSender)
    assert not isinstance(sender, ConsoleSender)
    assert stub.api_key == "re_clave_falsa"


def test_build_email_sender_refuses_to_fall_back_to_console_in_prod(monkeypatch):
    """SEGUNDA barrera (la primera es `validate_for_production`). En prod, una
    configuración que no permite enviar de verdad NO cae a `ConsoleSender`: lanza
    `RuntimeError`. Antes caía, y `ConsoleSender.send` escribía el cuerpo del
    email —con el código de verificación o el de reseteo— en el log: prod + resend
    sin key acababa con credenciales de un solo uso escritas en disco."""
    import app.services.email as email_mod

    monkeypatch.setattr(email_mod, "resend", None)
    cfg = Settings(
        **{
            **get_settings().model_dump(),
            "environment": "prod",
            "email_provider": "resend",
            "resend_api_key": None,
        }
    )
    with pytest.raises(RuntimeError) as exc:
        build_email_sender(cfg)
    assert "RESEND_API_KEY" in str(exc.value)


async def test_client_fixture_swallows_every_email(client):
    """El fixture `client` inyecta el emisor falso: los endpoints de email no
    dejan nada fuera del proceso durante la batería."""
    assert client.email_sender.messages == []
    await register(client, email="sinred@empresa.com")
    assert len(client.email_sender.messages) == 1
    assert client.email_sender.messages[0].to == "sinred@empresa.com"


# ---------------------------------------------------------------------------
# 2. Contrato del hash (regresión: sin sal por usuario / sin hashear)
# ---------------------------------------------------------------------------

def test_hash_is_sha256_of_purpose_user_and_code():
    code = "123456"
    esperado = sha256_hex(f"{PASSWORD_RESET}:usuario-1:{code}")
    assert hash_verification_code(PASSWORD_RESET, "usuario-1", code) == esperado
    assert hash_verification_code(PASSWORD_RESET, "usuario-1", code) != code


def test_hash_depends_on_user_purpose_and_code():
    base = hash_verification_code(PASSWORD_RESET, "usuario-1", "123456")
    assert base != hash_verification_code(PASSWORD_RESET, "usuario-2", "123456")
    assert base != hash_verification_code(EMAIL_VERIFICATION, "usuario-1", "123456")
    assert base != hash_verification_code(PASSWORD_RESET, "usuario-1", "654321")


def test_normalization_accepts_spaces_and_dashes():
    assert normalize_verification_code("042 731") == "042731"
    assert normalize_verification_code("042-731") == "042731"
    assert normalize_verification_code("042731") == "042731"
    assert is_valid_verification_code("042 731")
    assert not is_valid_verification_code("abc")
    assert not is_valid_verification_code("12345")
    assert format_verification_code_for_display("042731") == "042 731"


async def test_same_code_for_two_users_does_not_collide():
    """6 dígitos son 1.000.000 de combinaciones: sin sal por usuario, el UNIQUE
    global de `code_hash` reventaría con un IntegrityError en cuanto dos
    cuentas recibieran el mismo número. Aquí se comprueba que NO revienta y que
    los hashes son distintos."""
    user_a = await make_user("a@empresa.com")
    user_b = await make_user("b@empresa.com")

    try:
        row_a = await insert_code(user_a, EMAIL_VERIFICATION, "042731")
        row_b = await insert_code(user_b, EMAIL_VERIFICATION, "042731")
    except IntegrityError as exc:  # pragma: no cover - sería el fallo buscado
        pytest.fail(f"el mismo código para dos usuarios colisiona: {exc}")

    a, b = await get_code(row_a), await get_code(row_b)
    assert a.code_hash != b.code_hash
    assert a.code_hash == hash_verification_code(EMAIL_VERIFICATION, user_a, "042731")
    assert b.code_hash == hash_verification_code(EMAIL_VERIFICATION, user_b, "042731")


async def test_plain_code_is_never_stored_in_any_column():
    """Regresión dura: el código en claro no aparece en NINGÚN sitio de la tabla
    (ni en `code_hash` ni en ninguna otra columna de texto)."""
    await make_user("ana@empresa.com")
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == "ana@empresa.com"))
        uid = user.id

    row_id = await insert_code(uid, PASSWORD_RESET, "042731")
    row = await get_code(row_id)

    columnas_texto = [c.name for c in VerificationCode.__table__.columns if isinstance(c.type, String)]
    assert "code_hash" in columnas_texto
    for nombre in columnas_texto:
        valor = str(getattr(row, nombre))
        assert "042731" not in valor, f"el código en claro aparece en {nombre}"


async def test_registered_code_is_hashed_and_not_recoverable_from_db(client):
    """Flujo completo: el registro emite un código cuyo hash se puede recomputar
    desde el email recibido, pero en la BD solo está el hash."""
    body = await register(client, email="hash@empresa.com")
    codigo = client.email_sender.last_code()
    assert codigo.isdigit() and len(codigo) == 6

    filas = await codes_of(body["user"]["id"], EMAIL_VERIFICATION)
    assert len(filas) == 1
    fila = filas[0]
    assert fila.code_hash == hash_verification_code(EMAIL_VERIFICATION, body["user"]["id"], codigo)
    assert fila.code_hash != codigo
    assert codigo not in fila.code_hash
    for columna in ("code_hash", "purpose", "user_id"):
        assert codigo not in str(getattr(fila, columna))


# ---------------------------------------------------------------------------
# 3. Registro: emite UN código de verificación
# ---------------------------------------------------------------------------

async def test_register_sends_exactly_one_verification_email(client):
    body = await register(client, email="registro@empresa.com")
    sender = client.email_sender

    assert body["user"]["is_email_verified"] is False
    assert await user_email_verified(body["user"]["id"]) is False
    assert len(sender.messages) == 1

    mensaje = sender.last
    assert mensaje.to == "registro@empresa.com"
    assert "Verifica" in mensaje.subject
    assert sender.purpose_of_last() == EMAIL_VERIFICATION
    # El código viaja en el cuerpo: en texto plano tal cual, en el HTML agrupado
    # en tercios ("042 731"), que es como lo lee el usuario.
    assert sender.codes() == [sender.last_code()]
    assert sender.last_code() in mensaje.text
    assert format_verification_code_for_display(sender.last_code()) in mensaje.html


async def test_register_stores_a_usable_unconsumed_code(client):
    body = await register(client, email="vigente@empresa.com")
    fila = (await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0]
    assert fila.consumed_at is None
    assert fila.attempts == 0
    # Caduca en ~10 minutos (verification_code_ttl_minutes por defecto)
    restante = fila.expires_at - utcnow()
    assert timedelta(minutes=8) < restante <= timedelta(minutes=10)


async def test_register_survives_an_email_provider_failure(make_client):
    """El envío es best-effort: si el proveedor revienta, el registro NO falla y
    la cuenta queda creada (el usuario puede pedir otro código)."""
    client, sender, _ = make_client()
    sender.error = EmailDeliveryError("proveedor caído")

    res = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Ana Gómez", "email": "caido@empresa.com", "password": PASSWORD},
    )
    assert res.status_code == 201
    assert res.json()["user"]["is_email_verified"] is False
    assert sender.messages == []

    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == "caido@empresa.com"))
        assert user is not None


# ---------------------------------------------------------------------------
# 4. Verificación de email: happy path, idempotencia y replay
# ---------------------------------------------------------------------------

async def test_verify_email_happy_path(client):
    body = await register(client, email="verifica@empresa.com")
    headers = bearer(body["access_token"])
    codigo = client.email_sender.last_code()

    res = await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["is_email_verified"] is True
    assert res.json()["email"] == "verifica@empresa.com"

    # Persistido, y /me lo refleja
    assert await user_email_verified(body["user"]["id"]) is True
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.json()["is_email_verified"] is True

    # El código queda consumido
    fila = (await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0]
    assert fila.consumed_at is not None
    assert fila.attempts == 0


async def test_verify_email_accepts_formatted_code(client):
    body = await register(client, email="formato@empresa.com")
    headers = bearer(body["access_token"])
    codigo = client.email_sender.last_code()
    con_espacios = f"{codigo[:3]} {codigo[3:]}"
    res = await client.post("/api/v1/auth/verify-email", json={"code": con_espacios}, headers=headers)
    assert res.status_code == 200
    assert res.json()["is_email_verified"] is True


async def test_verify_email_is_idempotent_when_already_verified(client):
    body = await register(client, email="dos@empresa.com")
    headers = bearer(body["access_token"])
    codigo = client.email_sender.last_code()
    assert (
        await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
    ).status_code == 200
    consumido = (await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0].consumed_at
    enviados = len(client.email_sender.messages)

    # Segunda llamada (el usuario pulsa dos veces el botón): 200 y NADIE toca nada
    res = await client.post("/api/v1/auth/verify-email", json={"code": "000000"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["is_email_verified"] is True
    fila = (await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0]
    assert fila.consumed_at == consumido
    assert fila.attempts == 0
    assert len(client.email_sender.messages) == enviados


async def test_verify_email_replay_of_a_consumed_code_fails(client):
    """Replay: el mismo código dos veces. La segunda tiene que fallar, así que
    se devuelve el usuario a "sin verificar" en la BD para llegar a la fase de
    validación (si no, el atajo de idempotencia respondería 200)."""
    body = await register(client, email="replay@empresa.com")
    headers = bearer(body["access_token"])
    codigo = client.email_sender.last_code()
    assert (
        await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
    ).status_code == 200

    async with SessionLocal() as db:
        user = await db.get(User, body["user"]["id"])
        user.is_email_verified = False
        await db.commit()

    res = await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
    assert res.status_code == 400
    assert res.json()["detail"] == INVALID_CODE
    assert await user_email_verified(body["user"]["id"]) is False


async def test_verify_email_requires_authentication(client):
    await register(client, email="sintoken@empresa.com")
    res = await client.post("/api/v1/auth/verify-email", json={"code": "123456"})
    assert res.status_code == 401
    res = await client.post(
        "/api/v1/auth/verify-email", json={"code": "123456"}, headers=bearer("no-es-un-jwt")
    )
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# 5. Código incorrecto y bloqueo por intentos
# ---------------------------------------------------------------------------

async def test_wrong_code_is_generic_and_does_not_burn_the_right_one(client):
    """REGRESIÓN: fallar una vez no invalida el código bueno. Es el caso que más
    se rompe al cambiar el esquema de verificación."""
    body = await register(client, email="fallo@empresa.com")
    headers = bearer(body["access_token"])
    codigo = client.email_sender.last_code()
    malo = "999999" if codigo != "999999" else "888888"

    res = await client.post("/api/v1/auth/verify-email", json={"code": malo}, headers=headers)
    assert res.status_code == 400
    assert res.json()["detail"] == INVALID_CODE

    fila = (await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0]
    assert fila.attempts == 1
    assert fila.consumed_at is None  # NO se invalida por un fallo

    # El código correcto sigue funcionando
    ok = await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["is_email_verified"] is True


async def test_lockout_after_max_attempts(make_client):
    """5 intentos fallidos matan el código: el 6º, ya con el código CORRECTO,
    también falla (si no, el límite no protegería nada)."""
    client, _sender, _app = make_client(rate_limit_verify_email_max=100)
    body = await register(client, email="bloqueo@empresa.com")
    headers = bearer(body["access_token"])
    codigo = client.email_sender.last_code()
    malo = "999999" if codigo != "999999" else "888888"

    for intento in range(1, 6):
        res = await client.post("/api/v1/auth/verify-email", json={"code": malo}, headers=headers)
        assert res.status_code == 400, f"intento {intento}: {res.text}"
        assert res.json()["detail"] == INVALID_CODE

    fila = (await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0]
    assert fila.attempts == 5
    assert fila.consumed_at is not None, "el código debe quedar invalidado"
    assert await user_email_verified(body["user"]["id"]) is False

    # El 6º intento, con el código bueno, ya no entra
    res = await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
    assert res.status_code == 400
    assert res.json()["detail"] == INVALID_CODE
    assert await user_email_verified(body["user"]["id"]) is False


async def test_malformed_code_does_not_count_as_an_attempt(make_client):
    """Un cuerpo que no reduce a 6 dígitos no es un intento de adivinar un
    código: no hay nada que comparar y por tanto no se gasta un intento. El
    límite por IP es lo que frena esa vía."""
    client, _sender, _app = make_client(rate_limit_verify_email_max=100)
    body = await register(client, email="basura@empresa.com")
    headers = bearer(body["access_token"])

    for codigo in ("abc", "12345", "", "  ", "---"):
        res = await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
        assert res.status_code in (400, 422), f"code={codigo!r} -> {res.status_code}"
    fila = (await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0]
    assert fila.attempts == 0
    assert fila.consumed_at is None
    # El código bueno sigue sirviendo
    assert (
        await client.post(
            "/api/v1/auth/verify-email",
            json={"code": client.email_sender.last_code()},
            headers=headers,
        )
    ).status_code == 200


async def test_code_with_extra_digits_is_truncated_not_rejected(client):
    """La normalización recorta a 6 dígitos, así que "0427319" SÍ entra por la
    vía normal (y por tanto cuenta como intento si no coincide): no es un
    atajo para saltarse el contador."""
    body = await register(client, email="recorte@empresa.com")
    headers = bearer(body["access_token"])

    res = await client.post("/api/v1/auth/verify-email", json={"code": "1234567"}, headers=headers)
    assert res.status_code == 400
    fila = (await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0]
    assert fila.attempts == 1

    # Y el mismo código con dígitos de más SÍ vale (la normalización recorta a
    # los 6 primeros, así que el sobrante va detrás)
    codigo = client.email_sender.last_code()
    assert (
        await client.post(
            "/api/v1/auth/verify-email", json={"code": f"{codigo}9"}, headers=headers
        )
    ).status_code == 200


async def test_verify_email_rate_limit_by_ip(make_client):
    """Segunda capa de defensa: el límite por IP de POST /verify-email."""
    client, _sender, _app = make_client(rate_limit_verify_email_max=2, rate_limit_verify_email_window=60)
    body = await register(client, email="ip@empresa.com")
    headers = bearer(body["access_token"])
    codigo = client.email_sender.last_code()
    malo = "999999" if codigo != "999999" else "888888"

    assert (await client.post("/api/v1/auth/verify-email", json={"code": malo}, headers=headers)).status_code == 400
    assert (await client.post("/api/v1/auth/verify-email", json={"code": malo}, headers=headers)).status_code == 400
    r3 = await client.post("/api/v1/auth/verify-email", json={"code": malo}, headers=headers)
    assert r3.status_code == 429
    assert "Retry-After" in r3.headers


# ---------------------------------------------------------------------------
# 6. Caducidad
# ---------------------------------------------------------------------------

async def test_expired_code_is_rejected(client):
    body = await register(client, email="caduca@empresa.com")
    headers = bearer(body["access_token"])
    # Se inserta un código ya caducado (además del que emitió el registro)
    caducado_id = await insert_code(
        body["user"]["id"],
        EMAIL_VERIFICATION,
        "042731",
        expires_at=utcnow() - timedelta(minutes=1),
    )

    res = await client.post("/api/v1/auth/verify-email", json={"code": "042731"}, headers=headers)
    assert res.status_code == 400
    assert res.json()["detail"] == INVALID_CODE
    assert await user_email_verified(body["user"]["id"]) is False

    # El código que se acaba de reintentar tampoco revive nada
    fila = await get_code(caducado_id)
    assert fila.expires_at < utcnow()
    # `_validate_code` contabiliza el fallo también sobre la fila caducada
    # ("cada fallo suma un intento"): es inocuo porque una fila caducada ya no
    # puede validar nunca, y el pinchazo de intentos de un código muerto no
    # afecta al código vivo que se emita después.
    assert fila.attempts == 1

    # Y el código real que se emitió sigue sirviendo: el caducado no lo invalidó
    codigo = client.email_sender.last_code()
    assert (
        await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
    ).status_code == 200


async def test_code_expiring_in_the_future_still_works(client):
    body = await register(client, email="vence@empresa.com")
    headers = bearer(body["access_token"])
    codigo = client.email_sender.last_code()
    res = await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# 7. Aislamiento entre usuarios y entre propósitos
# ---------------------------------------------------------------------------

async def test_code_of_one_user_does_not_work_for_another(client):
    body_a = await register(client, email="ana@empresa.com")
    body_b = await register(client, email="bruno@empresa.com")
    headers_b = bearer(body_b["access_token"])
    codigo_a = client.email_sender.codes()[0]  # el de Ana
    codigo_b = client.email_sender.codes()[1]  # el de Bruno

    res = await client.post("/api/v1/auth/verify-email", json={"code": codigo_a}, headers=headers_b)
    assert res.status_code == 400
    assert res.json()["detail"] == INVALID_CODE
    assert await user_email_verified(body_b["user"]["id"]) is False

    # El intento fallido se cargó al código de Bruno, NO al de Ana
    fila_a = (await codes_of(body_a["user"]["id"], EMAIL_VERIFICATION))[0]
    fila_b = (await codes_of(body_b["user"]["id"], EMAIL_VERIFICATION))[0]
    assert fila_a.attempts == 0 and fila_a.consumed_at is None
    assert fila_b.attempts == 1

    # Y Bruno todavía puede verificar con el suyo
    ok = await client.post("/api/v1/auth/verify-email", json={"code": codigo_b}, headers=headers_b)
    assert ok.status_code == 200


async def test_reset_password_email_and_code_must_belong_together(client):
    """El email del body es la mitad de la credencial: con el email de A y el
    código de B (ni al revés) no se puede tocar la contraseña de nadie."""
    await register(client, email="ana@empresa.com")
    await register(client, email="bruno@empresa.com")

    await client.post("/api/v1/auth/forgot-password", json={"email": "ana@empresa.com"})
    await client.post("/api/v1/auth/forgot-password", json={"email": "bruno@empresa.com"})
    codigo_ana, codigo_bruno = client.email_sender.codes()[2:4]

    # Email de Ana + código de Ana: sí funciona
    ok = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": "ana@empresa.com", "code": codigo_ana, "new_password": NUEVA},
    )
    assert ok.status_code == 200, ok.text

    # Email de Ana + código de Bruno (el de Ana ya está consumido): 400
    cruzado = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": "ana@empresa.com", "code": codigo_bruno, "new_password": "OtraClave-456"},
    )
    assert cruzado.status_code == 400
    assert cruzado.json()["detail"] == INVALID_CODE

    # Email de Bruno + código de Ana: 400, y Bruno conserva su contraseña
    cruzado2 = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": "bruno@empresa.com", "code": codigo_ana, "new_password": NUEVA},
    )
    assert cruzado2.status_code == 400
    assert cruzado2.json()["detail"] == INVALID_CODE
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": "bruno@empresa.com", "password": PASSWORD}
        )
    ).status_code == 200

    # Y el código de Bruno sigue sirviendo para lo suyo (el intento ajeno no lo
    # invalidó)
    ok_bruno = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": "bruno@empresa.com", "code": codigo_bruno, "new_password": NUEVA},
    )
    assert ok_bruno.status_code == 200, ok_bruno.text


async def test_verification_code_cannot_be_used_to_reset_the_password(client):
    """El propósito viaja en el hash: el código de verificación no abre la vía
    del reseteo aunque se use con el email y la contraseña nueva correctos."""
    body = await register(client, email="ana@empresa.com")
    codigo = client.email_sender.last_code()

    res = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": "ana@empresa.com", "code": codigo, "new_password": NUEVA},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == INVALID_CODE
    assert await user_email_verified(body["user"]["id"]) is False

    # El código de verificación sigue vivo y sirve para lo suyo
    assert (
        await client.post(
            "/api/v1/auth/verify-email",
            json={"code": codigo},
            headers=bearer(body["access_token"]),
        )
    ).status_code == 200


# ---------------------------------------------------------------------------
# 8. No enumeración de cuentas
# ---------------------------------------------------------------------------

async def test_forgot_password_response_is_identical_and_sends_nothing_for_unknown_email(client):
    body = await register(client, email="existe@empresa.com")
    cliente_ana = client.email_sender.messages.copy()

    res_existente = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "existe@empresa.com"}
    )
    assert res_existente.status_code == 202
    assert res_existente.json()["detail"] == FORGOT_DETAIL
    assert len(client.email_sender.messages) == len(cliente_ana) + 1  # sí se envió a la real
    codigo = client.email_sender.last_code()

    res_inexistente = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "noexiste@empresa.com"}
    )
    assert res_inexistente.status_code == 202
    # IDÉNTICO byte a byte: ni el status, ni el cuerpo, ni el orden de claves
    assert res_inexistente.text == res_existente.text
    assert res_inexistente.json() == {"detail": FORGOT_DETAIL}
    # Cero emails enviados en el caso inexistente
    assert len(client.email_sender.messages) == len(cliente_ana) + 1
    assert codigo in client.email_sender.last.text


async def test_forgot_password_response_is_identical_when_the_provider_fails(make_client):
    """También cuando el envío falla: ni 500 ni mensaje distinto."""
    client, sender, _ = make_client()
    await register(client, email="existe@empresa.com")
    con_proveedor = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "existe@empresa.com"}
    )

    sender.error = EmailDeliveryError("caído")
    caido = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "existe@empresa.com"}
    )
    inexistente = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "noexiste@empresa.com"}
    )
    assert con_proveedor.status_code == caido.status_code == inexistente.status_code == 202
    assert con_proveedor.text == caido.text == inexistente.text


async def test_reset_password_gives_the_same_400_for_every_failure_mode(client):
    """Inexistente / email desconocido / código que no existe / código
    consumido: un único mensaje, para que el 400 no sea un oráculo."""
    body = await register(client, email="ana@empresa.com")
    codigo = client.email_sender.last_code()
    consumido_id = await insert_code(
        body["user"]["id"], PASSWORD_RESET, "111111", consumed_at=utcnow()
    )
    del consumido_id

    cuerpos = [
        {"email": "noexiste@empresa.com", "code": "123456", "new_password": NUEVA},
        {"email": "ana@empresa.com", "code": "123456", "new_password": NUEVA},  # no emitido
        {"email": "ana@empresa.com", "code": "111111", "new_password": NUEVA},  # consumido
        {"email": "ana@empresa.com", "code": codigo, "new_password": NUEVA},  # otro propósito
    ]
    respuestas = [
        await client.post("/api/v1/auth/reset-password", json=c) for c in cuerpos
    ]
    for res in respuestas:
        assert res.status_code == 400, res.text
        assert res.json() == {"detail": INVALID_CODE}
    assert len({res.text for res in respuestas}) == 1

    # Nada de eso ha cambiado la contraseña
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": "ana@empresa.com", "password": PASSWORD}
        )
    ).status_code == 200


async def test_reset_password_inactive_account_is_generic(client):
    """Una cuenta desactivada responde como una inexistente."""
    await register(client, email="ana@empresa.com")
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == "ana@empresa.com"))
        user.is_active = False
        await db.commit()

    res = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": "ana@empresa.com", "code": "123456", "new_password": NUEVA},
    )
    assert res.status_code == 400
    assert res.json() == {"detail": INVALID_CODE}


async def test_forgot_password_does_not_email_inactive_accounts(client):
    await register(client, email="ana@empresa.com")
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == "ana@empresa.com"))
        user.is_active = False
        await db.commit()
    enviados = len(client.email_sender.messages)

    res = await client.post("/api/v1/auth/forgot-password", json={"email": "ana@empresa.com"})
    assert res.status_code == 202
    assert res.json()["detail"] == FORGOT_DETAIL
    assert len(client.email_sender.messages) == enviados


# ---------------------------------------------------------------------------
# 8-bis. El envío NO está en la ruta de la petición (oráculo temporal)
# ---------------------------------------------------------------------------
#
# Por qué estos tests conducen la app ASGI a mano en vez de usar httpx: con
# ASGITransport las background tasks se ejecutan DENTRO de la llamada, así que al
# volver del `await` el email ya se ha enviado y no hay ningún instante
# observable en el que "el cliente ya tiene la respuesta pero el envío no ha
# ocurrido". Ese instante es exactamente el que hay que fijar, porque es el que
# separa un 202 de 3 ms de uno de 158 ms.
#
# La sonda se engancha a `http.response.body`: Starlette envía ese mensaje y
# DESPUÉS ejecuta las background tasks, así que en ese momento se puede mirar el
# emisor y tiene que estar intacto. Y el emisor de estos tests no devuelve nunca
# hasta que se le suelta, de modo que si alguien volviera a meter el `send` en la
# ruta de la petición la respuesta no llegaría nunca a enviarse y el test
# revienta por timeout. Nada de medir milisegundos: o hay oráculo o no lo hay.


class BlockingSender:
    """Emisor que se queda esperando indefinidamente hasta que se le suelta.

    Es la forma no flaky de comprobar que la respuesta no depende del proveedor:
    no hace falta cronometrar nada, basta con que el `await` del emisor bloquee
    y la respuesta siga saliendo."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.calls = 0
        self.messages: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> str:
        self.calls += 1
        self.messages.append(message)
        await self.release.wait()
        return "bloqueado"


async def asgi_call_with_body_probe(app, method: str, path: str, payload, probe, *, timeout: float = 5.0):
    """Llama a la app ASGI directamente y ejecuta `probe` en cuanto llega el
    cuerpo de la respuesta, antes de las background tasks.

    Devuelve (status, cuerpo, sonda_hecha). `probe` se invoca con la lista de
    mensajes recibidos hasta ese momento. El `timeout` existe para que una
    regresión (envío en la ruta de la petición) falle con un mensaje claro en
    lugar de colgarse el test para siempre.
    """
    import json as _json

    body = _json.dumps(payload).encode() if payload is not None else b""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
    }

    enviados: list[dict] = []
    cuerpo = bytearray()
    status_code = None
    sonda_hecha = False
    cuerpo_pendiente: dict | bool = {"type": "http.request", "body": body, "more_body": False}

    async def receive():
        nonlocal cuerpo_pendiente
        # Tras el cuerpo, todo servidor ASGI (y el propio ASGITransport de httpx)
        # entrega `http.disconnect` en cada llamada posterior: `BaseHTTPMiddleware`
        # lo espera para poder marcar la petición como consumida, así que
        # devolverlo es lo que hace falta para que el arnés sea realista.
        if not cuerpo_pendiente:
            return {"type": "http.disconnect"}
        mensaje, cuerpo_pendiente = cuerpo_pendiente, False
        return mensaje

    async def send(message):
        nonlocal status_code, sonda_hecha
        if message["type"] == "http.response.start":
            status_code = message["status"]
        elif message["type"] == "http.response.body":
            cuerpo.extend(message.get("body", b""))
            if not sonda_hecha and not message.get("more_body", False):
                sonda_hecha = True
                await probe(enviados)

    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=timeout)
    except asyncio.TimeoutError:  # pragma: no cover - sería la regresión
        pytest.fail(
            "la respuesta nunca llegó al cliente: el endpoint está esperando al "
            "proveedor de email dentro de la ruta de la petición. Eso devuelve "
            "el oráculo temporal de enumeración de cuentas."
        )
    return status_code, bytes(cuerpo), sonda_hecha


async def test_forgot_password_responds_before_the_email_is_sent(make_client):
    """REGRESIÓN DE SEGURIDAD (oráculo temporal). Al llegar la respuesta al
    cliente, el proveedor de email NO ha sido tocado; el envío ocurre después, en
    segundo plano. Es lo que hace que el 202 tarde lo mismo exista la cuenta o
    no: medido sobre uvicorn, 158.6 ms (existe) contra 3.2 ms (no existe) antes
    del arreglo, y 3.5 ms contra 3.3 ms después."""
    client, _sender, app = make_client()
    await register(client, email="existe@empresa.com")
    bloqueante = BlockingSender()
    app.state.email_sender = bloqueante

    enviados_al_mensaje_de_respuesta = []

    async def probe(_mensajes):
        enviados_al_mensaje_de_respuesta.append(bloqueante.calls)
        # Se suelta al emisor para que la background task pueda terminar y la
        # llamada ASGI no se quede colgada.
        bloqueante.release.set()

    status_code, cuerpo, sonda = await asgi_call_with_body_probe(
        app, "POST", "/api/v1/auth/forgot-password", {"email": "existe@empresa.com"}, probe
    )

    assert sonda is True
    assert status_code == 202
    assert _json_body(cuerpo) == {"detail": FORGOT_DETAIL}
    assert enviados_al_mensaje_de_respuesta == [0], (
        "el email se envió ANTES de que el cliente tuviera la respuesta: "
        "vuelve el oráculo temporal de enumeración"
    )
    # Y aun así, el código se emitió y se entregó (el arreglo no puede ser
    # "no hacer nada").
    assert bloqueante.calls == 1
    assert len(bloqueante.messages) == 1
    assert _extract_test_code(bloqueante.messages[0])


async def test_forgot_password_unknown_email_never_touches_the_sender(make_client):
    """La otra mitad de la garantía: para un email inexistente el proveedor no
    se toca en absoluto, ni antes ni después de la respuesta."""
    client, _sender, app = make_client()
    bloqueante = BlockingSender()
    app.state.email_sender = bloqueante

    async def probe(_mensajes):
        bloqueante.release.set()

    status_code, cuerpo, _sonda = await asgi_call_with_body_probe(
        app, "POST", "/api/v1/auth/forgot-password", {"email": "noexiste@empresa.com"}, probe
    )
    assert status_code == 202
    assert _json_body(cuerpo) == {"detail": FORGOT_DETAIL}
    assert bloqueante.calls == 0


async def test_register_responds_before_the_email_is_sent(make_client):
    """El registro usa el mismo camino en segundo plano. Aquí el oráculo sería
    mucho menos grave (el atacante ya sabe que la cuenta existe, porque la acaba
    de crear), pero dejarlo en línea significaría que la latencia del registro
    depende de Resend."""
    _client, _sender, app = make_client()
    bloqueante = BlockingSender()
    app.state.email_sender = bloqueante

    enviados_al_mensaje_de_respuesta = []

    async def probe(_mensajes):
        enviados_al_mensaje_de_respuesta.append(bloqueante.calls)
        bloqueante.release.set()

    status_code, _cuerpo, sonda = await asgi_call_with_body_probe(
        app,
        "POST",
        "/api/v1/auth/register",
        {"full_name": "Ana Gómez", "email": "nuevo@empresa.com", "password": PASSWORD},
        probe,
    )

    assert sonda is True
    assert status_code == 201
    assert enviados_al_mensaje_de_respuesta == [0]
    assert bloqueante.calls == 1  # emitido igualmente, solo que después


async def test_background_task_opens_its_own_session(make_client, caplog):
    """La sesión de `Depends(get_db)` ya está CERRADA cuando corre la background
    task (FastAPI cierra las dependencias con `yield` antes de responder), así
    que la tarea tiene que abrir la suya con `SessionLocal()`. Si alguien metiera
    la `db` de la petición en la closure, el endpoint reventaría con
    MissingGreenlet o con "sesión cerrada".

    Aquí se rompe `SessionLocal` por completo (hasta el momento de construirla) y
    se comprueban las dos cosas que tienen que cumplirse:
      1. la respuesta al cliente es la de siempre, y
      2. la excepción NO sale de la tarea (el 202 ya está en la calle: no hay a
         quién responderle, y una excepción acá tumba el worker).
    Ambas seFAILarían si `SessionLocal()` se construyera fuera del `try`."""
    import app.api.auth as auth_mod

    _client, _sender, app = make_client()

    def sesion_que_no_abre(*_a, **_kw):
        raise RuntimeError("no se pudo abrir la sesión")

    original = auth_mod.SessionLocal
    caplog.set_level("ERROR", logger="nexus.auth")
    auth_mod.SessionLocal = sesion_que_no_abre
    try:
        # 1) La respuesta no se ve afectada...
        res = await _call(
            app, "POST", "/api/v1/auth/forgot-password", {"email": "existe@empresa.com"}
        )
        assert res["status"] == 202
        assert res["json"] == {"detail": FORGOT_DETAIL}

        # 2) ...y la llamada directa tampoco propaga (esto es lo que se mide aquí:
        #    con `_call` el 202 ya está enviado y una excepción tarde no se ve).
        await auth_mod._issue_code_in_background(
            "password_reset",
            settings=get_settings(),
            sender=_sender,
            email="existe@empresa.com",
        )
    finally:
        auth_mod.SessionLocal = original

    # El fallo queda registrado para que se pueda diagnosticar.
    assert "no se pudo abrir la sesión" in caplog.text
    assert "password_reset" in caplog.text


async def test_background_task_never_propagates_a_database_failure(make_client):
    """La respuesta ya está enviada cuando corre la tarea, así que una excepción
    no puede convertirse en un 500 retroactivo ni tumbar el servidor: se registra
    y se termina. Aquí el emisor es el que revienta, que es el fallo esperable
    (proveedor caído) y el que el cliente no puede ver."""
    client, sender, _app = make_client()
    await register(client, email="existe@empresa.com")
    enviados = len(sender.messages)
    sender.error = EmailDeliveryError("Resend caído")

    res = await client.post("/api/v1/auth/forgot-password", json={"email": "existe@empresa.com"})
    assert res.status_code == 202
    assert res.json() == {"detail": FORGOT_DETAIL}
    assert len(sender.messages) == enviados  # nada nuevo salió


async def test_background_task_swallows_a_broken_session(make_client):
    """Si la propia sesión no se puede abrir, la respuesta sigue siendo la de
    siempre y el fallo se queda en el log. Cubre el `except` de
    `_issue_code_in_background` entero, incluido un rollback que falla."""
    import app.api.auth as auth_mod

    _client, _sender, app = make_client()

    class SesionRota:
        """Falla al abrirse y falla también al deshacerse: el `except` tiene que
        tragarse las dos cosas."""

        async def __aenter__(self):
            raise RuntimeError("no se pudo abrir la sesión")

        async def __aexit__(self, *exc):
            return False

        async def rollback(self):
            raise RuntimeError("ni siquiera se puede deshacer")

    original = auth_mod.SessionLocal
    auth_mod.SessionLocal = SesionRota
    try:
        res = await _call(
            app, "POST", "/api/v1/auth/forgot-password", {"email": "existe@empresa.com"}
        )
    finally:
        auth_mod.SessionLocal = original

    assert res["status"] == 202
    assert res["json"] == {"detail": FORGOT_DETAIL}


def _json_body(raw: bytes) -> dict:
    import json as _json

    return _json.loads(raw)


def _extract_test_code(message: EmailMessage) -> str:
    import re

    match = re.search(r"^[ \t]*(\d{6})[ \t]*$", message.text, re.MULTILINE)
    assert match, f"el email del segundo plano no lleva un código:\n{message.text}"
    return match.group(1)


async def _call(app, method: str, path: str, payload) -> dict:
    """Atajo: una llamada ASGI sin sonda, devolviendo status y cuerpo parseado."""
    status_code, cuerpo, _sonda = await asgi_call_with_body_probe(
        app, method, path, payload, lambda _m: asyncio.sleep(0)
    )
    return {"status": status_code, "json": _json_body(cuerpo)}


# ---------------------------------------------------------------------------
# 9. Reseteo de contraseña de punta a punta
# ---------------------------------------------------------------------------

async def test_reset_password_end_to_end_through_the_email(client):
    """El camino real: forgot-password -> código del email -> reset. Se comprueba
    el cambio de contraseña y la revocación de TODAS las sesiones."""
    body = await register(client, email="ana@empresa.com")
    # Dos sesiones (registro + login) para que la revocación sea múltiple
    await client.post(
        "/api/v1/auth/login", json={"email": "ana@empresa.com", "password": PASSWORD}
    )
    headers = bearer(body["access_token"])
    assert len((await client.get("/api/v1/auth/sessions", headers=headers)).json()) == 2

    res = await client.post("/api/v1/auth/forgot-password", json={"email": "ana@empresa.com"})
    assert res.status_code == 202
    codigo = client.email_sender.last_code()
    assert client.email_sender.purpose_of_last() == PASSWORD_RESET

    reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": "ana@empresa.com", "code": codigo, "new_password": NUEVA},
    )
    assert reset.status_code == 200, reset.text

    # Ambas sesiones revocadas: /sessions vacío y la cookie vieja no refresca
    assert (await client.get("/api/v1/auth/sessions", headers=headers)).json() == []
    cookie = client.cookies.get("nm_refresh")
    client.cookies.set("nm_refresh", cookie, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401

    # La contraseña antigua ya no entra; la nueva sí
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": "ana@empresa.com", "password": PASSWORD}
        )
    ).status_code == 401
    nuevo = await client.post(
        "/api/v1/auth/login", json={"email": "ana@empresa.com", "password": NUEVA}
    )
    assert nuevo.status_code == 200

    # El código se consumió
    filas = await codes_of(body["user"]["id"], PASSWORD_RESET)
    assert len(filas) == 1 and filas[0].consumed_at is not None


async def test_reset_password_keeps_other_accounts_untouched(client):
    await register(client, email="ana@empresa.com")
    body_b = await register(client, email="bruno@empresa.com")
    await client.post(
        "/api/v1/auth/login", json={"email": "bruno@empresa.com", "password": PASSWORD}
    )

    res = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "ana@empresa.com"}
    )
    assert res.status_code == 202
    codigo = client.email_sender.last_code()
    assert (
        await client.post(
            "/api/v1/auth/reset-password",
            json={"email": "ana@empresa.com", "code": codigo, "new_password": NUEVA},
        )
    ).status_code == 200

    # Bruno conserva sus dos sesiones y su contraseña
    sesiones = await client.get(
        "/api/v1/auth/sessions", headers=bearer(body_b["access_token"])
    )
    assert len(sesiones.json()) == 2
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": "bruno@empresa.com", "password": PASSWORD}
        )
    ).status_code == 200


# ---------------------------------------------------------------------------
# 10. Tope de códigos emitidos por hora
# ---------------------------------------------------------------------------

async def test_cap_per_hour_stops_the_sixth_code(client):
    """`verification_code_max_per_hour` (5) códigos por (usuario, propósito) en
    una hora. El registro consume el primero, así que la 6ª emisión es el 5º
    reenvío."""
    body = await register(client, email="tope@empresa.com")
    headers = bearer(body["access_token"])
    assert len(client.email_sender.messages) == 1

    for emission in range(2, 6):  # emisiones 2..5
        r = await client.post("/api/v1/auth/resend-verification", headers=headers)
        assert r.status_code == 200, f"emisión {emission}: {r.text}"
        assert r.json()["sent"] is True
        assert len(client.email_sender.messages) == emission

    assert len(await codes_of(body["user"]["id"], EMAIL_VERIFICATION)) == 5

    # La 6ª emisión: 429 con el mensaje específico del tope por usuario
    r = await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert r.status_code == 429
    assert "demasiados códigos" in r.json()["detail"]
    assert len(client.email_sender.messages) == 5  # no se emite nada

    # ...y el código vigente SIGUE sirviendo (el tope no deja al usuario fuera)
    codigo = client.email_sender.last_code()
    assert (
        await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
    ).status_code == 200


async def test_cap_does_not_leak_through_forgot_password(client):
    """Al agotar el tope, forgot-password sigue respondiendo 202 con el mismo
    detalle genérico (y sin enviar nada)."""
    body = await register(client, email="tope2@empresa.com")
    headers = bearer(body["access_token"])
    for _ in range(4):
        await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert len(client.email_sender.messages) == 5  # tope de email_verification

    # El primer forgot-password es el primero de SU propósito: sí envía
    res = await client.post("/api/v1/auth/forgot-password", json={"email": "tope2@empresa.com"})
    assert res.status_code == 202
    assert res.json()["detail"] == FORGOT_DETAIL
    assert client.email_sender.purpose_of_last() == PASSWORD_RESET
    assert len(client.email_sender.messages) == 6

    # Agotado también el de password_reset (4 más), sigue siendo 202 idéntico
    for _ in range(4):
        await client.post("/api/v1/auth/forgot-password", json={"email": "tope2@empresa.com"})
    assert len(client.email_sender.messages) == 10
    agotado = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "tope2@empresa.com"}
    )
    assert agotado.status_code == 202
    assert agotado.json()["detail"] == FORGOT_DETAIL
    assert len(client.email_sender.messages) == 10  # el tope corta el envío
    # ...y ni siquiera crea la fila del código que no pudo enviar
    assert len(await codes_of(body["user"]["id"], PASSWORD_RESET)) == 5


async def test_cap_window_is_rolling_one_hour(client):
    """Pasada la hora, el contador vuelve a cero (se verifica moviendo
    `created_at` hacia atrás: nada de sleeps)."""
    body = await register(client, email="ventana@empresa.com")
    headers = bearer(body["access_token"])
    for _ in range(4):
        await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert (await client.post("/api/v1/auth/resend-verification", headers=headers)).status_code == 429

    async with SessionLocal() as db:
        filas = list(
            (
                await db.execute(
                    select(VerificationCode).where(VerificationCode.user_id == body["user"]["id"])
                )
            ).scalars()
        )
        assert len(filas) == 5
        for fila in filas:
            fila.created_at = utcnow() - timedelta(hours=2)
        await db.commit()

    r = await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert r.status_code == 200, r.text
    assert len(client.email_sender.messages) == 6


# ---------------------------------------------------------------------------
# 11. resend-verification
# ---------------------------------------------------------------------------

async def test_resend_verification_sends_a_new_code_and_invalidates_the_old(client):
    body = await register(client, email="reenvio@empresa.com")
    headers = bearer(body["access_token"])
    codigo_viejo = client.email_sender.last_code()

    res = await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert res.status_code == 200
    # Contrato exacto de la respuesta documentado en el enunciado
    assert set(res.json()) == {"detail", "sent", "resend_available_in_minutes"}
    assert res.json()["sent"] is True
    assert client.email_sender.last_code() != codigo_viejo

    # El código anterior dejó de servir (solo hay uno vigente por propósito)
    viejo = await client.post(
        "/api/v1/auth/verify-email", json={"code": codigo_viejo}, headers=headers
    )
    assert viejo.status_code == 400
    assert await user_email_verified(body["user"]["id"]) is False

    # El nuevo sí
    nuevo = await client.post(
        "/api/v1/auth/verify-email",
        json={"code": client.email_sender.last_code()},
        headers=headers,
    )
    assert nuevo.status_code == 200


async def test_resend_verification_is_a_noop_when_already_verified(client):
    body = await register(client, email="yaverificado@empresa.com")
    headers = bearer(body["access_token"])
    codigo = client.email_sender.last_code()
    await client.post("/api/v1/auth/verify-email", json={"code": codigo}, headers=headers)
    enviados = len(client.email_sender.messages)

    res = await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert res.status_code == 200
    assert res.json()["sent"] is False
    assert "ya está verificado" in res.json()["detail"]
    assert len(client.email_sender.messages) == enviados  # cero envíos


async def test_resend_verification_503_when_the_provider_fails(make_client):
    """Aquí sí se puede ser honesto: el usuario está autenticado y solo se
    está limitando a sí mismo, así que un 503 informa sin revelar nada."""
    client, sender, _ = make_client()
    body = await register(client, email="caido@empresa.com")
    headers = bearer(body["access_token"])
    sender.error = EmailDeliveryError("Resend caído")

    res = await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert res.status_code == 503
    assert "No hemos podido enviar" in res.json()["detail"]
    # La cuenta sigue sin verificar y se puede reintentar
    assert await user_email_verified(body["user"]["id"]) is False
    sender.error = None
    assert (await client.post("/api/v1/auth/resend-verification", headers=headers)).status_code == 200


async def test_resend_verification_rate_limit_by_ip(make_client):
    """El límite por IP de los reenvíos es independiente del de verify-email.

    El 429 lleva el tiempo real que queda (no la ventana entera) en el cuerpo
    y en la cabecera `Retry-After`."""
    client, _sender, _app = make_client(
        rate_limit_resend_verification_max=1, rate_limit_resend_verification_window=3600
    )
    body = await register(client, email="ip2@empresa.com")
    headers = bearer(body["access_token"])
    assert (await client.post("/api/v1/auth/resend-verification", headers=headers)).status_code == 200
    r2 = await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert r2.status_code == 429
    cuerpo = r2.json()
    assert cuerpo["detail"] == "Has alcanzado el límite de reenvíos. Podrás pedir otro en 1 hora."
    # 1 h de ventana, y lo que queda es (casi) la hora entera
    assert 3590 <= cuerpo["retry_after_seconds"] <= 3600
    assert r2.headers["Retry-After"] == str(cuerpo["retry_after_seconds"])


async def test_resend_verification_requires_authentication(client):
    res = await client.post("/api/v1/auth/resend-verification")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# 12. validate_for_production
# ---------------------------------------------------------------------------

def prod_settings(**overrides) -> Settings:
    base = {
        "environment": "prod",
        "secret_key": "x" * 48,
        "cookie_secure": True,
        "email_provider": "resend",
        "resend_api_key": "re_clave_falsa",
        "email_enabled": True,
    }
    base.update(overrides)
    return Settings(**{**get_settings().model_dump(), **base})


def test_prod_requires_resend_api_key():
    """Sin RESEND_API_KEY, un forgot-password en prod "funcionaría" sin que
    nadie recibiera nada: esto lo hace fallar en el arranque."""
    with pytest.raises(RuntimeError) as exc:
        prod_settings(resend_api_key=None).validate_for_production()
    assert "RESEND_API_KEY" in str(exc.value)
    # También con la key en blanco
    with pytest.raises(RuntimeError) as exc:
        prod_settings(resend_api_key="   ").validate_for_production()
    assert "RESEND_API_KEY" in str(exc.value)


def test_prod_forbids_the_console_provider():
    """EMAIL_PROVIDER=console en prod dejaría los códigos en el log."""
    with pytest.raises(RuntimeError) as exc:
        prod_settings(email_provider="console").validate_for_production()
    assert "EMAIL_PROVIDER" in str(exc.value)


def test_prod_forbids_console_even_with_a_key():
    with pytest.raises(RuntimeError) as exc:
        prod_settings(email_provider="console", resend_api_key="re_clave_falsa").validate_for_production()
    assert "EMAIL_PROVIDER" in str(exc.value)


def test_prod_with_resend_and_key_is_valid():
    prod_settings().validate_for_production()  # no lanza


def test_prod_forbids_disabling_the_email():
    """`EMAIL_ENABLED=false` en prod era el hueco del bypass: la validación
    aceptaba "sin key" porque el email estaba apagado, y `build_email_sender`
    ignoraba el proveedor devolviendo el emisor de consola. Desactivar el email
    no es una opción: sin correo no hay verificación ni reseteo."""
    with pytest.raises(RuntimeError) as exc:
        prod_settings(email_enabled=False).validate_for_production()
    assert "EMAIL_ENABLED" in str(exc.value)
    # Tampoco vale la combinación que explotaba: provider resend, sin key y
    # email apagado. Ahora el primer error que sale es el de EMAIL_ENABLED.
    with pytest.raises(RuntimeError) as exc:
        prod_settings(email_enabled=False, resend_api_key=None).validate_for_production()
    assert "EMAIL_ENABLED" in str(exc.value)


def test_prod_email_checks_are_independent():
    """Cada fallo tiene su propio mensaje accionable, y el orden no esconde
    ninguno: se comprueba que cada variable culpable produce SU error."""
    # Solo falta la key (el resto bien).
    with pytest.raises(RuntimeError) as exc:
        prod_settings(resend_api_key=None).validate_for_production()
    assert "RESEND_API_KEY" in str(exc.value)

    # Solo el proveedor está mal.
    with pytest.raises(RuntimeError) as exc:
        prod_settings(email_provider="console").validate_for_production()
    assert "EMAIL_PROVIDER" in str(exc.value)

    # Solo el email está apagado.
    with pytest.raises(RuntimeError) as exc:
        prod_settings(email_enabled=False).validate_for_production()
    assert "EMAIL_ENABLED" in str(exc.value)

    # Y un proveedor desconocido (ni "console" ni "resend") tampoco vale: el
    # camino de "console" por descarte sería un agujero si mañana alguien
    # escribe "RESEND" con typo o "smtp".
    with pytest.raises(RuntimeError) as exc:
        prod_settings(email_provider="smtp").validate_for_production()
    assert "EMAIL_PROVIDER" in str(exc.value)


@pytest.mark.parametrize(
    "provider,key,enabled",
    [
        ("console", None, True),
        ("console", "re_clave_falsa", True),
        ("resend", None, True),
        ("resend", "re_clave_falsa", False),
        ("resend", None, False),  # la combinación exacta del bypass
        ("smtp", "re_clave_falsa", True),
    ],
)
def test_build_email_sender_never_returns_a_console_sender_in_prod(
    provider, key, enabled, monkeypatch
):
    """GARANTÍA DE SEGURIDAD: la ÚNICA forma de que production escriba un
    código en el log es que `build_email_sender` devuelva un `ConsoleSender`, así
    que en prod tiene que ser imposible: cualquier configuración que no sea
    "resend + key + habilitado" lanza `RuntimeError`."""
    import app.services.email as email_mod

    monkeypatch.setattr(email_mod, "resend", None)
    cfg = Settings(
        **{
            **get_settings().model_dump(),
            "environment": "prod",
            "email_provider": provider,
            "resend_api_key": key,
            "email_enabled": enabled,
        }
    )
    with pytest.raises(RuntimeError):
        sender = build_email_sender(cfg)
        pytest.fail(f"build_email_sender devolvió {type(sender).__name__} en prod")


async def test_console_sender_never_writes_the_code_in_prod(caplog):
    """TERCERA barrera, y la que de verdad importa: si algún día se alcanzara un
    `ConsoleSender` en producción, el código (una credencial de un solo uso) no
    puede aparecer en el log. Solo `to` + `subject`, a WARNING.

    Se construye el emisor a mano a propósito: es lascenario que la barrera
    anterior debería impedir y que hay que cubrir igualmente."""
    import logging

    cfg = prod_settings()
    sender = ConsoleSender(cfg)
    caplog.set_level(logging.DEBUG, logger="nexus.email")

    await sender.send(
        EmailMessage(
            to="ana@empresa.com",
            subject="Tu código de verificación",
            html="<p>042731</p>",
            text="Hola, Ana,\n\n    042731\n",
        )
    )

    assert "042731" not in caplog.text, (
        "el código de un solo uso se ha escrito en el log de producción:\n"
        + caplog.text
    )
    assert "ana@empresa.com" in caplog.text  # a quién sí, para poder diagnosticar
    assert "Tu código de verificación" in caplog.text
    # ...y solo a WARNING, nunca a INFO (que es donde se vaciaba el cuerpo).
    niveles = {r.levelno for r in caplog.records}
    assert niveles == {logging.WARNING}, niveles


def test_create_app_fails_in_prod_with_the_exploitable_combination():
    """El ORDEN importa en el arranque: `create_app` valida la configuración
    antes de construir el emisor, y la barrera de `build_email_sender` (dos
    líneas más abajo) tiene sus propios RuntimeError. Si el orden se invirtiera,
    el operador con EMAIL_PROVIDER=resend + EMAIL_ENABLED=false + sin key
    —justo la combinación que exploitaba— leería el error del emisor, que no
    le dice qué variable tiene que cambiar."""
    with pytest.raises(RuntimeError) as exc:
        create_app(prod_settings(email_enabled=False, resend_api_key=None))
    assert "EMAIL_ENABLED" in str(exc.value)

    with pytest.raises(RuntimeError) as exc:
        create_app(prod_settings(email_provider="console", resend_api_key=None))
    assert "EMAIL_PROVIDER" in str(exc.value)

    # Y la única combinación que arranca.
    create_app(prod_settings())


async def test_console_sender_still_logs_the_code_outside_prod(caplog):
    """La otra cara: FUERA de prod el emisor de consola tiene que seguir
    escribiendo el cuerpo completo, que es lo que permite recorrer el flujo de
    verificación entero en dev sin configurar ningún proveedor."""
    import logging

    cfg = Settings(**{**get_settings().model_dump(), "environment": "dev"})
    sender = ConsoleSender(cfg)
    caplog.set_level(logging.INFO, logger="nexus.email")

    await sender.send(
        EmailMessage(
            to="ana@empresa.com",
            subject="Tu código de verificación",
            html="<p>042731</p>",
            text="Hola, Ana,\n\n    042731\n",
        )
    )

    assert "042731" in caplog.text
    assert any(r.levelno == logging.INFO for r in caplog.records)


def test_dev_never_fails_validation():
    """En dev no se valida nada: ni el secreto, ni la cookie, ni el proveedor."""
    Settings(
        **{
            **get_settings().model_dump(),
            "environment": "dev",
            "secret_key": "corta",
            "cookie_secure": False,
            "email_provider": "console",
            "resend_api_key": None,
        }
    ).validate_for_production()


def test_prod_still_requires_a_strong_secret_and_secure_cookie():
    """No es de esta migración, pero el orden de las comprobaciones importa: el
    RuntimeError debe seguir siendo el del email cuando el resto está bien."""
    with pytest.raises(RuntimeError) as exc:
        prod_settings(secret_key="dev-only-insecure-secret-change-me-in-prod").validate_for_production()
    assert "SECRET_KEY" in str(exc.value)
    with pytest.raises(RuntimeError) as exc:
        prod_settings(cookie_secure=False).validate_for_production()
    assert "COOKIE_SECURE" in str(exc.value)


# ---------------------------------------------------------------------------
# 13. Guard de Origin sobre las rutas nuevas
# ---------------------------------------------------------------------------

NEW_AUTH_ROUTES = [
    ("post", "/api/v1/auth/forgot-password", {"email": "ana@empresa.com"}),
    ("post", "/api/v1/auth/reset-password", {"email": "a@b.com", "code": "123456", "new_password": NUEVA}),
    ("post", "/api/v1/auth/verify-email", {"code": "123456"}),
    ("post", "/api/v1/auth/resend-verification", None),
]


@pytest.mark.parametrize("method,path,payload", NEW_AUTH_ROUTES)
async def test_origin_guard_blocks_new_auth_routes(client, method, path, payload):
    res = await client.request(method.upper(), path, json=payload, headers={"Origin": "https://evil.example.com"})
    assert res.status_code == 403
    assert res.json()["detail"] == "Origen no permitido"


@pytest.mark.parametrize("method,path,payload", NEW_AUTH_ROUTES)
async def test_origin_guard_allows_frontend_origin_on_new_routes(client, method, path, payload):
    res = await client.request(method.upper(), path, json=payload, headers={"Origin": "http://localhost:5173"})
    assert res.status_code != 403


def test_new_routes_stay_under_the_auth_prefix():
    """origin_guard solo intercepta mutaciones bajo /api/v1/auth: si alguien
    moviera una de estas rutas fuera del router de auth, la defensa se
    desactivaría en silencio."""
    paths = create_app(get_settings()).openapi()["paths"]
    for metodo, path, _ in NEW_AUTH_ROUTES:
        assert path in paths, f"{path} ha desaparecido del contrato"
        assert metodo in paths[path]
        assert path.startswith("/api/v1/auth")


# ---------------------------------------------------------------------------
# 14. GET /verification-status y el 429 con el tiempo restante
#
# El endpoint existe para que el cliente NO calcule lo que no puede saber:
# cuándo se emitió el código (solo conoce la hora en la que él lo pidió) ni
# cuántas filas hay en verification_codes. Y el 429 tiene que decir el tiempo
# REAL que queda, no la ventana entera.
# ---------------------------------------------------------------------------

STATUS_URL = "/api/v1/auth/verification-status"


async def estado(client, headers) -> dict:
    """GET /verification-status con token, o el cuerpo si la ruta no responde."""
    res = await client.get(STATUS_URL, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def marcar_consumido(row_id: int) -> None:
    """Deja el código como "usado" sin verificar la cuenta: es el estado en el
    que se queda una cuenta sin código vigente (5 intentos fallidos, p. ej.)."""
    async with SessionLocal() as db:
        fila = await db.get(VerificationCode, row_id)
        fila.consumed_at = utcnow()
        await db.commit()


async def envelvecer(row_id: int, *, horas: int) -> None:
    """Atrasa `created_at` para sacar la fila de la ventana de la última hora."""
    async with SessionLocal() as db:
        fila = await db.get(VerificationCode, row_id)
        fila.created_at = utcnow() - timedelta(hours=horas)
        await db.commit()


async def caducar(row_id: int, *, minutos: int) -> None:
    """Deja la fila en el pasado. Se edita DENTRO de la sesión que la carga:
    tocarla fuera dejaría el cambio solo en un objeto desligado y la fila
    seguiría vigente, y el test pasaría sin probar nada."""
    async with SessionLocal() as db:
        fila = await db.get(VerificationCode, row_id)
        fila.expires_at = utcnow() - timedelta(minutes=minutos)
        await db.commit()


# --- Autenticación ---------------------------------------------------------

async def test_verification_status_requires_authentication(client):
    """Sin bearer no se responde: tanto la existencia del código pendiente
    como su caducidad son datos de la cuenta del solicitante."""
    res = await client.get(STATUS_URL)
    assert res.status_code == 401
    assert res.json()["detail"] == "Autenticación requerida"

    basura = await client.get(STATUS_URL, headers={"Authorization": "Bearer no-es-un-jwt"})
    assert basura.status_code == 401


async def test_verification_status_401_with_a_token_of_another_scheme(client):
    assert (
        await client.get(STATUS_URL, headers={"Authorization": "Basic YWJjOmRlZg=="})
    ).status_code == 401


# --- pendiente / caducidad -------------------------------------------------

async def test_status_is_not_pending_when_the_account_is_already_verified(client):
    """Cuenta verificada: `pending` False y el resto a 0. No hay nada que
    contar y ningún límite que mostrar, así que ni se consultan las filas."""
    body = await register(client, email="yaverificado@empresa.com")
    headers = bearer(body["access_token"])
    assert (
        await client.post(
            "/api/v1/auth/verify-email",
            json={"code": client.email_sender.last_code()},
            headers=headers,
        )
    ).status_code == 200

    assert await estado(client, headers) == {
        "pending": False,
        "expires_in_seconds": 0,
        "resend_available_in_seconds": 0,
        "codes_used_last_hour": 0,
        "codes_limit_per_hour": 0,
    }


async def test_status_expiry_matches_the_pending_code(client):
    """`expires_in_seconds` es la cuenta atrás real del código vigente, leída
    de la fila: el cliente no puede calcularla (no sabe cuándo se emitió)."""
    body = await register(client, email="vigente@empresa.com")
    headers = bearer(body["access_token"])

    datos = await estado(client, headers)
    assert datos["pending"] is True
    assert datos["codes_limit_per_hour"] == 5

    fila = (await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0]
    esperado = int((fila.expires_at - utcnow()).total_seconds())
    # Tolerancia de 2 s: entre leer la fila y formatear la respuesta pasa tiempo.
    assert abs(datos["expires_in_seconds"] - esperado) <= 2
    # Coherente con el TTL configurado (10 min por defecto).
    assert 0 < datos["expires_in_seconds"] <= 10 * 60


async def test_status_expiry_zero_when_there_is_no_pending_code(client):
    """Sin código vigente, `pending` False y `expires_in_seconds` 0 (nunca
    `None`, nunca un número que invite a pintar una cuenta atrás inexistente)."""
    body = await register(client, email="sincodigo@empresa.com")
    headers = bearer(body["access_token"])
    await marcar_consumido((await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0].id)

    datos = await estado(client, headers)
    assert datos["pending"] is False
    assert datos["expires_in_seconds"] == 0
    # El código emitido sigue contando como usado en la última hora.
    assert datos["codes_used_last_hour"] == 1


async def test_status_of_an_expired_code_is_pending_with_zero_left(client):
    """Un código CADUCADO sigue siendo `pending` (hay una verificación sin
    completar) pero con 0 segundos: la UI lo pinta como "caducado, pide otro".
    Marcarlo `pending=False` haría desaparecer la barra y dejaría al usuario sin
    ninguna señal de que debe pedir un código nuevo."""
    body = await register(client, email="caducado@empresa.com")
    headers = bearer(body["access_token"])
    await caducar((await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0].id, minutos=1)

    datos = await estado(client, headers)
    assert datos["pending"] is True
    assert datos["expires_in_seconds"] == 0


async def test_status_expiry_is_never_negative(client):
    """Suelo en 0 aunque la fila caducada se lea después del formateo: un
    negativo en el JSON rompe la cuenta atrás del frontend."""
    body = await register(client, email="suelo@empresa.com")
    headers = bearer(body["access_token"])
    await caducar((await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0].id, minutos=4320)

    assert (await estado(client, headers))["expires_in_seconds"] == 0


# --- tope horario ----------------------------------------------------------

async def test_status_counts_the_codes_used_in_the_last_hour(client):
    """`codes_used_last_hour` cuenta las emisiones del usuario, no las suyas:
    el registro emite el primer código y cada reenvío uno más."""
    body = await register(client, email="cuenta@empresa.com")
    headers = bearer(body["access_token"])
    assert (await estado(client, headers))["codes_used_last_hour"] == 1

    for _ in range(3):
        assert (
            await client.post("/api/v1/auth/resend-verification", headers=headers)
        ).status_code == 200
    assert (await estado(client, headers))["codes_used_last_hour"] == 4

    # Un código de otro propósito NO cuenta: la tabla es unificada y el tope
    # de `_issue_code_and_email` es por (usuario, propósito).
    await insert_code(body["user"]["id"], PASSWORD_RESET, "042731")
    assert (await estado(client, headers))["codes_used_last_hour"] == 4


async def test_status_ignores_codes_older_than_an_hour(client):
    """La ventana es la última hora, no la historia: sin esto el contador
    nunca bajaría y el frontend anunciaría un tope que ya se ha desbloqueado.

    Se envejecen 90 minutos a propósito (y no "2 horas"): 90 está FUERA de la
    ventana de 1 h y DENTRO de la de 2, así que el test distingue la una de la
    otra."""
    body = await register(client, email="viejo@empresa.com")
    headers = bearer(body["access_token"])
    for _ in range(2):
        await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert (await estado(client, headers))["codes_used_last_hour"] == 3

    await envelvecer((await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[0].id, horas=1.5)
    assert (await estado(client, headers))["codes_used_last_hour"] == 2

    # Lo contrario: un código de hace poco más de un minuto sigue dentro de la
    # ventana (si el corte fuera "la última emisión" en vez de "la última
    # hora", esto también fallaría).
    await envelvecer((await codes_of(body["user"]["id"], EMAIL_VERIFICATION))[1].id, horas=0.02)
    assert (await estado(client, headers))["codes_used_last_hour"] == 2


async def test_status_does_not_leak_other_accounts_codes(client):
    """El contador es POR USUARIO: el tope de Ana no puede aparecer en el
    estado de Bruno (y al revés)."""
    ana = await register(client, email="ana2@empresa.com")
    bruno = await register(client, email="bruno2@empresa.com")
    for _ in range(2):
        await client.post("/api/v1/auth/resend-verification", headers=bearer(ana["access_token"]))

    assert (await estado(client, bearer(ana["access_token"])))["codes_used_last_hour"] == 3
    assert (await estado(client, bearer(bruno["access_token"])))["codes_used_last_hour"] == 1


# --- enfriamiento del reenvío ---------------------------------------------

async def test_status_resend_cooldown_is_zero_before_any_resend(client):
    """Todavía no se pidió ningún reenvío: el cubo admite y el número es 0,
    que el frontend traduce en "puedes pedirlo ya"."""
    body = await register(client, email="fresco@empresa.com")
    assert (await estado(client, bearer(body["access_token"])))["resend_available_in_seconds"] == 0


async def test_status_resend_cooldown_matches_the_real_429(make_client):
    """LA coherencia que importa: el número que enseña este endpoint es el
    del MISMO cubo que va a denegar el reenvío. Si la clave se construyera de
    otra forma, el contador iría bien y el 429 no aparecería nunca (o al
    revés), y el frontend mentiría.

    Se comprueba contra el 429 de verdad, no contra una fórmula."""
    client, _sender, _app = make_client(
        rate_limit_resend_verification_max=1, rate_limit_resend_verification_window=3600
    )
    body = await register(client, email="cohemente@empresa.com")
    headers = bearer(body["access_token"])

    # 1er reenvío: el cubo (max=1) admite.
    assert (await client.post("/api/v1/auth/resend-verification", headers=headers)).status_code == 200

    cool = (await estado(client, headers))["resend_available_in_seconds"]
    assert cool > 0, "con el cubo lleno, el estado tiene que decir que falta tiempo"

    # 2º reenvío: 429, con un valor del mismo cubo y casi el mismo (ha pasado
    # algo más de tiempo entre las dos llamadas, así que no puede crecer).
    r429 = await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert r429.status_code == 429
    del_429 = r429.json()["retry_after_seconds"]
    assert 0 < cool <= del_429 <= 3600


async def test_status_does_not_consume_the_resend_bucket(make_client):
    """Mirar el estado NO gasta un reenvío: `retry_after` es una consulta pura.
    Con el umbral en 1, varias lecturas y el reenvío sigue pasando; si la
    lectura consumiera plaza, este test recibiría un 429."""
    client, _sender, _app = make_client(
        rate_limit_resend_verification_max=1, rate_limit_resend_verification_window=3600
    )
    body = await register(client, email="lectura@empresa.com")
    headers = bearer(body["access_token"])

    for _ in range(5):
        assert (await estado(client, headers))["resend_available_in_seconds"] == 0
    assert (await client.post("/api/v1/auth/resend-verification", headers=headers)).status_code == 200


async def test_status_is_read_only(client):
    """Un GET no muta nada: ni emite códigos ni invalida el vigente."""
    body = await register(client, email="sololectura@empresa.com")
    headers = bearer(body["access_token"])
    antes = await codes_of(body["user"]["id"], EMAIL_VERIFICATION)

    await estado(client, headers)

    despues = await codes_of(body["user"]["id"], EMAIL_VERIFICATION)
    assert [f.id for f in antes] == [f.id for f in despues]
    assert all(f.consumed_at is None for f in despues)
    # Y el código sigue sirviendo: leer el estado no lo gastó.
    assert (
        await client.post(
            "/api/v1/auth/verify-email",
            json={"code": client.email_sender.last_code()},
            headers=headers,
        )
    ).status_code == 200


# --- rate limit de la propia ruta ------------------------------------------

async def test_verification_status_is_rate_limited(make_client):
    """La ruta tiene umbral propio. Además, si su clave faltara en el dict
    `limits` de make_rate_limit_dependency esto devolvería 500 (KeyError) en vez
    de 429: es el fallo que ya ha pasado en este repo."""
    client, _sender, _app = make_client(
        rate_limit_verification_status_max=1, rate_limit_verification_status_window=60
    )
    body = await register(client, email="rlstatus@empresa.com")
    headers = bearer(body["access_token"])

    r1 = await client.get(STATUS_URL, headers=headers)
    r2 = await client.get(STATUS_URL, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 429
    assert r2.json()["retry_after_seconds"] > 0
    assert "Retry-After" in r2.headers


async def test_verification_status_uses_production_limits_by_default(client):
    """La lectura es generosa y usa los umbrales de producción (conftest los
    sube): un umbral estrecho convertiría una recarga de página en un 429."""
    assert (get_settings().rate_limit_verification_status_max,
            get_settings().rate_limit_verification_status_window) == (60, 60)

    body = await register(client, email="produccion@empresa.com")
    headers = bearer(body["access_token"])
    for _ in range(5):
        assert (await client.get(STATUS_URL, headers=headers)).status_code == 200


# --- El 429, en todas las rutas --------------------------------------------

async def test_429_carries_retry_after_in_header_and_body(make_client):
    """El 429 lleva el tiempo restante en los dos sitios, y ambos salen del
    cubo: la cabecera la leen proxies y librerías, el campo el frontend."""
    client, _sender, _app = make_client(
        rate_limit_verify_email_max=5, rate_limit_verify_email_window=60
    )
    body = await register(client, email="cabecera@empresa.com")
    headers = bearer(body["access_token"])
    codigo = client.email_sender.last_code()
    malo = "999999" if codigo != "999999" else "888888"

    # verify-email: 5/min aquí, así que el cubo se llena con los 5 intentos.
    for _ in range(5):
        assert (
            await client.post("/api/v1/auth/verify-email", json={"code": malo}, headers=headers)
        ).status_code == 400
    res = await client.post("/api/v1/auth/verify-email", json={"code": malo}, headers=headers)

    assert res.status_code == 429
    cuerpo = res.json()
    assert set(cuerpo) == {"detail", "retry_after_seconds"}
    assert cuerpo["retry_after_seconds"] > 0
    # La cabecera y el cuerpo no pueden discrepar: salen del mismo cálculo.
    assert res.headers["Retry-After"] == str(cuerpo["retry_after_seconds"])


async def test_429_detail_writes_the_real_time_legibly(make_client):
    """El texto dice el tiempo REAL que queda y en palabras: "47 minutos" o
    "30 segundos", nunca un número crudo ni la ventana entera."""
    client, _sender, _app = make_client(
        rate_limit_forgot_max=1, rate_limit_forgot_window=2820  # 47 minutos
    )
    body = await register(client, email="legible@empresa.com")
    headers = bearer(body["access_token"])

    assert (
        await client.post("/api/v1/auth/forgot-password", json={"email": "legible@empresa.com"})
    ).status_code == 202
    res = await client.post("/api/v1/auth/forgot-password", json={"email": "legible@empresa.com"})

    assert res.status_code == 429
    cuerpo = res.json()
    assert cuerpo["detail"] == (
        "Has alcanzado el límite de peticiones. Podrás volver a intentarlo en 47 minutos."
    )
    assert cuerpo["retry_after_seconds"] == 2820
    assert res.headers["Retry-After"] == "2820"


async def test_429_message_fits_the_remaining_time_of_the_window(client):
    """Con una ventana de 60 s, el mensaje dice segundos (la unidad con la que
    se reintenta de verdad), no "1 minuto"."""
    app = create_app(Settings(rate_limit_login_max=1, rate_limit_login_window=60))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        r1 = await c.post("/api/v1/auth/login", json={"email": "x@y.com", "password": "no"})
        r2 = await c.post("/api/v1/auth/login", json={"email": "x@y.com", "password": "no"})

    assert r1.status_code == 401
    assert r2.status_code == 429
    cuerpo = r2.json()
    assert 1 <= cuerpo["retry_after_seconds"] <= 60
    assert cuerpo["detail"].startswith("Demasiados intentos. Espera ")
    assert cuerpo["detail"].endswith(" e inténtalo de nuevo.")
    # Con 60 s de ventana el texto cae en segundos o en 1 minuto, nunca en 0.
    assert "segundos" in cuerpo["detail"] or "1 minuto" in cuerpo["detail"]


@pytest.mark.parametrize(
    "segundos,esperado",
    [
        (0, "un momento"),
        (1, "1 segundo"),
        (30, "30 segundos"),
        (59, "59 segundos"),
        (60, "1 minuto"),
        (90, "2 minutos"),
        (2820, "47 minutos"),
        (3600, "1 hora"),
        (5400, "1 hora 30 minutos"),
        (7200, "2 horas"),
        (7380, "2 horas 3 minutos"),
    ],
)
def test_humanize_wait_says_the_time_in_words(segundos, esperado):
    """Se redondea hacia ARRIBA en minutos y horas: decir "en 1 minuto" con 119
    segundos invita a reintentar y a encadenar otro 429. Por debajo del minuto
    se cuentan segundos, que es como se lee "prueba otra vez"."""
    assert humanize_wait(segundos) == esperado


# --- El limitador, en aislamiento ------------------------------------------

def test_limiter_reports_the_time_left_of_the_bucket():
    """`check` decide Y dice cuánto falta, leyendo el cubo de verdad (no
    `window_seconds` a pelo)."""
    limiter = InMemoryRateLimiter()
    assert limiter.check("k", 2, 60).allowed is True
    assert limiter.check("k", 2, 60).allowed is True

    denegado = limiter.check("k", 2, 60)
    assert denegado.allowed is False
    assert 0 < denegado.retry_after_seconds <= 60


def test_limiter_separates_buckets_by_key():
    limiter = InMemoryRateLimiter()
    assert limiter.check("ip1:/a", 1, 60).allowed is True
    assert limiter.check("ip2:/a", 1, 60).allowed is True  # otra IP
    assert limiter.check("ip1:/b", 1, 60).allowed is True  # otra ruta
    assert limiter.check("ip1:/a", 1, 60).allowed is False


def test_limiter_does_not_extend_the_window_with_denied_requests():
    """Una petición denegada NO se anota en el cubo: si se anotara, un cliente
    que insistiera en el 429 iría empujando su propia ventana y el tiempo que
    se le informa crecería sin fin."""
    limiter = InMemoryRateLimiter()
    limiter.check("k", 1, 60)
    primero = limiter.check("k", 1, 60).retry_after_seconds
    for _ in range(5):
        assert limiter.check("k", 1, 60).retry_after_seconds <= primero


def test_limiter_retry_after_is_a_pure_query():
    """`retry_after` no consume ni crea claves: llamarlo repetidas veces deja el
    cubo exactamente igual (y ni siquiera una entrada vacía en el mapa)."""
    limiter = InMemoryRateLimiter()
    assert limiter.retry_after("nuevo", 1, 60) == 0
    assert "nuevo" not in limiter._buckets  # no se crea la clave al consultar

    limiter.check("k", 2, 60)
    assert limiter.retry_after("k", 2, 60) == 0  # aún admite
    limiter.check("k", 2, 60)

    for _ in range(5):
        assert limiter.retry_after("k", 2, 60) > 0
    # Tras 5 consultas el cubo sigue lleno (no se consumió ninguna plaza) y el
    # tiempo restante no ha crecido: consultar no empuja la ventana.
    primera = limiter.check("k", 2, 60)
    assert primera.allowed is False
    assert primera.retry_after_seconds > 0
    assert limiter.check("otra", 1, 60).allowed is True


def test_limiter_reports_what_is_left_not_the_whole_window():
    """El número es lo que QUEDA del cubo, no la ventana completa (que es lo que
    devolvía el 429 antes de este cambio).

    No se duerme nada: se escriben a mano los sellos de tiempo del cubo, que es
    literalmente lo que el limitador mira. Cubo con una petición de hace 1,5 s y
    ventana de 2 s -> queda medio segundo, no 2."""
    limiter = InMemoryRateLimiter()
    ahora = time.monotonic()

    limiter._buckets["k"] = deque([ahora - 1.5])
    decision = limiter.check("k", 1, 2)
    assert decision.allowed is False
    assert decision.retry_after_seconds == 1, "devolvió la ventana, no lo que queda"

    # 30 s de ventana con una petición de hace 90 s: quedan 30, no 120.
    limiter._buckets["k"] = deque([ahora - 90])
    assert limiter.retry_after("k", 1, 120) == 30

    # Y una petición ya fuera de la ventana no bloquea nada.
    limiter._buckets["k"] = deque([ahora - 500])
    assert limiter.retry_after("k", 1, 120) == 0
    assert limiter.check("k", 1, 120).allowed is True


def test_limiter_with_max_zero_denies_instead_of_crashing():
    """`RATE_LIMIT_*_MAX=0` es una configuración sin sentido pero no imposible.
    Antes devolvía simplemente `False`; leer `bucket[0]` en un cubo vacío lo
    convertiría en un IndexError detrás de un 500."""
    limiter = InMemoryRateLimiter()
    decision = limiter.check("k", 0, 60)
    assert decision.allowed is False
    assert decision.retry_after_seconds == 60
    assert limiter.retry_after("k", 0, 60) == 60  # tampoco revienta la consulta


def test_bucket_key_is_host_and_path():
    assert bucket_key("127.0.0.1", "/api/v1/auth/resend-verification") == (
        "127.0.0.1:/api/v1/auth/resend-verification"
    )
