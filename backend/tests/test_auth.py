"""Suite de tests del sistema de autenticación.

Cubre: registro, login, protección de /me, rotación de refresh tokens,
detección de reutilización, revocación por logout/reset, cierre de sesión,
rate limiting, anti-enumeración y guard de Origin.

El reseteo de contraseña ya no usa un token largo: se hace con un CÓDIGO de
6 dígitos y el body es `{email, code, new_password}`. Los tests de reseteo de
este módulo comprueban el contrato HTTP; la semántica del código (hash salado,
intentos, caducidad, aislamiento entre usuarios) vive en
`tests/test_verification.py`.
"""

from datetime import timedelta

import jwt
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.main import create_app
from app.models import User, VerificationCode
from app.security import hash_verification_code, utcnow

PASSWORD = "Segura-1234"
PASSWORD_RESET = "password_reset"


async def register_user(client: AsyncClient, email: str = "ana@empresa.com", password: str = PASSWORD):
    return await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Ana Gómez", "email": email, "password": password},
    )


async def insert_reset_code(
    email: str, code: str = "123456", *, expires_at=None
) -> tuple[str, int]:
    """Inserta una fila VerificationCode de reseteo como si se acabara de
    emitir por email, y devuelve (user_id, id de la fila)."""
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None, f"el usuario {email} debe existir"
        row = VerificationCode(
            user_id=user.id,
            purpose=PASSWORD_RESET,
            code_hash=hash_verification_code(PASSWORD_RESET, user.id, code),
            expires_at=expires_at or utcnow() + timedelta(minutes=10),
        )
        db.add(row)
        await db.commit()
        return user.id, row.id


async def code_row(row_id: int) -> VerificationCode:
    async with SessionLocal() as db:
        return await db.get(VerificationCode, row_id)


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

async def test_register_success(client, auth_headers):
    res = await register_user(client)
    assert res.status_code == 201
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "ana@empresa.com"
    assert body["user"]["full_name"] == "Ana Gómez"
    assert body["user"]["auth_provider"] == "local"
    # La cuenta nace SIN verificar: el email es lo que se confirma después con
    # el código de 6 dígitos (POST /verify-email).
    assert body["user"]["is_email_verified"] is False

    # La cookie de refresco debe estar presente (httpOnly, SameSite=Lax)
    cookie = res.cookies.get("nm_refresh")
    assert cookie, "Debe emitir cookie de refresco"

    # /me autenticado con el access token
    me = await client.get("/api/v1/auth/me", headers=auth_headers(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "ana@empresa.com"


async def test_register_duplicate_email(client):
    assert (await register_user(client)).status_code == 201
    res = await register_user(client, email="ana@empresa.com")
    assert res.status_code == 409


async def test_register_email_case_insensitive(client):
    assert (await register_user(client, email="Ana@Empresa.com")).status_code == 201
    # Mismo email normalizado da 409
    res = await register_user(client, email="ana@empresa.com")
    assert res.status_code == 409


async def test_register_weak_password(client):
    res = await register_user(client, password="corta")
    assert res.status_code == 422


async def test_register_invalid_email(client):
    res = await register_user(client, email="no-es-un-correo")
    assert res.status_code == 422


async def test_password_stored_hashed(client):
    await register_user(client)
    from sqlalchemy import select

    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == "ana@empresa.com"))).scalar_one()
        assert user
        assert user.password_hash != PASSWORD
        assert user.password_hash.startswith("$argon2")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def test_login_success(client):
    await register_user(client)
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "ana@empresa.com", "password": PASSWORD, "remember_me": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["access_token"]
    assert res.cookies.get("nm_refresh")
    # remember_me=True -> cookie persistente (Max-Age 30 días)
    set_cookie = res.headers.get("set-cookie", "")
    assert "Max-Age=2592000" in set_cookie


async def test_login_remember_me_false_session_cookie(client):
    await register_user(client)
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "ana@empresa.com", "password": PASSWORD, "remember_me": False},
    )
    set_cookie = res.headers.get("set-cookie", "")
    assert "Max-Age" not in set_cookie  # cookie de sesión (se borra al cerrar navegador)


async def test_login_wrong_password(client):
    await register_user(client)
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "ana@empresa.com", "password": "incorrecta1"},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Correo o contraseña incorrectos"


async def test_login_unknown_email_same_message(client):
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "nadie@empresa.com", "password": PASSWORD},
    )
    assert res.status_code == 401
    # Mismo mensaje que password incorrecto: no se filtra si el correo existe
    assert res.json()["detail"] == "Correo o contraseña incorrectos"


async def test_login_uppercase_email(client):
    await register_user(client, email="carlos@empresa.com")
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "CARLOS@Empresa.com", "password": PASSWORD},
    )
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# /me (protección de rutas autenticadas)
# ---------------------------------------------------------------------------

async def test_me_requires_token(client, auth_headers):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=auth_headers("token-invalido"))).status_code == 401


async def test_me_rejects_expired_token(client, auth_headers):
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": "cualquiera",
            "type": "access",
            "iat": 0,
            "exp": 0,  # expirado
            "jti": "x",
        },
        settings.secret_key,
        algorithm="HS256",
    )
    res = await client.get("/api/v1/auth/me", headers=auth_headers(expired))
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Rotación de refresh tokens + detección de reutilización
# ---------------------------------------------------------------------------

async def test_refresh_rotation_and_reuse_detection(client):
    await register_user(client)
    old_cookie = client.cookies.get("nm_refresh")

    # Primer refresh: emite nuevo cookie y access token
    r1 = await client.post("/api/v1/auth/refresh")
    assert r1.status_code == 200
    new_cookie = client.cookies.get("nm_refresh")
    assert new_cookie and new_cookie != old_cookie

    # Reutilizar el token ROTADO = posible robo -> 401 + revoca TODAS las sesiones
    client.cookies.set("nm_refresh", old_cookie, path="/api/v1/auth")
    r2 = await client.post("/api/v1/auth/refresh")
    assert r2.status_code == 401

    # El token nuevo (aún válido) queda revocado en cascada por la detección
    client.cookies.set("nm_refresh", new_cookie, path="/api/v1/auth")
    r3 = await client.post("/api/v1/auth/refresh")
    assert r3.status_code == 401


async def test_refresh_without_cookie(client):
    res = await client.post("/api/v1/auth/refresh")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

async def test_logout_revokes_refresh(client):
    await register_user(client)
    old_cookie = client.cookies.get("nm_refresh")
    res = await client.post("/api/v1/auth/logout")
    assert res.status_code == 204

    # Intentar refrescar con la cookie revocada -> 401 (y revocación en cascada)
    client.cookies.set("nm_refresh", old_cookie, path="/api/v1/auth")
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

async def test_login_rate_limited():
    app = create_app(
        Settings(rate_limit_login_max=2, rate_limit_login_window=60)
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        from sqlalchemy import select

        async with SessionLocal() as db:
            db.add(User(email="rl@empresa.com", full_name="RL Test", password_hash="x"))
            await db.commit()

        r1 = await c.post("/api/v1/auth/login", json={"email": "rl@empresa.com", "password": "x"})
        r2 = await c.post("/api/v1/auth/login", json={"email": "rl@empresa.com", "password": "x"})
        assert r1.status_code == 401  # credenciales inválidas (password "x")
        assert r2.status_code == 401
        r3 = await c.post("/api/v1/auth/login", json={"email": "rl@empresa.com", "password": "x"})
        assert r3.status_code == 429
        assert "Retry-After" in r3.headers


async def test_register_rate_limited():
    app = create_app(Settings(rate_limit_register_max=1, rate_limit_register_window=60))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        r1 = await c.post(
            "/api/v1/auth/register",
            json={"full_name": "A B", "email": "rl2@empresa.com", "password": PASSWORD},
        )
        r2 = await c.post(
            "/api/v1/auth/register",
            json={"full_name": "A B", "email": "rl3@empresa.com", "password": PASSWORD},
        )
        assert r1.status_code == 201
        assert r2.status_code == 429


# ---------------------------------------------------------------------------
# Forgot / reset password
# ---------------------------------------------------------------------------

async def test_forgot_password_no_enumeration(client):
    # Misma respuesta (202, mismo detalle) para email existente o no
    await register_user(client, email="existente@empresa.com")
    r_existing = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "existente@empresa.com"}
    )
    r_unknown = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "noexiste@empresa.com"}
    )
    assert r_existing.status_code == 202
    assert r_unknown.status_code == 202
    assert r_existing.json() == r_unknown.json()


async def test_reset_password_flow(client):
    # Registramos y guardamos la cookie de sesión
    await register_user(client, email="reset@empresa.com")
    session_cookie = client.cookies.get("nm_refresh")

    # Emitimos un código de reseteo directamente (simula el correo recibido)
    _user_id, code_id = await insert_reset_code("reset@empresa.com", "123456")

    nuevo_password = "NuevaClave-987"
    res = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": "reset@empresa.com", "code": "123456", "new_password": nuevo_password},
    )
    assert res.status_code == 200, res.text

    # El login con la contraseña anterior falla; con la nueva, funciona
    r_old = await client.post("/api/v1/auth/login", json={"email": "reset@empresa.com", "password": PASSWORD})
    assert r_old.status_code == 401
    r_new = await client.post("/api/v1/auth/login", json={"email": "reset@empresa.com", "password": nuevo_password})
    assert r_new.status_code == 200

    # La sesión anterior fue revocada tras el reseteo
    client.cookies.set("nm_refresh", session_cookie, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401

    # El código queda consumido: no se puede reutilizar
    row = await code_row(code_id)
    assert row.consumed_at is not None
    assert row.attempts == 0


async def test_reset_password_token_single_use(client):
    await register_user(client, email="single@empresa.com")
    _user_id, code_id = await insert_reset_code("single@empresa.com", "042731")

    ok = {"email": "single@empresa.com", "code": "042731", "new_password": "OtraClave-123"}
    assert (await client.post("/api/v1/auth/reset-password", json=ok)).status_code == 200
    # Reutilizar el mismo código -> 400 genérico
    res = await client.post("/api/v1/auth/reset-password", json=ok)
    assert res.status_code == 400
    assert res.json()["detail"] == "El código es inválido o ha expirado"

    # Consumido en BD, y el contador de intentos sube (fallo contabilizado)
    row = await code_row(code_id)
    assert row.consumed_at is not None
    assert row.attempts == 1


async def test_reset_password_invalid_token(client):
    """Un código incorrecto REAL (6 dígitos) da 400 genérico.

    Ojo: un body con la forma antigua `{token, new_password}` ya no llega al
    endpoint —Pydantic exige `email` y `code`, así que devuelve 422. El caso que
    importa para la seguridad es el de abajo: existe una cuenta, hay un código
    emitido, pero el que se envía no es el bueno.
    """
    await register_user(client, email="victima@empresa.com")
    _user_id, _code_id = await insert_reset_code("victima@empresa.com", "123456")

    res = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": "victima@empresa.com", "code": "999999", "new_password": "OtraClave-123"},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "El código es inválido o ha expirado"

    # La contraseña no se ha tocado
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": "victima@empresa.com", "password": PASSWORD}
        )
    ).status_code == 200


async def test_reset_password_legacy_body_is_422(client):
    """El contrato nuevo es {email, code, new_password}: la forma antigua con
    `token` se rechaza por validación de schema (422), no por código inválido."""
    res = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "token-inventado-1234567890", "new_password": "OtraClave-123"},
    )
    assert res.status_code == 422
    faltantes = {e["loc"][-1] for e in res.json()["detail"]}
    assert {"email", "code"} <= faltantes


# ---------------------------------------------------------------------------
# Origin guard (defensa anti-CSRF)
# ---------------------------------------------------------------------------

async def test_origin_guard_blocks_unknown_origin(client):
    await register_user(client)
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "ana@empresa.com", "password": PASSWORD},
        headers={"Origin": "https://evil.example.com"},
    )
    assert res.status_code == 403


async def test_origin_guard_allows_frontend_origin(client):
    await register_user(client)
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "ana@empresa.com", "password": PASSWORD},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.status_code == 200