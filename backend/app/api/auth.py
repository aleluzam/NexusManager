"""Endpoints de autenticación y gestión de sesiones."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import make_rate_limit_dependency
from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import PasswordResetToken, RefreshToken, User
from app.schemas import (
    AuthResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    UserPublic,
)
from app.security import (
    build_refresh_cookie_delete_kwargs,
    build_refresh_cookie_kwargs,
    create_access_token,
    generate_refresh_token,
    generate_reset_token,
    hash_password,
    new_refresh_expiry,
    new_reset_expiry,
    sha256_hex,
    utcnow,
    verify_password,
)

logger = logging.getLogger("nexus.auth")

settings = get_settings()

router = APIRouter(prefix=settings.api_v1_prefix + "/auth", tags=["autenticación"])

_GENERIC_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Correo o contraseña incorrectos",
)
_INVALID_TOK = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sesión inválida o expirada",
)

rate = make_rate_limit_dependency


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    request: Request,
    payload: RegisterRequest,
    response: Response,
    _: None = Depends(rate("register")),
    db: AsyncSession = Depends(get_db),
):
    """Crea una cuenta local y abre sesión automáticamente."""
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una cuenta con este correo",
        )

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        auth_provider="local",
        is_email_verified=True,  # verificación SMTP se integrará después
    )
    db.add(user)
    await db.flush()

    access_token, expires_in = create_access_token(user.id)
    await _issue_refresh_session(db, user, remember_me=True, response=response)
    await db.commit()

    logger.info("Nuevo usuario registrado: %s", user.email)
    return AuthResponse(
        user=UserPublic.model_validate(user),
        access_token=access_token,
        expires_in=expires_in,
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    _: None = Depends(rate("login")),
    db: AsyncSession = Depends(get_db),
):
    """Inicia sesión con credenciales locales. Respuesta 401 genérica para
    email inexistente o contraseña incorrecta (sin enumeración de cuentas)."""
    user = await db.scalar(select(User).where(User.email == payload.email))

    if user is None or not verify_password(payload.password, user.password_hash):
        raise _GENERIC_INVALID
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cuenta desactivada. Contacta a soporte.",
        )

    user.last_login_at = utcnow()
    access_token, expires_in = create_access_token(user.id)
    await _issue_refresh_session(
        db, user, remember_me=payload.remember_me, response=response
    )
    await db.commit()

    logger.info("Login correcto: %s", user.email)
    return AuthResponse(
        user=UserPublic.model_validate(user),
        access_token=access_token,
        expires_in=expires_in,
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    request: Request,
    response: Response,
    _: None = Depends(rate("refresh")),
    db: AsyncSession = Depends(get_db),
):
    """Rota el refresh token (cookie httpOnly) y emite un nuevo access token.
    Detecta reutilización de tokens rotados (posible robo) y revoca todas
    las sesiones del usuario en ese caso."""
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise _INVALID_TOK

    token_hash = sha256_hex(token)
    row = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )

    if row is None:
        response.delete_cookie(**build_refresh_cookie_delete_kwargs())
        raise _INVALID_TOK

    # Detección de reutilización (token ya rotado/revocado).
    if row.revoked_at is not None:
        logger.warning("Refresh token reutilizado (posible robo): user=%s", row.user_id)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        await db.commit()
        response.delete_cookie(**build_refresh_cookie_delete_kwargs())
        raise _INVALID_TOK

    if row.expires_at <= utcnow():
        row.revoked_at = utcnow()
        response.delete_cookie(**build_refresh_cookie_delete_kwargs())
        await db.commit()
        raise _INVALID_TOK

    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise _INVALID_TOK

    # Rotación: el token usado se revoca y se emite uno nuevo.
    row.revoked_at = utcnow()
    new_token, new_hash = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=new_hash,
            expires_at=new_refresh_expiry(row.remember_me),
            remember_me=row.remember_me,
        )
    )
    access_token, expires_in = create_access_token(user.id)
    response.set_cookie(value=new_token, **build_refresh_cookie_kwargs(row.remember_me))
    await db.commit()

    return AuthResponse(
        user=UserPublic.model_validate(user),
        access_token=access_token,
        expires_in=expires_in,
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    _: None = Depends(rate("logout")),
    db: AsyncSession = Depends(get_db),
):
    """Revoca el refresh token actual y limpia la cookie."""
    token = request.cookies.get(settings.cookie_name)
    if token:
        row = await db.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == sha256_hex(token),
                RefreshToken.revoked_at.is_(None),
            )
        )
        if row is not None:
            row.revoked_at = utcnow()
            await db.commit()
    response.delete_cookie(**build_refresh_cookie_delete_kwargs())
    return Response(status_code=204)


@router.get("/me", response_model=UserPublic)
async def me(user: User = Depends(get_current_user)):
    """Devuelve el usuario autenticado (access token en Authorization)."""
    return user


@router.post("/forgot-password", response_model=MessageResponse, status_code=202)
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    _: None = Depends(rate("forgot-password")),
    db: AsyncSession = Depends(get_db),
):
    """Emite un token de reseteo. Respuesta idéntica para emails existentes
    o no: no revela qué cuentas existen."""
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is not None and user.is_active:
        reset_token, token_hash = generate_reset_token()
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=new_reset_expiry(),
            )
        )
        await db.commit()
        # Sin proveedor SMTP por ahora: en dev se loguea el enlace.
        if not settings.is_prod:
            logger.info("Reset password para %s -> token: %s", user.email, reset_token)
    return MessageResponse(
        detail="Si el correo existe, recibirás un enlace para restablecer tu contraseña."
    )


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    _: None = Depends(rate("reset-password")),
    db: AsyncSession = Depends(get_db),
):
    """Restablece la contraseña con un token de un solo uso y revoca todas
    las sesiones del usuario."""
    row = await db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == sha256_hex(payload.token)
        )
    )
    invalid = row is None or row.used_at is not None or row.expires_at <= utcnow()
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El enlace es inválido o ha expirado",
        )

    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El enlace es inválido o ha expirado",
        )

    row.used_at = utcnow()
    user.password_hash = hash_password(payload.new_password)
    # La contraseña cambió: revocamos todas las sesiones activas.
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()
    return MessageResponse(detail="Contraseña actualizada. Ya puedes iniciar sesión.")


async def _issue_refresh_session(
    db: AsyncSession,
    user: User,
    remember_me: bool,
    response: Response,
) -> None:
    token, token_hash = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=new_refresh_expiry(remember_me),
            remember_me=remember_me,
        )
    )
    response.set_cookie(value=token, **build_refresh_cookie_kwargs(remember_me))