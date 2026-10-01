"""Entorno, base de datos y aislamiento de los tests de ingestion-service.

Tres cosas hacen que esta suite no sea una batería de tests cualquiera:

1. **El entorno se fija ANTES de importar `app.*`.** `app/config.py` lee
   `os.environ` al importarse y, desde el Paso 0.2, **aborta el proceso** si
   `API_KEY` falta o es el valor de ejemplo. Si el orden se invierte, la suite
   entera muere en el import con un `RuntimeError` que no parece un fallo de
   tests. Por eso el bloque de `os.environ` va literalmente lo primero, sin
   imports de `app` por encima.

2. **Los `setdefault` no valen aquí: `docker compose` inyecta el `.env` real.**
   El contenedor de `api` arranca con `DATABASE_URL=.../ingesta` y
   `STORAGE_DIR=/storage` (ver `.env` / `.env.example`), y `.env` está en el
   entorno del proceso de pytest. Un `setdefault` no tocaría nada y los tests
   escribirían **sobre la base de datos de desarrollo**, que es exactamente lo
   que este fichero existe para impedir. Por eso `DATABASE_URL`, `STORAGE_DIR` y
   `API_KEY` se asignan con `os.environ[...] = ...` (no `setdefault`), y hay un
   **doble** guard: uno al importar este fichero (el que de verdad frena el
   `drop_all`) y otro fixture de sesión sobre el motor ya creado.

3. **Postgres de verdad, no SQLite.** `app/models.py` usa
   `Vector(1024)` de pgvector: en SQLite `create_all` ni siquiera crearía la
   tabla `clips`. Aquí se crea una base de datos **aparte** (`ingesta_test`)
   conectándose a `ingesta` como administrador, y se apunta ahí el
   `DATABASE_URL` de los tests.
"""

import os
import shutil
import tempfile

# SQLAlchemy sí puede importarse antes del guard: no lee el entorno ni crea
# ningún motor. Lo que no puede importarse todavía es `app.*` (ver punto 1).
from sqlalchemy.engine import make_url  # noqa: E402

# --------------------------------------------------------------------------
# 1. ENTORNO. Esto tiene que ser lo primero del fichero, antes de cualquier
#    `import app.*` (ver punto 1 del docstring).
# --------------------------------------------------------------------------

#: Base de datos dedicada a los tests. El guard de abajo aborta la suite si el
#: motor acaba apuntando a cualquier otra.
TEST_DB_NAME = os.environ.get("INGESTION_TEST_DB", "ingesta_test")

#: Credenciales/host del Postgres de compose. Se pueden sobrescribir por entorno
#: para correr la suite fuera de docker, pero eso no salta el guard: si alguien
#: pasa la base de desarrollo, la suite muere al importar en vez de destruir datos
#: en silencio.
_TEST_DB_URL = os.environ.get(
    "INGESTION_TEST_DATABASE_URL",
    f"postgresql+psycopg://ingesta:ingesta@postgres:5432/{TEST_DB_NAME}",
)

# `tempfile.mkdtemp` en vez de `/storage`: aunque un test se olvidara de pedir el
# fixture `storage_dir`, nunca puede escribir en el volumen real.
_STORAGE_TMP = tempfile.mkdtemp(prefix="ingestion-tests-storage-")

#: `DATABASE_URL` **antes** de sobrescribirla: es la base de desarrollo que
#: docker compose mete en el entorno desde el `.env`. Se guarda solo para poder
#: compararla más abajo; a partir de aquí la variable ya no dice nada de ella.
_DEV_DATABASE_URL = os.environ.get("DATABASE_URL", "")

os.environ["API_KEY"] = os.environ.get("INGESTION_TEST_API_KEY", "clave-solo-para-tests-no-publicada")
os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ["REDIS_URL"] = os.environ.get("INGESTION_TEST_REDIS_URL", "redis://redis:6379/15")
os.environ["STORAGE_DIR"] = _STORAGE_TMP
# `MAX_UPLOAD_BYTES` se deriva de esto; con `setdefault` porque no afecta a la
# seguridad de la suite y así se ve el valor real de producción.
os.environ.setdefault("MAX_UPLOAD_MB", "4096")

# --------------------------------------------------------------------------
# 1-bis. GUARD DE SEGURIDAD, antes de que exista siquiera el engine.
#
# Va aquí y no solo como fixture porque el aislamiento por test hace
# `drop_all`: si la base es la equivocada, tiene que morir el **import** de
# este fichero, que es el último momento en el que todavía no se ha escrito nada.
# Un fixture de sesión llegaría tarde, porque pytest puede montar los fixtures de
# función (y con ellos el `drop_all`) aunque uno de sesión haya fallado.
# --------------------------------------------------------------------------
_dev_db = make_url(_DEV_DATABASE_URL).database if _DEV_DATABASE_URL else None
_test_db = make_url(_TEST_DB_URL).database

if _dev_db and _dev_db == _test_db:
    raise RuntimeError(
        f"los tests apuntan a {_test_db!r}, que es la base de DESARROLLO (la de "
        "DATABASE_URL del entorno). El aislamiento por test hace drop_all/create_all: "
        "se perderían los datos. No se debe ejecutar la suite así."
    )
if _test_db != TEST_DB_NAME:
    raise RuntimeError(
        f"la base de datos de tests es {_test_db!r} pero debería ser "
        f"{TEST_DB_NAME!r}. ¿Se ha pasado INGESTION_TEST_DATABASE_URL a mano?"
    )
if not _test_db:
    raise RuntimeError("la URL de la base de datos de tests no trae nombre de base")

# --------------------------------------------------------------------------
# 2. Imports de la app. A partir de aquí el entorno ya está cerrado.
# --------------------------------------------------------------------------
import psycopg  # noqa: E402
import pytest  # noqa: E402

from app import storage  # noqa: E402
from app.db import Base, engine, init_db  # noqa: E402

# Registra las tablas en `Base.metadata` (idempotente: los modelos se importan
# una vez aunque se repita el import).
from app import models  # noqa: E402,F401


def _crear_base_de_datos(url_test: str) -> None:
    """Crea la base de datos de tests si no existe.

    Se conecta a la base `postgres` del **mismo** servidor con el usuario del
    propio servicio (en el compose es el superuser del Postgres de la imagen).
    `CREATE DATABASE` no admite transacción, de ahí el `autocommit`.

    Ojo con `make_url().set(...)`: devuelve una URL **nueva**, así que el nombre
    de la base de test hay que sacarlo antes de cambiarla por `postgres`.
    """
    url = make_url(url_test)
    nombre_test = url.database
    dsn_admin = (
        url.set(database="postgres")
        .render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )
    with psycopg.connect(dsn_admin, autocommit=True, connect_timeout=10) as conn:
        existe = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (nombre_test,)
        ).fetchone()
        if not existe:
            conn.execute(f'CREATE DATABASE "{nombre_test}"')


_crear_base_de_datos(_TEST_DB_URL)


# --------------------------------------------------------------------------
# 3. Fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def esquema_una_vez():
    """Crea la extensión `vector` y el esquema, una vez por sesión.

    `init_db()` es el arranque de producción: `CREATE EXTENSION vector` +
    `create_all`. La extensión **no** se puede crear dentro de una transacción
    y, sobre todo, tiene que existir ANTES de que `create_all` intente crear la
    columna `clips.embedding` (`Vector(1024)`).

    Se ejecuta antes que cualquier fixture de función, así que el `drop_all` +
    `create_all` de `db_limpia` no se pelea con nadie.
    """
    init_db()
    yield


@pytest.fixture(scope="session", autouse=True)
def test_database_guard():
    """Falla ruidosamente si los tests apuntan a la base de desarrollo.

    Mismo propósito que el guard de `backend/tests/conftest.py`, y por el mismo
    motivo: convierte en un fallo ruidoso y temprano cualquier desvío (un
    `DATABASE_URL` exportado antes de pytest, un `setdefault` que alguien
    cambiara por `os.environ.get`, una constante tocada en `app/config.py`) en
    vez de un `drop_all` silencioso sobre los datos de desarrollo.

    Se mira el motor de verdad (`app.db.engine`), no la variable de entorno: lo
    que importa es a dónde va a escribir SQL, no qué pone el entorno.
    """
    base = engine.url.database
    assert base == TEST_DB_NAME, (
        f"los tests se conectarían a la base de datos real {base!r}, "
        f"y el aislamiento por test hace drop_all/create_all: se perderían datos. "
        f"Se esperaba {TEST_DB_NAME!r}. No se debe ejecutar la suite así."
    )


@pytest.fixture(autouse=True)
def db_limpia():
    """Base de datos limpia antes y después de cada test.

    `app/db.py` crea el engine al importarse (no hay dependency_injector ni
    fixtures de sesión que sobreescribir), así que el aislamiento por test es
    `drop_all` + `create_all` contra ese engine, igual que hace
    `backend/tests/conftest.py`. Se repite después del test para no dejar filas
    que contaminen al siguiente, ni aunque uno falle.
    """
    with engine.begin() as conn:
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
    yield
    with engine.begin() as conn:
        Base.metadata.drop_all(conn)


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    """Apunta `STORAGE_DIR` a un `tmp_path` y devuelve ese directorio.

    `app/config.py` congela `STORAGE_DIR` al importarse y `app/storage.py` lo
    importa por valor, así que llegar al entorno no basta: hay que sustituir la
    referencia en el módulo que la usa (`dir_originales`/`guardar_upload` leen la
    global de `app.storage`). monkeypatch lo devuelve solo al terminar el test.

    Con esto, ningún test puede escribir en `/storage` aunque alguien pase por
    alto el fixture: el valor por defecto del entorno ya es un tmpdir.
    """
    destino = tmp_path / "storage"
    destino.mkdir()
    monkeypatch.setattr(storage, "STORAGE_DIR", str(destino))
    monkeypatch.setenv("STORAGE_DIR", str(destino))
    return destino


# --------------------------------------------------------------------------
# 4. Utilidades compartidas
# --------------------------------------------------------------------------
@pytest.fixture
def db():
    """Sesión de SQLAlchemy propia del test (cierre garantizado)."""
    from app.db import SessionLocal

    sesion = SessionLocal()
    try:
        yield sesion
    finally:
        sesion.close()


@pytest.fixture(scope="session")
def ffmpeg_disponible():
    """Si no hay ffmpeg/ffprobe, los tests que los necesitan hacen `skip`.

    La imagen del servicio los instala (`Dockerfile`), pero la suite también
    debería poder correr en un entorno sin ellos sin romperse: en ese caso
    fallar sería mentir, porque el bug del Paso 0.6 no está en ffmpeg.
    """
    falta = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if falta:
        pytest.skip(f"no hay {'/'.join(falta)} en el PATH")
    return True
