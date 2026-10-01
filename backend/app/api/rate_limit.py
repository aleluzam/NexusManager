"""Rate limiting: ventana deslizante en memoria por IP + ruta.

Suficiente para despliegues single-process; con varios workers/instancias
debe sustituirse por un backend compartido (Redis).
"""

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.config import Settings


@dataclass(frozen=True)
class RateLimitDecision:
    """Veredicto del cubo para UNA petición, con el tiempo que queda.

    `allowed` sustituye al `bool` que devolvía `allow()`: un booleano obligaba
    a que quien recibía el 429 se construyera su propio temporizador, y el
    resultado era siempre la ventana completa ("60" aunque quedaran 4
    segundos), que es una estimación y no un hecho. Aquí el número sale del
    propio cubo, así que es exactamente el tiempo que falta para que la
    petición sea aceptada.

    `retry_after_seconds` es 0 cuando `allowed` es True: si la petición pasa,
    no hay nada que esperar.
    """

    allowed: bool
    retry_after_seconds: int = 0


class RateLimitExceeded(Exception):
    """429 del limitador, con el detalle YA estructurado.

    No es un `HTTPException` porque el handler por defecto de FastAPI anida el
    `detail` bajo otra clave `detail`: pasar un dict ahí produciría
    `{"detail": {"detail": ..., "retry_after_seconds": ...}}`, que no es el
    contrato que consume el frontend. Con una excepción propia y su handler
    (ver `rate_limit_exceeded_handler`, registrado en `create_app`) el cuerpo
    es plano.
    """

    def __init__(self, *, detail: str, retry_after_seconds: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds


#: Texto del 429 por ruta. El cubo es genérico pero el mensaje no: "Has alcanzado
#: el límite de reenvíos" dice QUÉ se limitó y qué hacer a continuación, y el
#: genérico cubre el resto de rutas. `{wait}` recibe el texto legible del
#: tiempo restante (ver `humanize_wait`).
#:
#: Las rutas ausentes usan `DEFAULT_RATE_LIMIT_MESSAGE`, así que añadir una
#: dependencia `rate(...)` nueva no obliga a escribir un mensaje.
RATE_LIMIT_MESSAGES: dict[str, str] = {
    "resend-verification": "Has alcanzado el límite de reenvíos. Podrás pedir otro en {wait}.",
    "forgot-password": "Has alcanzado el límite de peticiones. Podrás volver a intentarlo en {wait}.",
}

#: Mensaje de las rutas sin texto propio. Mantiene la fórmula del anterior
#: ("Espera ... e inténtalo de nuevo") pero con el tiempo REAL en vez de "un
#: momento", que no era un tiempo sino un eufemismo.
DEFAULT_RATE_LIMIT_MESSAGE = "Demasiados intentos. Espera {wait} e inténtalo de nuevo."


def humanize_wait(seconds: int) -> str:
    """Segundos → texto legible en español ("45 segundos", "47 minutos",
    "1 hora 30 minutos").

    Se redondea hacia ARRIBA en minutos y horas: el usuario tiene que esperar
    al menos eso, y decir "en 1 minuto" cuando faltan 119 segundos hace que
    vuelva a reintentar y reciba otro 429.

    Por debajo de un minuto se cuentan segundos, porque es la unidad con la que
    un usuario lee "vuelve a intentarlo enseguida" de forma útil.
    """
    if seconds <= 0:
        return "un momento"
    if seconds < 60:
        return "1 segundo" if seconds == 1 else f"{seconds} segundos"

    if seconds < 3600:
        minutos = math.ceil(seconds / 60)
        return "1 minuto" if minutos == 1 else f"{minutos} minutos"

    horas, resto = divmod(seconds, 3600)
    partes = ["1 hora" if horas == 1 else f"{horas} horas"]
    if resto:
        minutos = math.ceil(resto / 60)
        partes.append("1 minuto" if minutos == 1 else f"{minutos} minutos")
    return " ".join(partes)


def _seconds_until(instant: float, now: float) -> int:
    """Segundos enteros (mínimo 1) que faltan para alcanzar `instant`.

    `ceil` y no truncar: `Retry-After` es un mínimo ("no antes de"), así que
    redondear hacia abajo invitaría al cliente a reintentar justo cuando el
    cubo sigue lleno. El mínimo de 1 evita además el 0, que se leería como
    "ahora puedes" y no es cierto para una petición que se acaba de denegar.
    """
    return max(1, math.ceil(instant - now))


def bucket_key(client_host: str, path: str) -> str:
    """Clave de cubo: host + ruta.

    Vive aquí para que el endpoint que consulta el estado de OTRO cubo
    (GET /verification-status leyendo el de resend-verification) no tenga que
    replicar el formato a mano: si las dos cadenas dejaran de coincidir, el
    contador que se muestra al usuario no sería el que va a fallar.
    """
    return f"{client_host}:{path}"


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300.0

    def _prune(self, now: float) -> None:
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        for key in list(self._buckets):
            bucket = self._buckets[key]
            if not bucket or now - bucket[-1] > 3600 * 24:
                del self._buckets[key]

    def check(
        self, key: str, max_requests: int, window_seconds: int
    ) -> RateLimitDecision:
        """¿Admite esta petición? Además devuelve cuánto falta si no la admite.

        Sustituye a `allow()`, que devolvía un bool y obligaba a reconstruir el
        tiempo restante. Una petición denegada NO se anota en el cubo (como
        antes): si se anotara, un cliente que insistiera en un 429 iría
        empujando su propia ventana y el tiempo que se le informa iría
        creciendo sin fin.
        """
        now = time.monotonic()
        self._prune(now)
        bucket = self._buckets[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if max_requests <= 0:
            # "Bloquear siempre" (configuración sin sentido, pero no imposible):
            # no hay marca en el cubo de la que deducir cuándo se libera, así
            # que se informa de la ventana entera —que es lo que se informaba
            # antes— en vez de leer bucket[0] y reventar con un IndexError
            # detrás de un 500 por una variable de entorno mal puesta.
            return RateLimitDecision(allowed=False, retry_after_seconds=window_seconds)
        if len(bucket) >= max_requests:
            # El cubo está lleno: la siguiente plaza se libera cuando salga de
            # la ventana la petición MÁS ANTIGUA (bucket[0]), no la más nueva.
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=_seconds_until(bucket[0] + window_seconds, now),
            )
        bucket.append(now)
        return RateLimitDecision(allowed=True)

    def retry_after(
        self, key: str, max_requests: int, window_seconds: int
    ) -> int:
        """Segundos que faltan para que este cubo vuelva a admitir una
        petición; 0 si admite ahora.

        CONSULTA PURA: no anota, no limpia y no crea la clave. Es lo que puede
        leer un endpoint de estado (`GET /verification-status`) para mostrarle
        al usuario el mismo contador que va a encontrar en el 429 de dentro de
        un rato. Si consumiera una plaza, un simple "ver el estado" gastaría
        un reenvío.
        """
        now = time.monotonic()
        if max_requests <= 0:
            # Mismo "bloquear siempre" que en `check`: sin marcas en el cubo no
            # hay nada de lo que deducir un tiempo, y decir 0 ("puedes ahora")
            # mientras la siguiente `check` va a denegar sería una mentira que
            # el endpoint de estado leería al usuario.
            return window_seconds
        bucket = self._buckets.get(key)
        if not bucket:
            return 0
        dentro = [t for t in bucket if t > now - window_seconds]
        if len(dentro) < max_requests:
            return 0
        return _seconds_until(dentro[0] + window_seconds, now)


def make_rate_limit_dependency(route: str) -> Callable[..., Awaitable[None]]:
    """Factory de dependencia que limita 'route' por IP usando la
    configuración de la aplicación (request.app.state.settings)."""

    async def limit_by_ip(request: Request) -> None:
        settings: Settings = request.app.state.settings
        # Estas claves deben cubrir TODAS las rutas que usen Depends(rate(...)):
        # el dict se indexa con 'route' en tiempo de petición, así que añadir una
        # dependencia sin añadir aquí su clave provoca un KeyError (500) en
        # runtime, no un fallo visible al importar el módulo.
        limits = {
            "login": (settings.rate_limit_login_max, settings.rate_limit_login_window),
            "register": (
                settings.rate_limit_register_max,
                settings.rate_limit_register_window,
            ),
            "refresh": (
                settings.rate_limit_refresh_max,
                settings.rate_limit_refresh_window,
            ),
            "logout": (
                settings.rate_limit_logout_max,
                settings.rate_limit_logout_window,
            ),
            "forgot-password": (
                settings.rate_limit_forgot_max,
                settings.rate_limit_forgot_window,
            ),
            "reset-password": (
                settings.rate_limit_reset_max,
                settings.rate_limit_reset_window,
            ),
            "verify-email": (
                settings.rate_limit_verify_email_max,
                settings.rate_limit_verify_email_window,
            ),
            "resend-verification": (
                settings.rate_limit_resend_verification_max,
                settings.rate_limit_resend_verification_window,
            ),
            "verification-status": (
                settings.rate_limit_verification_status_max,
                settings.rate_limit_verification_status_window,
            ),
            "profile": (
                settings.rate_limit_profile_max,
                settings.rate_limit_profile_window,
            ),
            "sessions": (
                settings.rate_limit_sessions_max,
                settings.rate_limit_sessions_window,
            ),
        }
        max_requests, window = limits[route]
        # El cubo se indexa por IP + ruta: dos rutas con la misma clave ("sessions")
        # comparten umbral pero llevan contadores independientes.
        key = bucket_key(request.client.host, request.url.path)
        limiter: InMemoryRateLimiter = request.app.state.rate_limiter
        decision = limiter.check(key, max_requests, window)
        if not decision.allowed:
            template = RATE_LIMIT_MESSAGES.get(route, DEFAULT_RATE_LIMIT_MESSAGE)
            raise RateLimitExceeded(
                detail=template.format(wait=humanize_wait(decision.retry_after_seconds)),
                retry_after_seconds=decision.retry_after_seconds,
            )

    return limit_by_ip


async def rate_limit_exceeded_handler(
    _request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Respuesta 429 con `detail` legible y `retry_after_seconds` en el CUERPO,
    más la cabecera estándar `Retry-After` con el mismo número.

    Los dos sitios llevan el valor por el mismo camino (el que calculó el
    cubo), así que no pueden discrepar. La cabecera es la que leen las
    librerías y los proxies; el campo del cuerpo, el frontend.
    """
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "detail": exc.detail,
            "retry_after_seconds": exc.retry_after_seconds,
        },
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )
