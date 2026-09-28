"""Aplicación FastAPI de NexusManager — factory configurable (aislable en tests)."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.rate_limit import InMemoryRateLimiter
from app.config import Settings, get_settings


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
# Factory
# ---------------------------------------------------------------------------

def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg.validate_for_production()
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