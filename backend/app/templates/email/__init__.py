"""Punto de entrada de las plantillas de email.

La API importa solo desde aquí (`from app.templates.email import ...`): el
nombre de los módulos internos es un detalle de implementación.
"""

from app.templates.email.password_reset import (
    SUBJECT as PASSWORD_RESET_SUBJECT,
    build_reset_url,
    render_password_reset_code_email,
)
from app.templates.email.verification_code import (
    SUBJECT as VERIFICATION_SUBJECT,
    render_verification_code_email,
)

__all__ = [
    "PASSWORD_RESET_SUBJECT",
    "VERIFICATION_SUBJECT",
    "build_reset_url",
    "render_password_reset_code_email",
    "render_verification_code_email",
]
