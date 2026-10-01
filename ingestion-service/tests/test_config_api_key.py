"""Paso 0.2 — `API_KEY` con fallo cerrado (antes: fallo abierto).

Antes de este paso, `app/config.py` hacía `os.environ.get("API_KEY",
"cambia-esta-clave")`: si la variable no estaba, el servicio **arrancaba** con una
clave que cualquiera que leyera el repositorio conoce, y el fallo solo aparecía
en el 401 de la primera petición.

Ahora `_api_key()` aborta el import con `RuntimeError` si la clave falta, está
vacía, es solo espacios o es el valor de ejemplo.

**Por qué en subproceso y no con `importlib.reload`:** `app/config.py` lee el
entorno al importarse y `conftest.py` ya ha fijado una `API_KEY` válida en
`os.environ`; `sys.modules` la cachea, así que un `reload` dentro de este
proceso seguiría viendo la clave buena. La única forma de observar el arranque
con un entorno limpio es un proceso nuevo: por eso todo esto va por
`subprocess.run([sys.executable, "-c", "import app.config"], env=...)`.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

#: Raíz del repo de ingestion-service: el padre de `tests/`. Es el directorio
#: desde el que `import app` tiene que resolver.
RAIZ = Path(__file__).resolve().parent.parent

#: Valor de ejemplo que estaba publicado en el repo (`app.config.API_KEY_EJEMPLO`).
VALOR_EJEMPLO = "cambia-esta-clave"

#: Variables que `app/config.py` lee después de validar la clave. El subproceso
#: necesita un entorno completo: si no, el camino feliz acabaría con un
#: `KeyError: 'DATABASE_URL'` y el test pasaría por el motivo equivocado.
_VARIABLES_QUE_TAMBIEN_HACE_FALTA = {
    "DATABASE_URL": "postgresql+psycopg://ingesta:ingesta@localhost:5432/ingesta_test",
    "REDIS_URL": "redis://localhost:6379/15",
    "STORAGE_DIR": "/tmp/ingestion-test-config",
}

#: Los mensajes tienen acentos ("API_KEY no está definida"), así que el decode no
#: puede depender del locale del contenedor: en un `LANG` no UTF-8, `text=True`
#: lanzaría `UnicodeDecodeError` y el test fallaría por el motivo equivocado.
_DECODE = {"encoding": "utf-8", "errors": "replace"}


def _arrancar_config(api_key: str | None) -> subprocess.CompletedProcess:
    """Arranca `import app.config` en un subproceso con entorno limpio.

    `api_key=None` deja la variable **sin definir**, que no es lo mismo que
    vacía: es el caso "el .env no la tiene".
    """
    env = {
        k: v
        for k, v in os.environ.items()
        # Se borra todo lo que venga de fuera: ni el `API_KEY` de `conftest.py`
        # ni el `DATABASE_URL` del `.env` de compose se cuelan en la prueba.
        if k not in {"API_KEY", *_VARIABLES_QUE_TAMBIEN_HACE_FALTA}
    }
    env.update(_VARIABLES_QUE_TAMBIEN_HACE_FALTA)
    env["PYTHONPATH"] = str(RAIZ)  # por si `app` no está en el sys.path
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if api_key is not None:
        env["API_KEY"] = api_key
    return subprocess.run(
        [sys.executable, "-c", "import app.config; print('IMPORTADO')"],
        env=env,
        cwd=str(RAIZ),
        capture_output=True,
        timeout=60,
        **_DECODE,
    )


def _aborta(proc: subprocess.CompletedProcess) -> None:
    """Comprueba que el proceso murió de verdad, y no con un warning ignorable."""
    assert proc.returncode != 0, (
        "el proceso debería haber abortado al importar app.config, pero salió "
        f"con {proc.returncode}.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    # Sin esto, un `raise` silencioso o un warning pasarían por un fallo abierto.
    assert "IMPORTADO" not in proc.stdout, "el import llegó a completarse"
    assert "RuntimeError" in proc.stderr, (
        f"el fallo no es un RuntimeError explícito:\n{proc.stderr}"
    )


# --- El fallo cerrado: cuatro formas de no tener una clave utilizable ---------
def test_api_key_sin_definir_aborta():
    """La variable no está: el arranque tiene que morir, no usar un default."""
    _aborta(_arrancar_config(None))


def test_api_key_vacia_aborta():
    """`API_KEY=` en el `.env` es tan mala como no tenerla."""
    _aborta(_arrancar_config(""))


def test_api_key_de_espacios_aborta():
    """Solo espacios: `.strip()` los convierte en cadena vacía.

    Sin el `strip`, una clave de un espacio en blanco pasaría el `if not key` y el
    servicio arrancaría con una credencial que cualquiera puede adivinar.
    """
    _aborta(_arrancar_config("   "))


def test_api_key_de_ejemplo_aborta():
    """El valor de ejemplo publicado en el repo es una clave pública."""
    proc = _arrancar_config(VALOR_EJEMPLO)
    _aborta(proc)
    assert "publicado en el repositorio" in proc.stderr, (
        f"el mensaje no explica por qué:\n{proc.stderr}"
    )
    assert VALOR_EJEMPLO in proc.stderr, "el mensaje no dice qué clave hay que cambiar"
    # Es un caso distinto al de "no está definida": si los dos casos dieran el
    # mismo error, el mensaje estaría mintiendo sobre la causa.
    assert "no está definida" not in proc.stderr, (
        f"una clave definida no puede reportarse como ausente:\n{proc.stderr}"
    )


def test_api_key_de_ejemplo_rodeada_de_espacios_aborta():
    """`"  cambia-esta-clave  "` es el mismo fallo.

    Cubre que el `strip()` de la línea 22 ocurre **antes** de comparar con
    `API_KEY_EJEMPLO`: si el orden fuera el contrario, esa variante pasaría el
    filtro con el mismo fallo abierto que este paso viene a cerrar.
    """
    proc = _arrancar_config(f"  {VALOR_EJEMPLO}  ")
    _aborta(proc)
    assert "publicado en el repositorio" in proc.stderr, (
        f"rodeada de espacios no se ha reconocido como valor de ejemplo:\n{proc.stderr}"
    )


# --- El camino feliz ---------------------------------------------------------
def test_api_key_valida_arranca_y_se_devuelve():
    """Con una clave real, el import termina y `API_KEY` vale lo que se puso."""
    clave = "clave-real-de-prueba-8fj2KQ"
    proc = _arrancar_config(clave)
    assert proc.returncode == 0, (
        f"una clave válida no debería impedir arrancar:\n{proc.stderr}"
    )
    assert "IMPORTADO" in proc.stdout

    # Y el valor sale del módulo, sin espacios alrededor: el `strip()` del
    # entorno no debe colarse en la credencial que se compara en cada request.
    valor = subprocess.run(
        [sys.executable, "-c", "import app.config; print(repr(app.config.API_KEY))"],
        env={**os.environ, **_VARIABLES_QUE_TAMBIEN_HACE_FALTA, "API_KEY": f"  {clave}  "},
        cwd=str(RAIZ),
        capture_output=True,
        timeout=60,
        **_DECODE,
    )
    assert valor.returncode == 0, valor.stderr
    assert valor.stdout.strip() == repr(clave)


def test_api_key_del_proceso_de_test_es_valida():
    """Sanidad: la clave que fija `conftest.py` supera `_api_key()`.

    Si este test falla, el problema no está en los casos anteriores sino en el
    entorno: la suite entera depende de que importar `app.config` funcione.
    """
    from app import config

    assert config.API_KEY and config.API_KEY != VALOR_EJEMPLO
    assert re.fullmatch(r"\S+", config.API_KEY), "la clave no debería llevar espacios"


def test_no_hay_default_en_el_codigo():
    """Guardarraíl de futuro: nadie reintroduce una clave por defecto.

    Es el bug original (`os.environ.get("API_KEY", "cambia-esta-clave")`), y un
    test de comportamiento no lo detectaría del todo: basta con que alguien
    reintroduzca el default con otro texto y los tests de arriba seguirían
    verdes mientras el fallo abierto vuelve.
    """
    fuente = (RAIZ / "app" / "config.py").read_text(encoding="utf-8")
    assert not re.search(r"os\.environ\.get\(\s*[\"']API_KEY[\"']\s*,", fuente), (
        "app/config.py vuelve a tener un valor por defecto para API_KEY: "
        "eso es el fallo abierto del Paso 0.2."
    )
