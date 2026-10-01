"""Email de verificación de dirección (código de 6 dígitos)."""

from app.security import format_verification_code_for_display
from app.services.email import EmailMessage
from app.templates.email.layout import (
    BRAND_ACCENT,
    BRAND_PRIMARY,
    FONT_STACK,
    SUPPORT_TEXT,
    _button,
    _code_block,
    _footer,
    _paragraph,
    esc,
    render_layout,
)

SUBJECT = "Verifica tu correo en NexusManager"


def render_verification_code_email(
    user_name: str,
    code: str,
    minutes: int,
    *,
    to: str,
    frontend_url: str | None = None,
    reply_to_url: str | None = None,
) -> EmailMessage:
    """Mensaje de verificación. `user_name` se escapa siempre; `code` debe
    venir ya normalizado (6 dígitos) y se muestra agrupado en tercios.

    `frontend_url` forma parte de la firma compartida con el reseteo pero esta
    plantilla no lo usa: quien recibe este correo acaba de registrarse y aún no
    tiene sesión abierta, así que el único camino posible es la app, no un
    enlace externo."""
    display_code = format_verification_code_for_display(code)
    greeting = esc(user_name)
    del frontend_url  # firma compartida; esta plantilla no lleva CTA

    body_rows = f"""
            <tr>
              <td style="padding:8px 32px 20px 32px;font-family:{FONT_STACK};font-size:22px;font-weight:800;line-height:28px;color:{BRAND_PRIMARY};">
                Hola, {greeting}
              </td>
            </tr>"""

    body_rows += _paragraph(
        "Introduce este código en la aplicación para confirmar que este correo "
        "es tuyo y activar tu cuenta."
    )
    body_rows += _code_block(display_code)
    body_rows += _paragraph(
        f'<strong style="color:{BRAND_ACCENT};">El código caduca en {int(minutes)} minutos.</strong> '
        "Si caduca, pide uno nuevo desde la pantalla de verificación; los "
        "códigos antiguos dejan de servir en cuanto se emite uno nuevo."
    )
    body_rows += _paragraph(
        f'<span style="color:{SUPPORT_TEXT};">Si no has creado esta cuenta, '
        "ignora este mensaje: no ocurrirá nada.</span>"
    )

    html = render_layout(
        title=SUBJECT,
        preheader=f"Tu código de verificación es {display_code}. Caduca en {int(minutes)} minutos.",
        body_rows=body_rows,
        sender_address="NexusManager",
        reason="confirmar que esta dirección de correo pertenece a tu cuenta de NexusManager.",
        reply_to_url=reply_to_url,
    )
    return EmailMessage(
        to=to,
        subject=SUBJECT,
        html=html,
        text=_text(user_name=user_name, code=code, minutes=minutes),
        tags=[
            {"name": "purpose", "value": "email_verification"},
            {"name": "app", "value": "nexusmanager"},
        ],
    )


def _text(*, user_name: str, code: str, minutes: int) -> str:
    """Versión en texto plano: alternativa accesible y lo que se ve en las
    notificaciones push del propio correo."""
    return "\n".join(
        [
            f"Hola, {user_name},",
            "",
            "Introduce este código en la aplicación para confirmar que este "
            "correo es tuyo y activar tu cuenta:",
            "",
            f"    {code}",
            "",
            f"El código caduca en {int(minutes)} minutos. Si caduca, pide uno "
            "nuevo desde la pantalla de verificación; los códigos antiguos "
            "dejan de servir en cuanto se emite uno nuevo.",
            "",
            "Si no has creado esta cuenta, ignora este mensaje: no ocurrirá nada.",
            "",
            "— NexusManager",
        ]
    )
