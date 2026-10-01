"""Aplicación FastAPI de NexusManager — factory configurable (aislable en tests)."""

import logging
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.rate_limit import InMemoryRateLimiter, RateLimitExceeded, rate_limit_exceeded_handler
from app.config import Settings, get_settings
from app.services.email import build_email_sender

#: Marca puesta en los handlers que instala `configure_app_logging`, para
#: poder retirarlos si el lifespan se ejecuta más de una vez en el mismo
#: proceso (recargas de uvicorn) en vez de acumular un handler por pasada.
_APP_LOG_HANDLER_FLAG = "_nexus_app_log_handler"

#: Hora, nivel, nombre del logger y mensaje. El nombre va porque `nexus` tiene
#: hijos por área (`nexus.auth`, `nexus.email`) y sin él no se sabe de quién es
#: la línea. El `asctime` por defecto trae fecha y hora con milisegundos, que es
#: lo que hace comparables estas líneas con las de uvicorn.
_APP_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def build_security_headers(settings: Settings) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    if settings.cookie_secure:  # solo cuando se sirve por HTTPS
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


async def origin_guard(request: Request, call_next, settings: Settings) -> JSONResponse | None:
    """Defensa en profundidad anti-CSRF para mutaciones de /auth.

    SameSite=Lax ya impide que el navegador envíe la cookie en POSTs
    cross-site; este check además rechaza peticiones con cabecera Origin
    falsa/desconocida. Clientes API (curl, scripts) no envían Origin.
    """
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if not request.url.path.startswith(settings.api_v1_prefix + "/auth"):
        return None
    origin = request.headers.get("origin")
    if origin and origin not in settings.frontend_origins:
        return JSONResponse(status_code=403, content={"detail": "Origen no permitido"})
    return None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def configure_app_logging(settings: Settings) -> None:
    """Enchufa un `StreamHandler` a stdout en el logger `nexus` y fija su nivel.

    Sin esto, `nexus.email` y `nexus.auth` heredan el nivel WARNING del raíz y
    todos sus `logger.info` —empezando por el del `ConsoleSender`, que escribe el
    código de verificación— se descartan en silencio: la fila se crea en
    `verification_codes` y no hay forma de obtener el código ni por la API ni por
    el log.

    POR QUÉ ESTA FUNCIÓN Y NO `basicConfig`/`dictConfig`: las dos tocan el logger
    RAÍZ. `dictConfig` además RESETEALO, y uvicorn configuró los suyos con la suya
    al arrancar: un `dictConfig` de la app se comería `uvicorn.error` y
    `uvicorn.access`, y un `basicConfig` no-op si uvicorn ya instaló un root
    handler. Aquí el handler se cuelga solo de `nexus`, que es la jerarquía de la
    app, así que ni se pisan los loggers de uvicorn ni los de SQLAlchemy/asyncio.

    El nivel sale de `settings.log_level` y se aplica IGUAL en producción: no
    hace falta apagarlo allí porque no es él quien decide qué se registra. Ese
    contrato de seguridad vive en el código que compone el mensaje, no en la
    configuración del log:
      - `ConsoleSender.send` comprueba `settings.is_prod` y, si es prod, escribe
        un WARNING con destinatario y asunto y OMITE el cuerpo. Ese `if` no se
        toca aquí, así que subir `nexus` a INFO no lo desactiva.
      - `build_email_sender` nunca devuelve un `ConsoleSender` en producción
        (lanza RuntimeError), de modo que ni siquiera se llega a ese WARNING.
    Los INFO que sí quedan visibles en producción son los de `nexus.auth`
    (usuario registrado, login correcto, código enviado con su id y su caducidad):
    no llevan el código, solo su existencia. Y esto es auditable con un grep de
    `logger.` en `app/`: el ÚNICO registro cuyo texto contiene el código es el
    `ConsoleSender`, que es justo el que está detrás del `if is_prod`.

    Idempotente a propósito: si el lifespan corre dos veces en el mismo proceso
    (recarga de uvicorn, o tests con lifespan), se retiran primero los handlers
    que instaló esta función. Sin eso, cada pasada añadiría uno más y cada
    línea de la app saldría repetida tantas veces como recargas.
    """
    app_logger = logging.getLogger("nexus")

    for existing in list(app_logger.handlers):
        if getattr(existing, _APP_LOG_HANDLER_FLAG, False):
            app_logger.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_APP_LOG_FORMAT))
    setattr(handler, _APP_LOG_HANDLER_FLAG, True)
    app_logger.addHandler(handler)
    app_logger.setLevel(settings.log_level_name)

    # `propagate` se deja como esté (True por defecto): en producción el raíz no
    # tiene handlers, así que las líneas no se duplican, y en pytest los tests
    # que capturan logs siguen viéndolas a través del handler del raíz.
    app_logger.info(
        "Logging de la app configurado: logger=nexus nivel=%s handler=stdout",
        settings.log_level_name,
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    # Se valida AQUÍ, y no solo en el lifespan de más abajo, por el orden: abajo
    # se construye el emisor de email, que en producción tiene sus propias
    # barreras y lanza su propio RuntimeError. Sin esta llamada, un
    # `EMAIL_ENABLED=false` en prod moriría con el mensaje del emisor en lugar
    # del de la variable que hay que cambiar. La del lifespan se conserva: es la
    # puerta de arranque de la app y `validate_for_production` es pura.
    cfg.validate_for_production()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg.validate_for_production()
        # El logging se configura AQUÍ y no al importar el módulo porque los
        # tests levantan la app con `httpx.ASGITransport`, que no ejecuta el
        # lifespan: así la salida de pytest no se ensucia con el log de la app.
        configure_app_logging(cfg)
        from app.database import init_db

        await init_db()
        yield

    app = FastAPI(
        title=cfg.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not cfg.is_prod else None,
        redoc_url=None if cfg.is_prod else "/redoc",
    )

    app.state.settings = cfg
    app.state.rate_limiter = InMemoryRateLimiter()
    # El 429 del limitador sale por un handler propio (no por el `HTTPException`
    # por defecto, que anidaría el detalle bajo otra clave `detail`): cuerpo
    # plano `{"detail": ..., "retry_after_seconds": N}` + cabecera `Retry-After`.
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    # Emisor de email en el state (y no en settings) para que los tests
    # inyecten el suyo: `app.state.email_sender = RecordingSender()` sin tocar
    # la configuración global. Ver app/services/email.py.
    app.state.email_sender = build_email_sender(cfg)

    # Cabeceras de seguridad + verificación de Origin
    security_headers = build_security_headers(cfg)

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        start = time.perf_counter()
        blocked = await origin_guard(request, call_next, cfg)
        if blocked is not None:
            return blocked
        response = await call_next(request)
        for header, value in security_headers.items():
            response.headers.setdefault(header, value)
        response.headers.setdefault("X-Response-Time-Ms", f"{int((time.perf_counter() - start) * 1000)}")
        return response

    # CORS: solo orígenes del frontend, con credenciales.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(auth_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/hello")
    async def hello():
        return {"message": "Hello, World!"}

    return app


app = create_app()