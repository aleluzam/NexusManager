"""Configuración del entorno de tests (debe importarse antes que la app).

Los módulos de la app leen la configuración al importarse, por lo que las
variables de entorno se fijan aquí, arriba del todo.
"""

import os

os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/nexus_test.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-1234567890abcdef1234567890abcdef")
# Límites generosos para la batería general de tests.
os.environ.setdefault("RATE_LIMIT_LOGIN_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_REGISTER_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_REFRESH_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_LOGOUT_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_FORGOT_MAX", "1000")
os.environ.setdefault("RATE_LIMIT_RESET_MAX", "1000")
os.environ.setdefault("ACCESS_TOKEN_MINUTES", "15")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.database import Base, engine
from app.main import create_app


@pytest_asyncio.fixture
async def client():
    """Cliente HTTP contra la app global (ASGITransport)."""
    app = create_app(get_settings())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


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
    for name in ("nexus", "nexus.auth"):
        logger = __import__("logging").getLogger(name)
        logger.propagate = True