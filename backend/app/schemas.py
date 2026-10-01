"""Schemas Pydantic de la API de autenticación."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("full_name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("El nombre no puede estar vacío")
        return v

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def password_quality(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres")
        if len(v) > 128:
            raise ValueError("La contraseña no puede superar 128 caracteres")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = True

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class ResetPasswordRequest(BaseModel):
    """Reseteo por código numérico.

    El body incluye el email a propósito: el código va hasheado con
    `user_id + purpose` como sal, así que para buscarlo hay que saber qué
    usuario es. Con el email en el cuerpo, un código robado no sirve contra
    otra cuenta y el atacante tiene que conocer ambas cosas.

    Acepta el código como lo escribió el usuario ("042 731", "042-731"); lo
    normaliza la capa de servicio, no el schema. Idempotente en cuanto al
    envío: reenviar el mismo request tras un fallo de red no cambia nada.
    """

    email: EmailStr
    code: str = Field(min_length=1, max_length=32)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("new_password")
    @classmethod
    def password_quality(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres")
        if len(v) > 128:
            raise ValueError("La contraseña no puede superar 128 caracteres")
        return v


class VerifyEmailRequest(BaseModel):
    """Confirmación del email por código. Idempotente: si el usuario ya está
    verificado se responde 200 sin consumir el código."""

    code: str = Field(min_length=1, max_length=32)


class EmailStatusResponse(BaseModel):
    """Respuesta de POST /auth/resend-verification: qué pasó con la petición
    de reenvío, sin prometer más de lo que se cumple."""

    detail: str
    sent: bool = True
    resend_available_in_minutes: int | None = None


class VerificationStatusResponse(BaseModel):
    """Estado de la verificación de email, autoritativo del SERVIDOR.

    El cliente no puede calcular nada de aquí: no sabe cuándo se emitió el
    código (solo tiene la hora a la que lo pidió) ni cuántas filas hay en
    `verification_codes`. Todo son SEGUNDOS enteros, nunca `None` ni negativos,
    para que la UI pueda hacer cuentas atrás con ellos sin defenderse.

    - `pending`: hay una verificación sin completar. Un código ya CADUCADO
      sigue contando como pendiente (`expires_in_seconds` vale 0): la UI lo
      pinta como "caducado, pide otro" en vez de desaparecer, que dejaría al
      usuario sin ninguna forma de saber qué hacer.
    - `expires_in_seconds`: cuánto le queda al código vigente (suelo 0).
    - `resend_available_in_seconds`: cuánto falta para que el limitador de
      `POST /resend-verification` de ESTE cliente vuelva a admitir la llamada
      (0 = admite ahora). Es el mismo cubo y la misma clave que usaría esa
      petición, no una estimación aparte.
    - `codes_used_last_hour` / `codes_limit_per_hour`: códigos de
      verificación emitidos en la última hora frente al tope configurado.

    Con la cuenta ya verificada, `pending` es False y el resto son 0: no hay
    nada pendiente que contar y ningún límite que mostrar. Por eso los cinco
    campos admiten 0 (`ge=0`) en lugar de exigir 1 en los dos que son
    topes en el caso normal.
    """

    pending: bool
    expires_in_seconds: int = Field(ge=0)
    resend_available_in_seconds: int = Field(ge=0)
    codes_used_last_hour: int = Field(ge=0)
    codes_limit_per_hour: int = Field(ge=0)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str
    auth_provider: str
    is_email_verified: bool
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserPublic
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # segundos de validez del access token


class MessageResponse(BaseModel):
    detail: str


class UserProfileUpdate(BaseModel):
    """Edición del perfil por parte del propio usuario. Solo el nombre visible:
    email y contraseña se cambian por sus propios flujos."""

    full_name: str = Field(min_length=2, max_length=120)

    @field_validator("full_name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("El nombre no puede estar vacío")
        return v


class SessionPublic(BaseModel):
    """Sesión activa tal y como se ve desde el panel. Jamás incluye el token
    ni su hash: la cookie httpOnly es la única credencial.

    `id` es el autoincrement global de la tabla `refresh_tokens`, no un id por
    usuario: delata el número total de sesiones creadas en el sistema. Se
    mantiene a propósito (la UI lo usa como `key` estable de React y porque
    facilita depurar), y la consecuencia asumida es que un usuario puede estimar
    ese volumen global por la diferencia entre ids. Si algún día ese dato pasa
    a ser sensible, la alternativa es un identificador opaco por sesión, lo que
    cambiaría el contrato de la API.
    """

    id: int
    created_at: datetime
    expires_at: datetime
    remember_me: bool
    current: bool
