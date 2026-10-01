"""Configuración del entorno de tests (debe importarse antes que la app).

Los módulos de la app leen la configuración al importarse, por lo que las
variables de entorno se fijan aquí, arriba del todo.

Aislamiento de red: TODAS las apps que se construyen aquí reciben un
`RecordingSender` en `app.state.email_sender`. Ningún test puede alcanzar la
red, ni por accidente: el emisor real (`ConsoleSender`/`ResendSender`) no llega
nunca a `app.state` en una prueba, y `test_verification.py` comprueba además que
la factoría no construye un `ResendSender` fuera de producción.
"""

import os

os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/nexus_test.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-1234567890abcdef1234567890abcdef")
# Límites generosos para la batería general de tests. Los tests que QUIEREN
# provocar un 429 no mueven estos valores: construyen su propia app con
# `make_client(rate_limit_..._max=N)`, porque el limitador vive en
# `app.state.rate_limiter` y por tanto es por app, no global.
os.environ.setdefault("RATE_LIMIT_LOGIN_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_REGISTER_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_REFRESH_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_LOGOUT_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_FORGOT_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_RESET_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_VERIFY_EMAIL_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_RESEND_VERIFICATION_MAX", "1000")
os.environ.setdefault("ACCESS_TOKEN_MINUTES", "15")

import re

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.database import Base, engine
from app.main import create_app
from app.services.email import EmailMessage

#: El código aparece en el texto plano del email en una línea propia, con
#: sangría: es la forma más robusta de leerlo del mensaje sin depender del HTML.
_CODE_LINE_RE = re.compile(r"^[ \t]*(\d{6})[ \t]*$", re.MULTILINE)


class RecordingSender:
    """Emisor de email falso: guarda los mensajes en memoria y no sale a la red.

    Cumple el contrato `EmailSender` (send -> id). Si se le fija `error`, el
    siguiente `send` lo lanza sin registrar nada, que es como se reproduce un
    proveedor caído.
    """

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.error: Exception | None = None

    async def send(self, message: EmailMessage) -> str:
        if self.error is not None:
            raise self.error
        self.messages.append(message)
        return f"recorded-{len(self.messages)}"

    # --- Inspección para los tests -----------------------------------------
    @property
    def last(self) -> EmailMessage:
        assert self.messages, "no se ha enviado ningún email"
        return self.messages[-1]

    def codes(self) -> list[str]:
        """Códigos de 6 dígitos de cada email enviado, en orden de envío."""
        return [_extract_code(m) for m in self.messages]

    def last_code(self) -> str:
        """Código del último email enviado."""
        return _extract_code(self.last)

    def purpose_of_last(self) -> str | None:
        """Valor del tag `purpose` del último email (vía de los metadatos)."""
        for tag in self.last.tags:
            if tag.get("name") == "purpose":
                return tag.get("value")
        return None


def _extract_code(message: EmailMessage) -> str:
    match = _CODE_LINE_RE.search(message.text)
    assert match, (
        "el email no lleva el código en una línea propia:\n" + message.text
    )
    return match.group(1)


def _build_app(**overrides) -> tuple[FastAPI, RecordingSender]:
    """App con `Settings` explícitos (heredando los del entorno de test) y un
    emisor falso inyectado. `overrides` se aplican sobre `get_settings()`, así
    que basta con pasar lo que el test quiere cambiar."""
    cfg = Settings(**{**get_settings().model_dump(), **overrides})
    app = create_app(cfg)
    sender = RecordingSender()
    app.state.email_sender = sender
    return app, sender


@pytest_asyncio.fixture
async def make_client():
    """Fábrica de clientes HTTP con configuración propia.

    Devuelve un `RecordingSender` y la app junto al cliente, para poder
    comprobar el emisor y usar `app.state` en los tests. Todos los clientes
    que se crean con ella se cierran al terminar el test.
    """
    clients: list[AsyncClient] = []

    def factory(**overrides) -> tuple[AsyncClient, RecordingSender, FastAPI]:
        app, sender = _build_app(**overrides)
        client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )
        # Mismo acceso que en el fixture `client`: el emisor falso es
        # consultable desde el test.
        client.email_sender = sender
        clients.append(client)
        return client, sender, app

    yield factory
    for client in clients:
        await client.aclose()


@pytest_asyncio.fixture
async def client(make_client):
    """Cliente HTTP contra la app de test (ASGITransport), sin red.

    El emisor falso queda accesible como `client.email_sender`.
    """
    c, _sender, _app = make_client()
    yield c


@pytest.fixture(scope="session", autouse=True)
def test_database_guard():
    """Comprobación de seguridad: los tests NUNCA pueden apuntar a la base de
    datos real (`backend/nexus.db`).

    `os.environ.setdefault` de arriba fija `DATABASE_URL` antes de que se importe
    `app.database`, así que el motor real de los tests es `/tmp/nexus_test.db`.
    Este guard convierte en un fallo ruidoso cualquier desvío futuro (por ejemplo,
    alguien exportando `DATABASE_URL` antes de correr pytest, o el `default` de
    `app.config` si se tocara el orden de los imports), en vez de escribir
    silenciosamente sobre los datos de desarrollo.
    """
    url = str(engine.url)
    assert "/tmp/nexus_test.db" in url, (
        f"los tests se conectarían a la base de datos real: {url}. "
        "No se debe ejecutar la suite así (se sobrescribiría nexus.db)."
    )


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    """Base de datos limpia antes de cada test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    # también tras el test para no contaminar entre tests
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def auth_headers():
    def _make(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return _make


def pytest_configure(config):
    for name in ("nexus", "nexus.auth", "nexus.email"):
        logger = __import__("logging").getLogger(name)
        logger.propagate = True
