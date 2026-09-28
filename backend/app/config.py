"""Configuración de la aplicación cargada desde variables de entorno / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Seguridad: secreto de firma de JWTs. En producción DEBE venir de una
    # variable de entorno con al menos 32 bytes de entropía.
    secret_key: str = "dev-only-insecure-secret-change-me-in-prod"

    # Tokens de acceso (JWT) de corta vida
    access_token_minutes: int = 15

    # Tokens de refresco (cookie httpOnly)
    refresh_token_days: int = 7
    refresh_token_days_remember: int = 30

    # Tokens de reseteo de contraseña
    reset_token_minutes: int = 30

    # Base de datos (async SQLAlchemy). Por defecto SQLite local; en
    # producción apuntar a PostgreSQL: postgresql+asyncpg://...
    database_url: str = "sqlite+aiosqlite:///./nexus.db"

    # CORS: orígenes del frontend permitidos (con credenciales)
    frontend_origins: list[str] = ["http://localhost:5173"]

    # Cookies
    cookie_secure: bool = False  # True en producción (solo HTTPS)
    cookie_domain: str | None = None
    cookie_name: str = "nm_refresh"

    # Rate limiting por IP (ventana deslizante en memoria).
    # Claves: register, login, refresh, logout, forgot-password, reset-password
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

    @property
    def is_prod(self) -> bool:
        return self.environment.lower() == "prod"

    def validate_for_production(self) -> None:
        """Falla rápido si la configuración de producción es insegura."""
        if self.is_prod:
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


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_for_production()
    return settings