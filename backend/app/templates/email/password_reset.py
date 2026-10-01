"""Email de reseteo de contraseña (código de 6 dígitos + CTA)."""

from urllib.parse import quote

from app.security import format_verification_code_for_display
from app.services.email import EmailMessage
from app.templates.email.layout import (
    BRAND_ACCENT,
    BRAND_PRIMARY,
    FONT_STACK,
    SUPPORT_TEXT,
    _button,
    _code_block,
    _paragraph,
    esc,
    render_layout,
)

SUBJECT = "Restablece tu contraseña de NexusManager"
RESET_PATH = "/auth?mode=reset"


def build_reset_url(frontend_url: str, to_email: str) -> str:
    """Enlace del CTA: lleva el email precargado para que el usuario solo tenga
    que teclear el código. El email va urlencodeado (`quote`) porque es un
    parámetro de query; el escapado de HTML lo hace el layout."""
    base = frontend_url.rstrip("/")
    return f"{base}{RESET_PATH}&email={quote(to_email, safe='')}"


def render_password_reset_code_email(
    user_name: str,
    code: str,
    minutes: int,
    *,
    to: str,
    frontend_url: str | None = None,
    reply_to_url: str | None = None,
) -> EmailMessage:
    """Mensaje de reseteo. A diferencia del de verificación, aquí el código es
    una confirmación y no la vía principal: el CTA abre el formulario del
    frontend. `frontend_url` NUNCA se inventa aquí, viene de la configuración;
    sin él, la plantilla cae al formato solo-código (sigue siendo funcional).
    """
    display_code = format_verification_code_for_display(code)
    greeting = esc(user_name)
    reset_url = build_reset_url(frontend_url, to) if frontend_url else ""

    body_rows = f"""
            <tr>
              <td style="padding:8px 32px 20px 32px;font-family:{FONT_STACK};font-size:22px;font-weight:800;line-height:28px;color:{BRAND_PRIMARY};">
                Hola, {greeting}
              </td>
            </tr>"""

    body_rows += _paragraph(
        "Recibimos una solicitud para restablecer la contraseña de tu cuenta. "
        "Usa este código para confirmar que eres tú:"
    )
    body_rows += _code_block(display_code)
    body_rows += _paragraph(
        f'<strong style="color:{BRAND_ACCENT};">El código caduca en {int(minutes)} minutos.</strong> '
        "Después de cambiar la contraseña se cierran todas tus sesiones "
        "abiertas en otros dispositivos, por seguridad."
    )
    body_rows += _paragraph(
        f'<span style="color:{SUPPORT_TEXT};">Si no has solicitado este cambio, '
        "ignora este mensaje: tu contraseña no cambia y no necesitas hacer nada.</span>"
    )
    if reset_url:
        body_rows += _button("Restablecer contraseña", reset_url)

    html = render_layout(
        title=SUBJECT,
        preheader=f"Tu código para restablecer la contraseña es {display_code}. Caduca en {int(minutes)} minutos.",
        body_rows=body_rows,
        sender_address="NexusManager",
        reason="has solicitado restablecer la contraseña de tu cuenta de NexusManager.",
        reply_to_url=reply_to_url,
    )
    return EmailMessage(
        to=to,
        subject=SUBJECT,
        html=html,
        text=_text(user_name=user_name, code=code, minutes=minutes, reset_url=reset_url),
        tags=[
            {"name": "purpose", "value": "password_reset"},
            {"name": "app", "value": "nexusmanager"},
        ],
    )


def _text(*, user_name: str, code: str, minutes: int, reset_url: str) -> str:
    """Versión en texto plano: incluye el enlace como alternativa al botón
    (muchos clientes de correo y los gateways de empresa bloquean botones)."""
    lines = [
        f"Hola, {user_name},",
        "",
        "Recibimos una solicitud para restablecer la contraseña de tu cuenta. "
        "Usa este código para confirmar que eres tú:",
        "",
        f"    {code}",
        "",
        f"El código caduca en {int(minutes)} minutos. Después de cambiar la "
        "contraseña se cierran todas tus sesiones abiertas en otros "
        "dispositivos, por seguridad.",
    ]
    if reset_url:
        lines += ["", f"También puedes ir directamente a: {reset_url}"]
    lines += [
        "",
        "Si no has solicitado este cambio, ignora este mensaje: tu contraseña no "
        "cambia y no necesitas hacer nada.",
        "",
        "— NexusManager",
    ]
    return "\n".join(lines)
