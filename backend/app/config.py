"""Configuración de la aplicación cargada desde variables de entorno / .env."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Niveles que acepta `log_level`. Se listan en vez de usar
#: `logging.getLevelNamesMapping()` para no atar el archivo a una versión
#: concreta de Python, y para que un nivel raro (NOTSET) no acabe siendo un
#: "no hacer nada" silencioso.
LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Identidad del servicio
    app_name: str = "NexusManager API"
    environment: str = "dev"  # "dev" | "prod"
    api_v1_prefix: str = "/api/v1"

    # --- Logging -------------------------------------------------------------
    # Nivel de los loggers DE LA APP (`nexus` y sus hijos: `nexus.auth`,
    # `nexus.email`). No toca los loggers de uvicorn (`uvicorn`, `uvicorn.error`,
    # `uvicorn.access`), que los configura el propio uvicorn con su dictConfig, ni
    # los de librerías de terceros: el handler se cuelga del logger `nexus`, no
    # del raíz. Subir o bajar el nivel solo decide qué de lo que la app YA
    # registra se emite; no crea registros nuevos ni cambia QUÉ se registra. La
    # garantía de que un código OTP no se escribe en el log en producción no
    # depende de este valor: depende del `if settings.is_prod` de
    # `ConsoleSender.send`, que se comprueba antes de construir el mensaje. Ver
    # `configure_app_logging` en app/main.py.
    log_level: str = "info"

    # Seguridad: secreto de firma de JWTs. En producción DEBE venir de una
    # variable de entorno con al menos 32 bytes de entropía.
    secret_key: str = "dev-only-insecure-secret-change-me-in-prod"

    # Tokens de acceso (JWT) de corta vida
    access_token_minutes: int = 15

    # Tokens de refresco (cookie httpOnly)
    refresh_token_days: int = 7
    refresh_token_days_remember: int = 30

    # Base de datos (async SQLAlchemy). Por defecto SQLite local; en
    # producción apuntar a PostgreSQL: postgresql+asyncpg://...
    database_url: str = "sqlite+aiosqlite:///./nexus.db"

    # --- Email transaccional (códigos de verificación y reseteo) -----------
    # Proveedor: "console" (solo log, para dev/test) | "resend" (producción).
    # La selección real la hace app.services.email.build_email_sender, que
    # NUNCA construye un ResendSender fuera de producción: es la garantía
    # mecánica de que un test (ENVIRONMENT=dev) no puede enviar un email real
    # aunque tenga RESEND_API_KEY en el entorno.
    resend_api_key: str | None = None
    email_provider: str = "console"  # "console" | "resend"
    email_from: str = "NexusManager <onboarding@resend.dev>"
    email_reply_to: str | None = None
    email_enabled: bool = True

    # Frontend al que apuntan los enlaces de los emails (CTA de reseteo).
    frontend_url: str = "http://localhost:5173"
    # Email de soporte: con él, el pie de los emails incluye el enlace de
    # soporte (mailto). Sin él, el pie lo omite en lugar de inventar una URL.
    support_email: str | None = None

    # Ciclo de vida de los códigos de verificación (tabla verification_codes).
    # El hash de cada código va salado con user_id + purpose, así que 6 dígitos
    # no son adivinables por colisión, pero el TTL corto y el límite de intentos
    # acortan la ventana de fuerza bruta online.
    verification_code_ttl_minutes: int = 10
    verification_code_max_attempts: int = 5
    # Tope de códigos emitidos por usuario y propósito dentro de una hora.
    # Es un límite de EMISIÓN (no de intentos): frena el spam de "reenvía el
    # código" y el envío masivo de correos desde una cuenta ajena.
    verification_code_max_per_hour: int = 5

    # CORS: orígenes del frontend permitidos (con credenciales)
    frontend_origins: list[str] = ["http://localhost:5173"]

    # Cookies
    cookie_secure: bool = False  # True en producción (solo HTTPS)
    cookie_domain: str | None = None
    cookie_name: str = "nm_refresh"

    # Rate limiting por IP (ventana deslizante en memoria).
    # Claves (deben coincidir con las de app/api/rate_limit.py, o el dict se
    # indexa con una clave inexistente y la ruta revienta con KeyError -> 500):
    # register, login, refresh, logout, forgot-password, reset-password,
    # verify-email, resend-verification, verification-status, profile, sessions
    rate_limit_login_max: int = 5
    rate_limit_login_window: int = 60
    rate_limit_register_max: int = 5
    rate_limit_register_window: int = 60
    rate_limit_refresh_max: int = 15
    rate_limit_refresh_window: int = 60
    rate_limit_logout_max: int = 15
    rate_limit_logout_window: int = 60
    rate_limit_forgot_max: int = 3
    rate_limit_forgot_window: int = 60
    rate_limit_reset_max: int = 5
    rate_limit_reset_window: int = 3600
    # Verificación de email: POST /verify-email (intentos fallidos) y
    # POST /resend-verification (reenvíos) van separados a propósito: teclear
    # un código 5 veces por minuto y pedir 3 correos por minuto son dos abusos
    # distintos (uno contra el sistema, otro contra el buzón de la víctima).
    rate_limit_verify_email_max: int = 5
    rate_limit_verify_email_window: int = 60
    rate_limit_resend_verification_max: int = 3
    rate_limit_resend_verification_window: int = 3600
    # GET /verification-status: LECTURA del estado de la verificación. Es la
    # ruta que la barra de verificación consulta para pintar la cuenta atrás y
    # el enfriamiento del botón de reenviar, así que se la deja GENEROSA: un
    # umbral estrecho convertiría una recarga de página normal en un 429.
    rate_limit_verification_status_max: int = 60
    rate_limit_verification_status_window: int = 60
    # Panel de usuario: PATCH /me (editar el nombre, acción poco frecuente).
    rate_limit_profile_max: int = 20
    rate_limit_profile_window: int = 60
    # Clave compartida por GET /sessions y POST /sessions/revoke-others: ambos
    # leen/escriben sesiones del usuario y comparten umbral.
    rate_limit_sessions_max: int = 30
    rate_limit_sessions_window: int = 60

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Falla al arrancar si el nivel no existe, en vez de tragárselo.

        Sin esto, `setLevel("debg")` reventaría más tarde y más lejos (dentro
        del lifespan, con la traza de por medio), y un valor en blanco
        dejaría el logger en NOTSET heredando el del raíz. Un `LOG_LEVEL`
        mal escrito es un fallo de configuración: que se note al cargar.
        """
        normalized = value.strip().upper()
        if normalized not in LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL={value!r} no es un nivel válido. Usa uno de: "
                f"{', '.join(LOG_LEVELS)}."
            )
        return normalized

    @property
    def log_level_name(self) -> str:
        """`log_level` normalizado, listo para `logging.Logger.setLevel`."""
        return self.log_level.strip().upper()

    @property
    def is_prod(self) -> bool:
        return self.environment.lower() == "prod"

    def validate_for_production(self) -> None:
        """Falla rápido si la configuración de producción es insegura."""
        if not self.is_prod:
            return
        if self.secret_key == "dev-only-insecure-secret-change-me-in-prod":
            raise RuntimeError(
                "SECRET_KEY no configurada. Debe definir una clave fuerte "
                "en producción."
            )
        if len(self.secret_key.encode()) < 32:
            raise RuntimeError(
                "SECRET_KEY debe tener al menos 32 bytes de entropía."
            )
        if not self.cookie_secure:
            raise RuntimeError(
                "COOKIE_SECURE debe ser true en producción (cookies solo "
                "por HTTPS)."
            )
        # --- Email: TRES condiciones, todas obligatorias en producción -------
        #
        # Antes bastaba con "si el email está habilitado, que haya key" y con
        # "el proveedor no puede ser console". La combinación
        # EMAIL_PROVIDER=resend + EMAIL_ENABLED=false + sin key pasaba la
        # validación, build_email_sender ignoraba el provider y devolvía un
        # ConsoleSender, y su `send` escribía el cuerpo del email —con el
        # código de verificación y el de reseteo— en el log de producción. Eso
        # es una credencial de un solo uso escrita en disco, no un problema de
        # configuración: por eso las tres se exigen y cada una con su mensaje.
        if not self.email_enabled:
            raise RuntimeError(
                "EMAIL_ENABLED=false en producción. Desactivar el email no es "
                "una opción de rendimiento: sin correo no hay forma de recibir "
                "ni el código de verificación ni el de reseteo de contraseña, y "
                "ningún usuario podría completar esos flujos. Ponlo en true y "
                "configura EMAIL_PROVIDER=resend con RESEND_API_KEY."
            )
        if self.email_provider.strip().lower() != "resend":
            raise RuntimeError(
                f"EMAIL_PROVIDER={self.email_provider!r} en producción. Tiene "
                "que ser 'resend': con 'console' los códigos se imprimirían en "
                "el log en lugar de enviarse, y cualquiera con acceso al log "
                "entraría en la cuenta."
            )
        if not (self.resend_api_key or "").strip():
            raise RuntimeError(
                "RESEND_API_KEY no configurada. Es obligatoria en producción "
                "para enviar los códigos de verificación y de reseteo."
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_for_production()
    return settings