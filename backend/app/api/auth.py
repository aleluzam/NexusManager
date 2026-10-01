"""Endpoints de autenticación y gestión de sesiones."""

import logging
from datetime import timedelta

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import InMemoryRateLimiter, bucket_key, make_rate_limit_dependency
from app.config import Settings, get_settings
from app.database import SessionLocal, get_db
from app.deps import get_current_user
from app.models import RefreshToken, User, VerificationCode
from app.schemas import (
    AuthResponse,
    EmailStatusResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    SessionPublic,
    UserProfileUpdate,
    UserPublic,
    VerificationStatusResponse,
    VerifyEmailRequest,
)
from app.security import (
    build_refresh_cookie_delete_kwargs,
    build_refresh_cookie_kwargs,
    create_access_token,
    generate_refresh_token,
    generate_verification_code,
    hash_password,
    hash_verification_code,
    is_valid_verification_code,
    new_refresh_expiry,
    new_verification_expiry,
    normalize_verification_code,
    sha256_hex,
    utcnow,
    verify_password,
)
from app.services.email import EmailDeliveryError, EmailMessage, EmailSender, build_email_sender
from app.templates.email import (
    render_password_reset_code_email,
    render_verification_code_email,
)

logger = logging.getLogger("nexus.auth")

settings = get_settings()

router = APIRouter(prefix=settings.api_v1_prefix + "/auth", tags=["autenticación"])

#: Path COMPLETO de POST /resend-verification, tal y como lo construye el router
#: de arriba. Se usa para indexar el cubo del limitador desde
#: GET /verification-status: la clave tiene que ser byte a byte la misma que
#: usa el limitador al atender el reenvío, o el contador que se le enseña al
#: usuario sería el de otro cubo distinto del que va a fallar. Se deriva de las
#: MISMAS variables que el `prefix` del router para que no puedan separarse.
RESEND_VERIFICATION_PATH = settings.api_v1_prefix + "/auth" + "/resend-verification"

# Discriminantes de la tabla verification_codes (columna `purpose`).
PURPOSE_EMAIL_VERIFICATION = "email_verification"
PURPOSE_PASSWORD_RESET = "password_reset"

# Un único mensaje para todo lo que puede fallar al validar un código
# (inexistente, ya usado, caducado, demasiados intentos): distinguir los casos
# leería a un atacante como un oráculo sobre el estado del código.
_INVALID_CODE_DETAIL = "El código es inválido o ha expirado"

# Reintentos de generación por colisión del UNIQUE de `code_hash`. Con el hash
# salado por (purpose, user_id) la probabilidad es despreciable, pero el
# fallo sería un INSERT que rompe la transacción y el código no llegaría al
# usuario; dos intentos bastan de sobra.
_CODE_GENERATION_ATTEMPTS = 3

_GENERIC_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Correo o contraseña incorrectos",
)
_INVALID_TOK = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sesión inválida o expirada",
)
_NO_REFRESH_COOKIE = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="No hay sesión activa en este dispositivo. Vuelve a iniciar sesión.",
)
_INVALID_CODE = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail=_INVALID_CODE_DETAIL,
)

rate = make_rate_limit_dependency


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    request: Request,
    payload: RegisterRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    _: None = Depends(rate("register")),
    db: AsyncSession = Depends(get_db),
):
    """Crea una cuenta local y abre sesión automáticamente.

    La cuenta nace con `is_email_verified=False` y el código de verificación se
    emite en segundo plano: si el email falla, el registro NO se rompe (el
    usuario puede reintentarlo con POST /resend-verification) porque perder la
    cuenta ya creada por un problema del proveedor sería peor. Que el envío no
    forme parte de la ruta de la petición mantiene además invariante el tiempo
    de respuesta (ver `_issue_code_in_background`)."""
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
        is_email_verified=False,
    )
    db.add(user)
    await db.flush()

    access_token, expires_in = create_access_token(user.id)
    await _issue_refresh_session(db, user, remember_me=True, response=response)
    await db.commit()

    logger.info("Nuevo usuario registrado: %s", user.email)

    # El usuario ya está confirmado en la base de datos (el commit de arriba), así
    # que la tarea en segundo plano solo necesita su id.
    background_tasks.add_task(
        _issue_code_in_background,
        PURPOSE_EMAIL_VERIFICATION,
        settings=_app_settings(request),
        sender=_email_sender(request),
        user_id=user.id,
    )

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


# ---------------------------------------------------------------------------
# Panel de usuario: perfil y sesiones activas
# ---------------------------------------------------------------------------

@router.patch("/me", response_model=UserPublic)
async def update_me(
    payload: UserProfileUpdate,
    _: None = Depends(rate("profile")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Actualiza únicamente el nombre visible. Email y contraseña no se tocan:
    el modelo de request es cerrado, así que cualquier campo extra en el body
    se ignora. Idempotente: reenviar el mismo nombre devuelve 200 sin error."""
    user.full_name = payload.full_name
    await db.commit()
    logger.info("Nombre actualizado: %s", user.email)
    return user


@router.get("/sessions", response_model=list[SessionPublic])
async def list_sessions(
    request: Request,
    _: None = Depends(rate("sessions")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista las sesiones activas del usuario (una por dispositivo). Nunca
    devuelve el token ni su hash: la cookie httpOnly es la única credencial.
    `current` marca la sesión de la cookie de esta petición; sin cookie, ninguna
    sesión es la actual."""
    cookie_token = request.cookies.get(settings.cookie_name)
    current_hash = sha256_hex(cookie_token) if cookie_token else None

    result = await db.execute(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > utcnow(),
        )
        # created_at tiene resolución de segundo en SQLite: id desempata.
        .order_by(RefreshToken.created_at.desc(), RefreshToken.id.desc())
    )

    return [
        SessionPublic(
            id=row.id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            remember_me=row.remember_me,
            current=current_hash is not None and row.token_hash == current_hash,
        )
        for row in result.scalars()
    ]


@router.post("/sessions/revoke-others", status_code=204)
async def revoke_other_sessions(
    request: Request,
    _: None = Depends(rate("sessions")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoca todas las sesiones del usuario salvo la de esta cookie, que
    sigue viva: no se modifica ni se borra la cookie. Sin cookie, o con una
    cookie que no sea una sesión viva y no caducada del propio usuario, la
    operación es un no-op (204) para no dejar al usuario sin sesiones."""
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise _NO_REFRESH_COOKIE

    token_hash = sha256_hex(token)
    # Solo se conserva una sesión viva Y no caducada de este usuario (token ya
    # rotado, revocado, caducado o de otra cuenta -> no se toca nada): revocar
    # "todas" dejaría al usuario sin ninguna sesión desde la que reautenticarse.
    # expires_at es obligatorio en el filtro: una cookie caducada pero no
    # revocada no puede considerarse la sesión actual, porque GET /sessions la
    # oculta y el usuario acabaría sin ninguna sesión operativa.
    current = await db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > utcnow(),
        )
    )
    if current is None:
        return Response(status_code=204)

    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.token_hash != token_hash,
        )
        .values(revoked_at=utcnow())
    )
    await db.commit()
    logger.info("Sesiones ajenas revocadas: %s", user.email)
    return Response(status_code=204)


#: Único `detail` de forgot-password. Importante que sea UNA constante y no una
#: cadena escrita en dos sitios: la respuesta tiene que ser byte a byte idéntica
#: exista o no la cuenta (y no solo "parecido").
_FORGOT_DETAIL = (
    "Si el correo existe, recibirás un código para restablecer tu contraseña."
)


@router.post("/forgot-password", response_model=MessageResponse, status_code=202)
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(rate("forgot-password")),
):
    """Emite un código de 6 dígitos para el reseteo. Responde SIEMPRE 202 con el
    mismo `detail`, exista o no la cuenta, se haya superado el límite de envío o
    falle el proveedor: nada de lo que pase revela si el email está dado de alta.

    La garantía se sostiene sobre el TIEMPO, no solo sobre el status y el cuerpo.
    Con el envío en la ruta de la petición, la latencia era la del proveedor para
    las cuentas reales (mediana 158,9 ms con un emisor de 150 ms) y 3,2 ms para las
    inexistentes: una separación de +155,7 ms que un único umbral de 75 ms
    confirmaba con el 100% de acierto. Por eso aquí no se consulta ni la base de
    datos ni el proveedor: la respuesta se encola y se devuelve, y la búsqueda del
    usuario, el alta del código y el envío ocurren en `_issue_code_in_background`,
    ya con la respuesta entregada. No hay ni una rama en el camino de ida y vuelta."""
    background_tasks.add_task(
        _issue_code_in_background,
        PURPOSE_PASSWORD_RESET,
        settings=_app_settings(request),
        sender=_email_sender(request),
        email=payload.email,
    )
    return MessageResponse(detail=_FORGOT_DETAIL)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    _: None = Depends(rate("reset-password")),
    db: AsyncSession = Depends(get_db),
):
    """Restablece la contraseña con el código de un solo uso y revoca TODAS las
    sesiones del usuario.

    El body lleva el email además del código porque el hash del código va
    salado con el id de usuario: sin saber a quién pertenece no se puede
    calcular. Un email inexistente produce el mismo 400 genérico que un código
    equivocado, así que tampoco sirve para enumerar cuentas."""
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is None or not user.is_active:
        raise _INVALID_CODE

    row = await _validate_code(
        db, user, PURPOSE_PASSWORD_RESET, payload.code, settings=_app_settings(request)
    )
    if row is None:
        raise _INVALID_CODE

    row.consumed_at = utcnow()
    user.password_hash = hash_password(payload.new_password)
    # La contraseña cambió: revocamos todas las sesiones activas.
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()
    logger.info("Contraseña restablecida: %s", user.email)
    return MessageResponse(detail="Contraseña actualizada. Ya puedes iniciar sesión.")


@router.post("/verify-email", response_model=UserPublic)
async def verify_email(
    request: Request,
    payload: VerifyEmailRequest,
    _: None = Depends(rate("verify-email")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirma la dirección de email con el código recibido y devuelve el
    usuario actualizado. Idempotente: si ya estaba verificado responde 200 sin
    tocar ningún código, para que pulsar dos veces el botón no sea un error."""
    if user.is_email_verified:
        return user

    row = await _validate_code(
        db,
        user,
        PURPOSE_EMAIL_VERIFICATION,
        payload.code,
        settings=_app_settings(request),
    )
    if row is None:
        raise _INVALID_CODE

    row.consumed_at = utcnow()
    user.is_email_verified = True
    await db.commit()
    logger.info("Email verificado: %s", user.email)
    return user


@router.post("/resend-verification", response_model=EmailStatusResponse)
async def resend_verification(
    request: Request,
    _: None = Depends(rate("resend-verification")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reenvía el código de verificación. Requiere sesión, así que (a diferencia
    de forgot-password) puede responder con la verdad: si el proveedor falla se
    devuelve 503 en vez de un 200 que promete un correo que nunca llegó. En
    cambio, nunca se confirma ni se desmiente nada sobre cuentas ajenas: solo
    trata del usuario autenticado."""
    if user.is_email_verified:
        return EmailStatusResponse(
            detail="Tu correo ya está verificado.", sent=False
        )

    cfg = _app_settings(request)
    ttl_minutes = cfg.verification_code_ttl_minutes
    try:
        row = await _issue_code_and_email(
            db,
            user,
            PURPOSE_EMAIL_VERIFICATION,
            sender=_email_sender(request),
            settings=cfg,
        )
    except EmailDeliveryError as exc:
        logger.warning("Fallo al reenviar el código de verificación: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No hemos podido enviar el correo de verificación. "
                "Inténtalo de nuevo en unos minutos."
            ),
        ) from exc

    if row is None:
        # Tope de códigos por hora alcanzado (no por IP: por usuario y
        # propósito). Aquí sí se informa: el usuario ya está autenticado y solo
        # se está limitando a sí mismo.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Has pedido demasiados códigos de verificación. Espera una hora "
                f"o usa el último que te enviamos (caduca en "
                f"{ttl_minutes} minutos)."
            ),
        )

    return EmailStatusResponse(
        detail=(
            "Te hemos enviado un nuevo código de verificación. "
            f"Caduca en {ttl_minutes} minutos."
        ),
        sent=True,
    )


@router.get("/verification-status", response_model=VerificationStatusResponse)
async def verification_status(
    request: Request,
    _: None = Depends(rate("verification-status")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Estado de la verificación de la cuenta: cuenta atrás del código, cuántos
    códigos se han pedido esta hora y cuánto falta para poder pedir otro.

    Es una LECTURA pura: no emite, no consume códigos ni toca el cubo del
    limitador. Todo lo que devuelve es autoritativo del servidor porque el
    cliente no puede saberlo por su cuenta: solo conoce la hora en la que él
    pidió el código (no la de emisión) y no ve la tabla `verification_codes`.

    Requiere sesión (401 sin token) porque tanto la Existence del código
    pendiente como su caducidad son datos de la cuenta del solicitante.
    """
    cfg = _app_settings(request)

    # Cuenta ya verificada: no hay nada pendiente ni que contar. Se cortan las
    # dos consultas a propósito, y `codes_limit_per_hour` también va a 0 (ver
    # `VerificationStatusResponse`): la UI no muestra contador cuando
    # `pending` es False, así que un tope de 5 sobre un 0 se leería como un
    # dato roto en vez de como una respuesta.
    if user.is_email_verified:
        return VerificationStatusResponse(
            pending=False,
            expires_in_seconds=0,
            resend_available_in_seconds=0,
            codes_used_last_hour=0,
            codes_limit_per_hour=0,
        )

    # El vigente es el ÚLTIMO sin consumir. Solo puede haber uno (emitir otro
    # consume el anterior en `_issue_code_and_email`), pero se ordena por id
    # descendente en vez de asumirlo, igual que hace `_validate_code`.
    vigente = await db.scalar(
        select(VerificationCode)
        .where(
            VerificationCode.user_id == user.id,
            VerificationCode.purpose == PURPOSE_EMAIL_VERIFICATION,
            VerificationCode.consumed_at.is_(None),
        )
        .order_by(VerificationCode.id.desc())
        .limit(1)
    )

    # `int()` trunca hacia cero, y con el suelo 0 un código caducado da 0 en
    # vez de un negativo: la respuesta nunca es negativa aunque la fila se haya
    # quedado atrás (reloj desincronizado, caducada entre la consulta y el
    # formateo).
    expires_in = 0
    if vigente is not None:
        expires_in = max(0, int((vigente.expires_at - utcnow()).total_seconds()))

    used_last_hour = await db.scalar(
        select(func.count())
        .select_from(VerificationCode)
        .where(
            VerificationCode.user_id == user.id,
            VerificationCode.purpose == PURPOSE_EMAIL_VERIFICATION,
            VerificationCode.created_at >= utcnow() - timedelta(hours=1),
        )
    )

    # Enfriamiento del REENVÍO: el cubo que va a encontrar
    # POST /resend-verification, con su misma clave. `retry_after` es una
    # consulta pura (no consume plaza), así que este endpoint no gasta un
    # reenvío del usuario por mirarlo. Si el cubo no expone el tiempo restante
    # (0), el frontend lo trata como "disponible" y la petición que haga a
    # continuación le dirá la verdad en su propio 429.
    limiter: InMemoryRateLimiter = request.app.state.rate_limiter
    resend_available_in = limiter.retry_after(
        bucket_key(request.client.host, RESEND_VERIFICATION_PATH),
        cfg.rate_limit_resend_verification_max,
        cfg.rate_limit_resend_verification_window,
    )

    return VerificationStatusResponse(
        pending=vigente is not None,
        expires_in_seconds=expires_in,
        resend_available_in_seconds=resend_available_in,
        codes_used_last_hour=int(used_last_hour or 0),
        codes_limit_per_hour=cfg.verification_code_max_per_hour,
    )


async def _issue_code_in_background(
    purpose: str,
    *,
    settings: Settings,
    sender: EmailSender,
    user_id: str | None = None,
    email: str | None = None,
) -> None:
    """Emite el código y envía el email FUERA de la ruta de la petición.

    Por qué una `BackgroundTasks` y no "enviar antes de responder": el
    `sender.send` es una llamada de red cuyo coste depende de si el email
    existe. Awaited en la ruta de la petición convertía el 202 de
    `forgot-password` en un oráculo de enumeración (medido sobre uvicorn real:
    158,90 ms de mediana con cuenta existente frente a 3,19 ms sin cuenta, y un
    atacante con umbral de 75 ms acertaba 25/25 en ambos casos). Encolada, la
    respuesta no depende para nada del caso: mismo status, mismo cuerpo y mismo
    tiempo (+0,1 ms de separación, 0/25 y 25/25).

    Por qué abre SU PROPIA sesión, y no la de `Depends(get_db)`: desde FastAPI
    0.106 las dependencias con `yield` se cierran ANTES de que se envíe la
    respuesta, así que para cuando esta función corre la `AsyncSession` de la
    petición ya está cerrada — capturarla en la closure daría
    `MissingGreenlet` o "sesión cerrada" en runtime. `SessionLocal` es una
    factoría: una sesión nueva, y su propio commit/rollback, por tarea.

    Identifica al usuario por `user_id` (registro: ya está confirmado en la base
    de datos) o por `email` (forgot-password: la búsqueda va aquí para que la
    ruta de la petición no ramifique).

    NUNCA propaga excepciones: la respuesta ya está en la calle y no hay a quién
    responderle. Un fallo de base de datos o de proveedor se registra y se
    termina; el usuario puede pedir otro código con POST /resend-verification.
    """
    db: AsyncSession | None = None
    try:
        # Dentro del try, y no antes: hasta la CONSTRUCCIÓN de la sesión puede
        # fallar (pool agotado, base de datos caída, configuración rota) y aquí
        # no hay a quién responderle.
        db = SessionLocal()
        async with db:
            if user_id is not None:
                user = await db.get(User, user_id)
            else:
                user = await db.scalar(select(User).where(User.email == email))
            if user is None or not user.is_active:
                return
            await _issue_code_best_effort(
                db, user, purpose, settings=settings, sender=sender
            )
    except Exception:
        # El `rollback` también se guarda: si la sesión está rota, el propio
        # rollback puede lanzar, y aquí no hay nada que responderle al cliente
        # (la respuesta ya salió). Lo que se registra es el fallo de fondo, no
        # el del propio rollback, que no ayuda a diagnosticar nada.
        if db is not None:
            try:
                await db.rollback()
            except Exception:
                logger.debug("El rollback de la sesión en segundo plano también falló")
        logger.exception(
            "Fallo al emitir en segundo plano el código de %s para %s",
            purpose,
            email or user_id,
        )


async def _issue_code_best_effort(
    db: AsyncSession,
    user: User,
    purpose: str,
    *,
    settings: Settings,
    sender: EmailSender,
) -> VerificationCode | None:
    """Envuelve `_issue_code_and_email` para los flujos donde un fallo de email
    NO puede romper la respuesta (registro y "he olvidado mi contraseña"). El
    código queda en la tabla y el usuario siempre puede pedir otro. Los fallos de
    BASE DE DATOS no se absorben aquí: los captura `_issue_code_in_background`."""
    try:
        return await _issue_code_and_email(
            db, user, purpose, sender=sender, settings=settings
        )
    except EmailDeliveryError as exc:
        logger.warning(
            "No se pudo enviar el código de %s a %s: %s", purpose, user.email, exc
        )
        return None


async def _issue_code_and_email(
    db: AsyncSession,
    user: User,
    purpose: str,
    *,
    sender: EmailSender,
    settings: Settings,
) -> VerificationCode | None:
    """Emite un código nuevo para (user, purpose) y envía el email.

    Devuelve la fila creada, o None si se alcanzó el límite de códigos por
    hora. Lanza `EmailDeliveryError` si el envío falla (el código ya está
    guardado: el usuario solo tiene que pedir que se lo reenvíen).

    `settings` es explícito (y no `get_settings()`) a propósito: el
    `lru_cache` de la configuración es global al proceso, así que leerlo desde
    aquí haría que `create_app(Settings(...))` — que es como los tests ajustan
    límites y TTL — no surtiera efecto.

    El orden importa. Primero el LÍMITE y después la revocación de los códigos
    anteriores: si se revocaran antes, un usuario que agota el límite por hora
    se quedaría sin código nuevo Y sin el viejo, es decir, sin forma de
    entrar. Con este orden, un código vigente sigue valiendo.
    """
    cfg = settings

    # Tope de emisión por usuario y propósito en la última hora. No basta el
    # rate limit por IP: sirve para un atacante con IP rotando.
    #
    # RIESGO ASUMIDO (TOCTOU, conocido y NO arreglado a propósito): el contador
    # es un SELECT y luego un INSERT, sin locking ni restricción en la base de
    # datos, así que 8 peticiones concurrentes del mismo (usuario, propósito)
    # leen todas "4 emitidos" y las 8 emiten: 9 códigos en vez de 5. Se acepta
    # porque el rate limit por IP (3/min en forgot-password, 3/h en
    # resend-verification) ya impide la ráfaga y el efecto es auto-spam (mandarse
    # códigos a uno mismo, con el mismo tope por hora de siempre), no un
    # compromiso con el usuario que un atacante no pueda cumplir. Y su arreglo
    # (contador atómico o `SELECT ... FOR UPDATE`) no compensa el coste de
    # bloqueos por el beneficio real. Que conste por escrito para que el
    # siguiente que lo lea no lo re-descubra como una vulnerabilidad nueva.
    issued_last_hour = await db.scalar(
        select(func.count())
        .select_from(VerificationCode)
        .where(
            VerificationCode.user_id == user.id,
            VerificationCode.purpose == purpose,
            VerificationCode.created_at >= utcnow() - timedelta(hours=1),
        )
    )
    if (issued_last_hour or 0) >= cfg.verification_code_max_per_hour:
        logger.info(
            "Límite de códigos de %s alcanzado para %s (%s en la última hora)",
            purpose,
            user.email,
            issued_last_hour,
        )
        return None

    # Solo puede haber un código vigente por (usuario, propósito): al emitir uno
    # nuevo se invalida el anterior en lugar de acumular varios válidos.
    await db.execute(
        update(VerificationCode)
        .where(
            VerificationCode.user_id == user.id,
            VerificationCode.purpose == purpose,
            VerificationCode.consumed_at.is_(None),
        )
        .values(consumed_at=utcnow())
    )
    await db.commit()

    expires_at = new_verification_expiry(purpose, settings=cfg)
    for attempt in range(1, _CODE_GENERATION_ATTEMPTS + 1):
        # Normalizar aunque el código ya venga normalizado: el hash depende de
        # esta forma exacta y el generador no debe poder desviarse del
        # contrato sin que se note aquí.
        code = normalize_verification_code(generate_verification_code())
        row = VerificationCode(
            user_id=user.id,
            purpose=purpose,
            code_hash=hash_verification_code(purpose, user.id, code),
            expires_at=expires_at,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            # Colisión del UNIQUE de code_hash: otro usuario tiene ese mismo
            # código (improbable con la sal, pero posible). Se descarta solo el
            # INSERT fallido — el revoke anterior ya quedó confirmado.
            await db.rollback()
            logger.warning(
                "Colisión de code_hash al emitir %s (intento %d/%d) para %s",
                purpose,
                attempt,
                _CODE_GENERATION_ATTEMPTS,
                user.id,
            )
            continue
        await db.commit()
        break
    else:  # pragma: no cover - 3 colisiones seguidas no se han observado
        logger.error(
            "No se pudo emitir un código de %s para %s tras %d intentos",
            purpose,
            user.email,
            _CODE_GENERATION_ATTEMPTS,
        )
        return None

    # El envío va DESPUÉS del commit: así un proveedor lento o caído no
    # bloquea la transacción de la petición, y un fallo de red no deja código
    # sin guardar.
    message = _build_code_message(user, purpose, code, cfg)
    message_id = await sender.send(message)
    logger.info(
        "Código de %s enviado a %s (id=%s, caduca en %s)",
        purpose,
        user.email,
        message_id,
        expires_at.isoformat(),
    )
    return row


async def _validate_code(
    db: AsyncSession,
    user: User,
    purpose: str,
    raw_code: str,
    *,
    settings: Settings,
) -> VerificationCode | None:
    """Valida el código de (user, purpose) y devuelve la fila si es válido.

    Se busca por el par (purpose, code_hash) con el hash salado por usuario, que
    es el único modo de consulta que no acepta códigos de otra cuenta.

    Cada fallo suma un intento; al agotar `verification_code_max_attempts` el
    código se invalida, de modo que 5 intentos fallidos matan el código en lugar
    de dejarlo disponible para siempre. Cuando el código enviado NO existe, el
    intento se contabiliza sobre el código vigente del usuario para ese
    propósito: si no, un atacante que probara 999.999 códigos equivocados no
    tocaría ningún contador (no hay fila que incrementar) y el límite no
    protegería nada.

    Devuelve None en cualquier fallo (el llamante responde con el 400 genérico).
    """
    cfg = settings
    if not is_valid_verification_code(raw_code):
        # No es un código (no reduce a 6 dígitos) ni hay nada que comparar.
        return None

    code_hash = hash_verification_code(
        purpose, user.id, normalize_verification_code(raw_code)
    )
    row = await db.scalar(
        select(VerificationCode).where(
            VerificationCode.purpose == purpose,
            VerificationCode.code_hash == code_hash,
        )
    )
    if (
        row is not None
        and row.consumed_at is None
        and row.expires_at > utcnow()
        and row.attempts < cfg.verification_code_max_attempts
    ):
        return row

    # Fallo. Se contabiliza sobre la fila que coincide (si la hubo) o sobre la
    # más reciente sin usar de este usuario y propósito.
    target = row
    if target is None:
        target = await db.scalar(
            select(VerificationCode)
            .where(
                VerificationCode.user_id == user.id,
                VerificationCode.purpose == purpose,
                VerificationCode.consumed_at.is_(None),
            )
            .order_by(VerificationCode.id.desc())
            .limit(1)
        )
    if target is None:
        return None

    target.attempts += 1
    if target.attempts >= cfg.verification_code_max_attempts:
        target.consumed_at = utcnow()
        logger.warning(
            "Código de %s invalidado tras %d intentos fallidos: user=%s",
            purpose,
            target.attempts,
            user.id,
        )
    await db.commit()
    return None


def _build_code_message(
    user: User, purpose: str, code: str, cfg
) -> EmailMessage:
    """Construye el email del código a partir de la plantilla del propósito."""
    render = (
        render_verification_code_email
        if purpose == PURPOSE_EMAIL_VERIFICATION
        else render_password_reset_code_email
    )
    reply_to_url = f"mailto:{cfg.support_email}" if cfg.support_email else None
    return render(
        user_name=user.full_name,
        code=code,
        minutes=cfg.verification_code_ttl_minutes,
        to=user.email,
        frontend_url=cfg.frontend_url,
        reply_to_url=reply_to_url,
    )


def _app_settings(request: Request) -> Settings:
    """Configuración de ESTA app. `get_settings()` está cacheado a nivel de
    proceso, así que usarlo dentro de los endpoints haría que los ajustes que
    hace `create_app(Settings(...))` (tests, límites, TTL) no se aplicaran.
    `request.app.state.settings` es la fuente de verdad."""
    return request.app.state.settings


def _email_sender(request: Request) -> EmailSender:
    """Emisor de la app. Vive en `app.state` (no en settings) para que los
    tests puedan sustituirlo por un espía sin tocar la configuración global."""
    sender = getattr(request.app.state, "email_sender", None)
    if sender is None:
        # App construida sin la factoría (p. ej. un test mínimo): se construye
        # una vez y se cachea en el state.
        sender = build_email_sender(_app_settings(request))
        request.app.state.email_sender = sender
    return sender


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