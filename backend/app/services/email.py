"""Capa de envío de email agnóstica del proveedor.

El resto de la app solo conoce `EmailSender`: nunca el SDK de Resend ni el
formato de sus parámetros. Así el proveedor se cambia en un sitio (y los
tests, inyectando un emisor propio en `app.state.email_sender`).

Regla de seguridad central — hay DOS, y son simétricas:

1. `build_email_sender` NUNCA construye un `ResendSender` si `settings.is_prod`
   es False. Los tests corren con ENVIRONMENT=dev, de modo que ni con
   RESEND_API_KEY en el entorno se puede enviar un email real desde una prueba;
   es una garantía de código, no una convención.
2. En producción `build_email_sender` NUNCA devuelve un `ConsoleSender` ni
   siquiera como fallback: lanza `RuntimeError`. Y `ConsoleSender.send` vuelve a
   comprobarlo, porque su `logger.info` escribía el cuerpo del email —con el
   código de verificación y el de reseteo, que son credenciales de un solo uso—
   en el log. La combinación EMAIL_PROVIDER=resend + EMAIL_ENABLED=false + sin
   key, que `validate_for_production` dejaba pasar, llegaba exactamente ahí.

Fuera de producción el fallback a `ConsoleSender` sí es silencioso: un `.env`
mal configurado se manifiesta como un WARNING en el log, no como un 500.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.config import Settings

logger = logging.getLogger("nexus.email")

try:  # El SDK es una dependencia opcional en la práctica: si no está, la app
    # arranca igual y solo falla el envío (ver ResendSender.send).
    import resend
    from resend.exceptions import ResendError
except ImportError:  # pragma: no cover - depende del entorno
    resend = None  # type: ignore[assignment]

    class ResendError(Exception):  # type: ignore[no-redef]
        """Sustituto para poder escribir `except ResendError` sin el SDK."""


class EmailDeliveryError(Exception):
    """Fallo al entregar un email. La capa HTTP la traduce a WARNING (register,
    forgot-password) o a 503 (resend-verification, donde el usuario está
    autenticado y no hay riesgo de enumeración)."""


@dataclass
class EmailMessage:
    """Mensaje agnóstico de proveedor. `html` y `text` van siempre los dos:
    `text` es la alternativa accesible (y la que usan los clientes que no
    renderizan HTML)."""

    to: str
    subject: str
    html: str
    text: str
    tags: list[dict[str, str]] = field(default_factory=list)


@runtime_checkable
class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> str:
        """Envia el mensaje y devuelve el id que asigna el proveedor."""
        ...


class ConsoleSender:
    """Emisor de desarrollo: escribe el contenido en el log INFO en lugar de
    enviarlo. Es el fallback por defecto fuera de producción, así que los flujos
    se pueden probar de punta a punta sin ningún proveedor configurado.

    Recibe los `settings` por constructor porque decide SIEMPRE en función del
    entorno: en producción el cuerpo del email (que lleva un código de un solo
    uso, es decir una credencial) no se escribe nunca, ni aunque alguien
    construya este emisor a mano saltándose `build_email_sender`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, message: EmailMessage) -> str:
        message_id = f"console-{uuid.uuid4()}"
        if self._settings.is_prod:
            # Barrera de último recurso: `build_email_sender` ya prohíbe llegar
            # aquí en producción, pero el cuerpo contiene el código de
            # verificación o el de reseteo y eso en un log es una credencial
            # filtrada. Se registra a quién y de qué se trata, nunca el código.
            logger.warning(
                "[email:console] to=%s subject=%s id=%s | cuerpo OMITIDO a "
                "propósito: en producción el emisor de consola está prohibido y "
                "su contenido incluiría un código de un solo uso.",
                message.to,
                message.subject,
                message_id,
            )
            return message_id
        logger.info(
            "[email:console] to=%s subject=%s id=%s\n%s",
            message.to,
            message.subject,
            message_id,
            message.text,
        )
        return message_id


class ResendSender:
    """Emisor real vía la API de Resend.

    - `resend.api_key` es una variable GLOBAL del módulo: se fija aquí, no en
      cada llamada, y nunca se loguea.
    - La `idempotency_key` viaja en el SEGUNDO argumento (`options`), que es
      donde la cabecera `Idempotency-Key` se adjunta; meterla en `params` la
      mandaría como parte del cuerpo y el proveedor la ignoraría.
    - Los parámetros van en snake_case (el SDK los convierte a camelCase).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Solo se ejecuta cuando hay key (lo garantiza build_email_sender).
        resend.api_key = settings.resend_api_key  # type: ignore[union-attr]

    async def send(self, message: EmailMessage) -> str:
        if resend is None:
            raise EmailDeliveryError(
                "El SDK de Resend no está instalado. Instala 'resend' "
                "(backend/requirements.txt) o usa EMAIL_PROVIDER=console."
            )

        params: dict[str, Any] = {
            "from": self._settings.email_from,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        # Solo se añaden si vienen informados: mandar null los hace fallar.
        if message.tags:
            params["tags"] = message.tags
        if self._settings.email_reply_to:
            params["reply_to"] = self._settings.email_reply_to

        # Clave de idempotencia estable por (destinatario, asunto, cuerpo): un
        # reintento tras un timeout ambiguo no duplica el email. Sin esto, la
        # clave sería aleatoria y el reintento sí lo duplicaría.
        options = {"idempotency_key": _idempotency_key(message)}

        try:
            response = await resend.Emails.send_async(params, options)
        except ResendError as exc:
            raise EmailDeliveryError(f"Resend rechazó el envío: {exc}") from exc
        except Exception as exc:  # red, DNS, timeouts: el SDK no tipifica todo
            raise EmailDeliveryError(
                f"No se pudo contactar con Resend: {type(exc).__name__}: {exc}"
            ) from exc

        # El SDK devuelve un dict {"id": ...}; algunos forks, un objeto.
        message_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", None)
        if not message_id:
            raise EmailDeliveryError("Resend respondió sin id de mensaje")
        return str(message_id)


def build_email_sender(settings: Settings) -> EmailSender:
    """Devuelve el emisor que corresponde a la configuración.

    Fuera de producción NUNCA lanza: si el proveedor no es utilizable se cae a
    `ConsoleSender` con un WARNING explícito (un fallo de arranque por un `.env`
    incompleto sería peor que registrar el código en el log de desarrollo).

    En producción es al revés: NUNCA devuelve un `ConsoleSender`, ni siquiera
    como fallback. Lanza `RuntimeError` si la configuración no permite enviar
    de verdad. Es la segunda barrera de `validate_for_production()` (que solo se
    ejecuta al construir la app) y cubre también los caminos que construyen el
    emisor sin pasar por ella, para que en producción no exista ninguna ruta que
    termine en el emisor que escribe credenciales en el log."""
    if settings.is_prod:
        return _build_production_sender(settings)

    if not settings.email_enabled:
        logger.warning(
            "EMAIL_ENABLED=false: los emails no se enviarán, se registrarán "
            "en el log (ConsoleSender)."
        )
        return ConsoleSender(settings)

    if settings.email_provider.strip().lower() == "resend":
        if not (settings.resend_api_key or "").strip():
            logger.warning(
                "EMAIL_PROVIDER=resend sin RESEND_API_KEY: se usará "
                "ConsoleSender y los códigos solo quedarán en el log."
            )
            return ConsoleSender(settings)
        # Guarda de seguridad: los tests nunca salen a internet. Para
        # enviar de verdad hay que declarar ENVIRONMENT=prod, que además
        # exige la key (validate_for_production).
        logger.warning(
            "EMAIL_PROVIDER=resend ignorado porque ENVIRONMENT=%s no es "
            "'prod': se usará ConsoleSender.",
            settings.environment,
        )
        return ConsoleSender(settings)

    # Proveedor "console" (o desconocido): comportamiento de desarrollo.
    return ConsoleSender(settings)


def _build_production_sender(settings: Settings) -> EmailSender:
    """Emisor de producción. Opciones: `ResendSender` o `RuntimeError`.

    Se repite aquí lo que exige `validate_for_production()` a propósito: son
    dos barreras independientes, no una duplicación. La de `Settings` solo corre
    al crear la app y es la que da el error bonito al operador; esta garantiza
    que, pase lo que pase con la validación, ninguna llamada a
    `build_email_sender` en producción pueda devolver el emisor de log."""
    if not settings.email_enabled:
        raise RuntimeError(
            "EMAIL_ENABLED=false en producción: build_email_sender no puede "
            "devolver un emisor de consola (escribiría los códigos de "
            "verificación y de reseteo en el log) y sin email no hay forma de "
            "enviarlos. Configura el envío real."
        )
    if settings.email_provider.strip().lower() != "resend":
        raise RuntimeError(
            f"EMAIL_PROVIDER={settings.email_provider!r} en producción: solo se "
            "admite 'resend'. El emisor de consola está prohibido porque deja "
            "los códigos en el log."
        )
    if not (settings.resend_api_key or "").strip():
        raise RuntimeError(
            "EMAIL_PROVIDER=resend en producción sin RESEND_API_KEY: falta la "
            "credencial del proveedor, y el fallback a consola escribiría los "
            "códigos en el log."
        )
    return ResendSender(settings)


def _idempotency_key(message: EmailMessage) -> str:
    digest = hashlib.sha256(
        f"{message.to}|{message.subject}|{message.html}".encode("utf-8")
    ).hexdigest()
    # Límite del proveedor: 256 caracteres. El hex de sha256 (64) + prefijo
    # legible del propósito caben de sobra.
    return f"nm-{digest[:64]}"
