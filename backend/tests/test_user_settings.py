"""Suite de tests del panel de usuario: edición de perfil y sesiones activas.

Cubre: PATCH /me (persistencia, normalización, validación, aislamiento de
campos), GET /sessions (filtrado, `current`, estado vacío, aislamiento IDOR,
ausencia de token_hash) y POST /sessions/revoke-others (conservación de la
sesión actual, no-op con cookie ajena o caducada, 401 sin cookie y aislamiento
entre usuarios). Además fija invariantes de plataforma de estos endpoints:
cobertura del guard de Origin y rate limiting propio.
"""

from datetime import datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.main import create_app
from app.models import RefreshToken, User
from app.security import generate_refresh_token, new_refresh_expiry, sha256_hex, utcnow

PASSWORD = "Segura-1234"
EMAIL = "panel@empresa.com"
COOKIE = "nm_refresh"


async def register_user(client: AsyncClient, email: str = EMAIL) -> dict:
    """Registra un usuario y devuelve el cuerpo de la respuesta."""
    res = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Ana Gómez", "email": email, "password": PASSWORD},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def login_again(client: AsyncClient, email: str = EMAIL) -> str:
    """Abre una sesión adicional y devuelve el valor de la nueva cookie."""
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "remember_me": True},
    )
    assert res.status_code == 200, res.text
    return client.cookies.get(COOKIE)


def other_client() -> AsyncClient:
    """Segundo cliente HTTP (otra cookie jar) contra la misma app."""
    transport = ASGITransport(app=create_app(get_settings()))
    return AsyncClient(transport=transport, base_url="http://testserver")


async def live_session_ids(user_id: str) -> list[int]:
    """Ids de las sesiones no revocadas de un usuario, leídos de la BD."""
    async with SessionLocal() as db:
        rows = await db.execute(
            select(RefreshToken.id).where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
        )
        return list(rows.scalars())


async def session_id_of(user_id: str, token: str) -> int:
    """Id de la fila RefreshToken cuyo hash corresponde a `token`."""
    async with SessionLocal() as db:
        row = await db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(token))
        )
        assert row is not None and row.user_id == user_id
        return row.id


async def add_dead_sessions(user_id: str) -> None:
    """Inserta una sesión revocada y otra expirada (no deben listarse)."""
    _, revoked_hash = generate_refresh_token()
    _, expired_hash = generate_refresh_token()
    async with SessionLocal() as db:
        db.add(
            RefreshToken(
                user_id=user_id,
                token_hash=revoked_hash,
                expires_at=new_refresh_expiry(True),
                remember_me=True,
                revoked_at=utcnow(),
            )
        )
        db.add(
            RefreshToken(
                user_id=user_id,
                token_hash=expired_hash,
                expires_at=utcnow(),  # ya vencida
                remember_me=False,
            )
        )
        await db.commit()


async def add_session(
    user_id: str,
    expires_at: datetime,
    remember_me: bool = True,
) -> str:
    """Inserta una fila RefreshToken y devuelve el token en claro, que es
    justamente lo que el navegador recibiría en la cookie httpOnly."""
    token, token_hash = generate_refresh_token()
    async with SessionLocal() as db:
        db.add(
            RefreshToken(
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
                remember_me=remember_me,
            )
        )
        await db.commit()
    return token


async def add_session_of_other_user(
    email: str = "intruso@empresa.com",
    full_name: str = "Intruso",
) -> tuple[str, int]:
    """Crea otro usuario con una sesión viva. Devuelve (token, id_de_sesión)."""
    token, token_hash = generate_refresh_token()
    async with SessionLocal() as db:
        intruder = User(email=email, full_name=full_name, password_hash="x")
        db.add(intruder)
        await db.flush()
        row = RefreshToken(
            user_id=intruder.id,
            token_hash=token_hash,
            expires_at=new_refresh_expiry(True),
            remember_me=True,
        )
        db.add(row)
        await db.commit()
        return token, row.id


async def revoked_at_of(session_id: int):
    """Valor de revoked_at de la fila con ese id (None si sigue sin revocar)."""
    async with SessionLocal() as db:
        row = await db.scalar(select(RefreshToken).where(RefreshToken.id == session_id))
        assert row is not None
        return row.revoked_at


# ---------------------------------------------------------------------------
# PATCH /me — perfil
# ---------------------------------------------------------------------------

async def test_patch_me_updates_and_persists_full_name(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])

    res = await client.patch(
        "/api/v1/auth/me", json={"full_name": "Ana Gómez Prat"}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["full_name"] == "Ana Gómez Prat"

    # Persistido en la BD y visible en /me
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == EMAIL))
        assert user.full_name == "Ana Gómez Prat"
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.json()["full_name"] == "Ana Gómez Prat"


async def test_patch_me_normalizes_whitespace(client, auth_headers):
    body = await register_user(client)
    res = await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "   Ana    Gómez   Prat  "},
        headers=auth_headers(body["access_token"]),
    )
    assert res.status_code == 200
    assert res.json()["full_name"] == "Ana Gómez Prat"


async def test_patch_me_rejects_blank_name(client, auth_headers):
    body = await register_user(client)
    res = await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "     "},  # vacío tras normalizar
        headers=auth_headers(body["access_token"]),
    )
    assert res.status_code == 422


async def test_patch_me_rejects_short_name(client, auth_headers):
    body = await register_user(client)
    res = await client.patch(
        "/api/v1/auth/me", json={"full_name": "A"}, headers=auth_headers(body["access_token"])
    )
    assert res.status_code == 422


async def test_patch_me_requires_token(client):
    body = await register_user(client)
    assert (await client.patch("/api/v1/auth/me", json={"full_name": "Sin Token"})).status_code == 401
    assert (
        await client.patch(
            "/api/v1/auth/me",
            json={"full_name": "Token Malo"},
            headers={"Authorization": "Bearer token-invalido"},
        )
    ).status_code == 401
    # /me sigue intacto
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["full_name"] == "Ana Gómez"


async def test_patch_me_ignores_email_and_password(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])

    res = await client.patch(
        "/api/v1/auth/me",
        json={
            "full_name": "Ana Gómez Prat",
            "email": "secuestrado@empresa.com",
            "password": "OtraClave-123",
            "is_email_verified": False,
        },
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["email"] == EMAIL  # el email no se toca
    assert res.json()["full_name"] == "Ana Gómez Prat"

    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.id == body["user"]["id"]))
        assert user.email == EMAIL

    # La contraseña original sigue siendo la válida
    assert (await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})).status_code == 200
    assert (
        await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "OtraClave-123"})
    ).status_code == 401


async def test_patch_me_is_idempotent(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    for _ in range(2):
        res = await client.patch(
            "/api/v1/auth/me", json={"full_name": "Ana Gómez"}, headers=headers
        )
        assert res.status_code == 200
        assert res.json()["full_name"] == "Ana Gómez"


async def test_patch_me_rejects_too_long_name(client, auth_headers):
    """El límite de 120 caracteres lo impone el schema y la columna de la BD."""
    body = await register_user(client)
    res = await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "A" * 121},
        headers=auth_headers(body["access_token"]),
    )
    assert res.status_code == 422
    # 120 sí se aceptan (límite inclusivo)
    ok = await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "A" * 120},
        headers=auth_headers(body["access_token"]),
    )
    assert ok.status_code == 200


async def test_patch_me_rejects_wrong_type_name(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    for valor in (42, ["Ana", "Gómez"], {"nombre": "Ana"}, None, True):
        res = await client.patch(
            "/api/v1/auth/me", json={"full_name": valor}, headers=headers
        )
        assert res.status_code == 422, f"full_name={valor!r} debería ser 422"


async def test_patch_me_requires_full_name_field(client, auth_headers):
    """El campo es obligatorio: un body sin él no es un no-op silencioso."""
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    for payload in ({}, {"nombre": "Ana Gómez Prat"}, {"email": "otro@empresa.com"}):
        res = await client.patch("/api/v1/auth/me", json=payload, headers=headers)
        assert res.status_code == 422, f"body={payload!r} debería ser 422"
    # El nombre no se ha tocado
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.json()["full_name"] == "Ana Gómez"


async def test_patch_me_cannot_touch_identity_fields(client, auth_headers):
    """El modelo de request es cerrado, pero `id`, `is_active`, `password_hash`
    y `auth_provider` tampoco deben cambiar ni filtrarse en la respuesta."""
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    user_id = body["user"]["id"]

    async with SessionLocal() as db:
        antes = await db.scalar(select(User).where(User.id == user_id))
        estado_inicial = (antes.is_active, antes.password_hash, antes.auth_provider)
    assert estado_inicial[2] == "local"

    res = await client.patch(
        "/api/v1/auth/me",
        json={
            "full_name": "Ana Gómez Prat",
            "id": "00000000-0000-0000-0000-000000000000",
            "is_active": False,
            "password_hash": "$argon2id$hash-falso",
            "auth_provider": "google",
        },
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == user_id
    assert data["auth_provider"] == "local"
    # El esquema de respuesta no expone ni is_active ni password_hash
    assert set(data) == {
        "id",
        "email",
        "full_name",
        "auth_provider",
        "is_email_verified",
        "created_at",
    }

    async with SessionLocal() as db:
        despues = await db.scalar(select(User).where(User.id == user_id))
        assert despues.full_name == "Ana Gómez Prat"
        assert (despues.is_active, despues.password_hash, despues.auth_provider) == estado_inicial
        assert despues.id == user_id

    # Sigue siendo la misma cuenta: ni la desactivada ni con otro proveedor
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    assert (
        await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    ).status_code == 200


# ---------------------------------------------------------------------------
# GET /sessions — listado de sesiones activas
# ---------------------------------------------------------------------------

async def test_list_sessions_returns_only_active_sessions(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    user_id = body["user"]["id"]

    await login_again(client)  # 2ª sesión
    await login_again(client)  # 3ª sesión
    await add_dead_sessions(user_id)  # 1 revocada + 1 expirada (ocultas)

    res = await client.get("/api/v1/auth/sessions", headers=headers)
    assert res.status_code == 200
    body_sessions = res.json()
    assert len(body_sessions) == 3
    # Orden: la más reciente primero. Se comparan los valores reales de
    # created_at entre sí (no una lista ordenada contra sí misma) y contra el
    # orden de la BD, para que un orden por id no pase por alto el contrato.
    fechas = [datetime.fromisoformat(s["created_at"]) for s in body_sessions]
    assert all(anterior >= posterior for anterior, posterior in zip(fechas, fechas[1:]))
    async with SessionLocal() as db:
        orden_bd = list(
            (
                await db.execute(
                    select(RefreshToken.id)
                    .where(
                        RefreshToken.user_id == user_id,
                        RefreshToken.revoked_at.is_(None),
                        RefreshToken.expires_at > utcnow(),
                    )
                    .order_by(RefreshToken.created_at.desc(), RefreshToken.id.desc())
                )
            ).scalars()
        )
    assert [s["id"] for s in body_sessions] == orden_bd
    assert body_sessions[0]["id"] == max(orden_bd)
    assert set(body_sessions[0]) == {"id", "created_at", "expires_at", "remember_me", "current"}


async def test_list_sessions_never_exposes_token_hash(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    await login_again(client)

    cookie_token = client.cookies.get(COOKIE)
    res = await client.get("/api/v1/auth/sessions", headers=headers)
    assert res.status_code == 200

    raw = res.text
    assert "token_hash" not in raw
    # Tampoco el hash real del token (ni el token en claro)
    assert sha256_hex(cookie_token) not in raw
    assert cookie_token not in raw
    for session in res.json():
        assert set(session) == {"id", "created_at", "expires_at", "remember_me", "current"}


async def test_list_sessions_marks_current_session(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    user_id = body["user"]["id"]

    first_cookie = client.cookies.get(COOKIE)  # sesión del registro
    second_cookie = await login_again(client)  # sesión del login
    assert first_cookie != second_cookie

    res = await client.get("/api/v1/auth/sessions", headers=headers)
    sessions = res.json()
    assert len(sessions) == 2
    currents = [s for s in sessions if s["current"]]
    assert len(currents) == 1
    assert currents[0]["id"] == await session_id_of(user_id, second_cookie)

    # Cambiando la cookie de la petición, cambia la sesión marcada
    client.cookies.set(COOKIE, first_cookie, path="/api/v1/auth")
    res = await client.get("/api/v1/auth/sessions", headers=headers)
    currents = [s for s in res.json() if s["current"]]
    assert len(currents) == 1
    assert currents[0]["id"] == await session_id_of(user_id, first_cookie)


async def test_list_sessions_without_cookie_has_no_current(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    await login_again(client)
    client.cookies.clear()  # sin cookie de refresco (p. ej. /me con el access token)

    res = await client.get("/api/v1/auth/sessions", headers=headers)
    assert res.status_code == 200  # no es un error: simplemente no hay sesión actual
    assert len(res.json()) == 2
    assert all(s["current"] is False for s in res.json())


async def test_list_sessions_requires_token(client):
    assert (await client.get("/api/v1/auth/sessions")).status_code == 401


async def test_list_sessions_returns_empty_list_when_no_sessions(client, auth_headers):
    """Usuario sin ninguna sesión viva: 200 con lista vacía, no 404 ni error.
    Es el estado que la UI tiene implementado ("0 sesiones")."""
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    user_id = body["user"]["id"]

    # La única sesión (la del registro) deja de estar viva
    async with SessionLocal() as db:
        filas = await db.execute(
            select(RefreshToken).where(RefreshToken.user_id == user_id)
        )
        for fila in filas.scalars():
            fila.revoked_at = utcnow()
        await db.commit()

    client.cookies.clear()  # sin cookie: el 401 de revoke-others no aplica aquí
    res = await client.get("/api/v1/auth/sessions", headers=headers)
    assert res.status_code == 200
    assert res.json() == []


async def test_list_sessions_ignores_cookie_of_another_user(client, auth_headers):
    """Con la cookie de OTRO usuario el listado sigue siendo el propio: el
    filtro de `current` no debe ampliar el alcance de los datos (IDOR)."""
    body = await register_user(client, email="ana@empresa.com")
    headers = auth_headers(body["access_token"])
    user_id = body["user"]["id"]
    await login_again(client, email="ana@empresa.com")  # 2 sesiones de Ana
    propias = await live_session_ids(user_id)
    assert len(propias) == 2

    stolen_token, stolen_id = await add_session_of_other_user(
        email="bruno@empresa.com", full_name="Bruno"
    )
    client.cookies.set(COOKIE, stolen_token, path="/api/v1/auth")

    res = await client.get("/api/v1/auth/sessions", headers=headers)
    assert res.status_code == 200
    sessions = res.json()

    # Solo las sesiones de Ana, y ninguna marcada como actual
    assert {s["id"] for s in sessions} == set(propias)
    assert stolen_id not in {s["id"] for s in sessions}
    assert all(s["current"] is False for s in sessions)
    assert stolen_token not in res.text
    assert sha256_hex(stolen_token) not in res.text


# ---------------------------------------------------------------------------
# POST /sessions/revoke-others
# ---------------------------------------------------------------------------

async def test_revoke_others_keeps_current_session(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    user_id = body["user"]["id"]

    old_cookie = client.cookies.get(COOKIE)  # sesión antigua
    current_cookie = await login_again(client)  # sesión actual
    assert (await client.get("/api/v1/auth/sessions", headers=headers)).status_code == 200

    res = await client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
    assert res.status_code == 204
    assert res.content == b""

    # La cookie no se modifica ni se borra
    assert client.cookies.get(COOKIE) == current_cookie
    assert "set-cookie" not in res.headers

    # Solo queda la sesión actual
    sessions = (await client.get("/api/v1/auth/sessions", headers=headers)).json()
    assert len(sessions) == 1
    assert sessions[0]["current"] is True

    # En BD solo se ha revocado la sesión antigua
    async with SessionLocal() as db:
        rows = await db.execute(
            select(RefreshToken).where(RefreshToken.user_id == user_id)
        )
        revocadas = [r for r in rows.scalars() if r.revoked_at is not None]
        assert len(revocadas) == 1
        assert revocadas[0].token_hash == sha256_hex(old_cookie)

    # La sesión actual sigue viva: /me y /refresh funcionan
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200

    # La sesión antigua ya no refresca (esto dispara además la detección de
    # reutilización, que revoca en cascada; por eso el estado de BD se
    # comprueba antes)
    client.cookies.set(COOKIE, old_cookie, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_revoke_others_returns_204_with_nothing_to_revoke(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])

    # Solo existe la sesión de la cookie: no hay "otras" que revocar
    res = await client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
    assert res.status_code == 204
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200


async def test_revoke_others_without_cookie_returns_401(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    client.cookies.clear()

    res = await client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
    assert res.status_code == 401
    assert res.json()["detail"] == (
        "No hay sesión activa en este dispositivo. Vuelve a iniciar sesión."
    )


async def test_revoke_others_with_unrelated_cookie_does_not_touch_sessions(client, auth_headers):
    """Si la cookie no corresponde a una sesión viva del usuario, no se revoca
    nada: un cookie manipulado no puede dejar al usuario sin sesiones."""
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    user_id = body["user"]["id"]
    await login_again(client)
    before = await live_session_ids(user_id)
    assert len(before) == 2

    client.cookies.set(COOKIE, "token-inventado-1234567890", path="/api/v1/auth")
    res = await client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
    assert res.status_code == 204
    assert await live_session_ids(user_id) == before


async def test_revoke_others_with_cookie_from_another_user(client, auth_headers):
    """La cookie apunta a una sesión viva de OTRO usuario: como no pertenece al
    user_id autenticado, el endpoint hace no-op en vez de tirar las propias."""
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    user_id = body["user"]["id"]
    await login_again(client)
    before = await live_session_ids(user_id)
    assert len(before) == 2

    stolen_token, stolen_id = await add_session_of_other_user()
    client.cookies.set(COOKIE, stolen_token, path="/api/v1/auth")
    res = await client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
    assert res.status_code == 204

    # Ni las sesiones propias ni las del intruso se tocan
    assert await live_session_ids(user_id) == before
    assert await revoked_at_of(stolen_id) is None


async def test_revoke_others_with_expired_cookie_does_not_touch_live_sessions(
    client, auth_headers
):
    """Cookie CADUCADA pero no revocada: no es una sesión válida, así que el
    guard debe hacer no-op como con una cookie ajena. Si se aceptara, el UPDATE
    revocaría las sesiones vivas restantes y el usuario se quedaría sin ninguna
    (y /sessions mostraría 0 con el aviso de éxito)."""
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    user_id = body["user"]["id"]

    register_id = await session_id_of(user_id, client.cookies.get(COOKIE))
    live_cookie = await login_again(client)  # sesión viva de 30 días
    live_id = await session_id_of(user_id, live_cookie)
    live_token_hash = sha256_hex(live_cookie)
    vivas = {register_id, live_id}

    # Sesión caducada (expires_at en el pasado) y NO revocada, presentada como
    # la cookie de la petición.
    expired_token = await add_session(
        user_id, utcnow() - timedelta(minutes=1), remember_me=True
    )
    expired_id = await session_id_of(user_id, expired_token)

    # Estado previo: la caducada ni se lista, las dos vivas sí.
    previas = (await client.get("/api/v1/auth/sessions", headers=headers)).json()
    assert {s["id"] for s in previas} == vivas

    client.cookies.set(COOKIE, expired_token, path="/api/v1/auth")
    res = await client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
    assert res.status_code == 204

    # Las sesiones VIVAS siguen sin revocarse...
    assert await revoked_at_of(live_id) is None
    assert await revoked_at_of(register_id) is None
    # ...y la caducada tampoco se toca (no-op completo, como con cookie ajena)
    assert await revoked_at_of(expired_id) is None
    async with SessionLocal() as db:
        fila = await db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == live_token_hash)
        )
        assert fila.revoked_at is None

    # /sessions las sigue listando: la UI no puede mostrar "0 sesiones"
    listado = (await client.get("/api/v1/auth/sessions", headers=headers)).json()
    assert {s["id"] for s in listado} == vivas
    assert all(s["current"] is False for s in listado)  # la caducada no es la actual

    # Y la sesión viva conserva su credencial: /refresh funciona
    client.cookies.set(COOKIE, live_cookie, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200


# ---------------------------------------------------------------------------
# Aislamiento entre usuarios
# ---------------------------------------------------------------------------

async def test_sessions_are_isolated_between_users(client, auth_headers):
    body = await register_user(client, email="ana@empresa.com")
    headers = auth_headers(body["access_token"])
    await login_again(client, email="ana@empresa.com")  # 2 sesiones de Ana

    async with other_client() as client_b:
        b = await register_user(client_b, email="bruno@empresa.com")
        await client_b.post(
            "/api/v1/auth/login",
            json={"email": "bruno@empresa.com", "password": PASSWORD},
        )  # 2 sesiones de Bruno
        b_headers = auth_headers(b["access_token"])

        # Listado: cada usuario solo ve lo suyo
        ana = (await client.get("/api/v1/auth/sessions", headers=headers)).json()
        bruno = (await client_b.get("/api/v1/auth/sessions", headers=b_headers)).json()
        assert len(ana) == 2 and len(bruno) == 2
        assert not {s["id"] for s in ana} & {s["id"] for s in bruno}

        # `current` se comprueba identificando la sesión, no su posición: así el
        # test no depende del desempate por id de SQLite.
        for listado, user_id, cookie in (
            (ana, body["user"]["id"], client.cookies.get(COOKIE)),
            (bruno, b["user"]["id"], client_b.cookies.get(COOKIE)),
        ):
            marcadas = [s["id"] for s in listado if s["current"]]
            assert len(marcadas) == 1
            assert marcadas[0] == await session_id_of(user_id, cookie)
            assert all(s["current"] is False for s in listado if s["id"] != marcadas[0])

        # Revocar las de Ana no toca las de Bruno
        assert (
            await client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
        ).status_code == 204
        assert len((await client.get("/api/v1/auth/sessions", headers=headers)).json()) == 1
        assert len((await client_b.get("/api/v1/auth/sessions", headers=b_headers)).json()) == 2

        # Bruno sigue pudiendo refrescar
        assert (await client_b.post("/api/v1/auth/refresh")).status_code == 200


# ---------------------------------------------------------------------------
# Guard de Origin (defensa anti-CSRF)
#
# La cobertura de estos endpoints depende de que sigan bajo el prefijo
# /api/v1/auth: origin_guard solo intercepta mutaciones bajo esa ruta. Estos
# tests fijan esa invariante para que mover PATCH /me o revoke-others fuera del
# router de auth no desactive la defensa en silencio.
# ---------------------------------------------------------------------------

async def test_origin_guard_blocks_profile_update(client, auth_headers):
    body = await register_user(client)
    res = await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Ana Secuestrada"},
        headers={**auth_headers(body["access_token"]), "Origin": "https://evil.example"},
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "Origen no permitido"
    me = await client.get("/api/v1/auth/me", headers=auth_headers(body["access_token"]))
    assert me.json()["full_name"] == "Ana Gómez"  # el cambio no se aplicó


async def test_origin_guard_allows_profile_update_from_frontend(client, auth_headers):
    body = await register_user(client)
    res = await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Ana Gómez Prat"},
        headers={**auth_headers(body["access_token"]), "Origin": "http://localhost:5173"},
    )
    assert res.status_code == 200


async def test_origin_guard_blocks_revoke_others(client, auth_headers):
    body = await register_user(client)
    headers = auth_headers(body["access_token"])
    await login_again(client)  # 2ª sesión que un atacante intentaría revocar

    res = await client.post(
        "/api/v1/auth/sessions/revoke-others",
        headers={**headers, "Origin": "https://evil.example"},
    )
    assert res.status_code == 403
    assert len((await client.get("/api/v1/auth/sessions", headers=headers)).json()) == 2


# ---------------------------------------------------------------------------
# Rate limiting de los endpoints del panel
#
# El cubo se indexa por IP + ruta, así que cada endpoint lleva su propio
# contador aunque comparta clave en la configuración.
# ---------------------------------------------------------------------------

async def test_panel_endpoints_rate_limited(auth_headers):
    """Los 3 endpoints del panel tienen rate limiting: responden bien por debajo
    del umbral y 429 al superarlo. Si su clave faltara en el dict `limits` de
    make_rate_limit_dependency, esto devolvería 500 (KeyError) en vez de 429."""
    app = create_app(
        Settings(
            rate_limit_profile_max=1,
            rate_limit_profile_window=60,
            rate_limit_sessions_max=1,
            rate_limit_sessions_window=60,
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        body = await register_user(c)
        headers = auth_headers(body["access_token"])

        # PATCH /me
        r1 = await c.patch("/api/v1/auth/me", json={"full_name": "Ana Gómez"}, headers=headers)
        r2 = await c.patch("/api/v1/auth/me", json={"full_name": "Ana Gómez"}, headers=headers)
        assert r1.status_code == 200
        assert r2.status_code == 429
        assert "Retry-After" in r2.headers

        # GET /sessions
        r3 = await c.get("/api/v1/auth/sessions", headers=headers)
        r4 = await c.get("/api/v1/auth/sessions", headers=headers)
        assert r3.status_code == 200
        assert len(r3.json()) == 1
        assert r4.status_code == 429

        # POST /sessions/revoke-others
        r5 = await c.post("/api/v1/auth/sessions/revoke-others", headers=headers)
        r6 = await c.post("/api/v1/auth/sessions/revoke-others", headers=headers)
        assert r5.status_code == 204
        assert r6.status_code == 429


async def test_panel_endpoints_use_production_limits_by_default(client, auth_headers):
    """Los 3 endpoints usan los umbrales de producción (conftest no los pisa):
    responden con normalidad, sin 500 por clave ausente ni 429 inesperado."""
    settings = get_settings()
    assert (settings.rate_limit_profile_max, settings.rate_limit_profile_window) == (20, 60)
    assert (settings.rate_limit_sessions_max, settings.rate_limit_sessions_window) == (30, 60)

    body = await register_user(client)
    headers = auth_headers(body["access_token"])

    assert (
        await client.patch(
            "/api/v1/auth/me", json={"full_name": "Ana Gómez"}, headers=headers
        )
    ).status_code == 200
    assert (await client.get("/api/v1/auth/sessions", headers=headers)).status_code == 200
    assert (
        await client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
    ).status_code == 204
