"""Layout base de los emails transaccionales.

Restricciones del medium (no negociables en un email):
- Tablas, nunca flex/grid: Outlook (Word) no los soporta.
- Estilos INLINE: los clientes borran las hojas de estilo <style> o las ignoran.
- Ancho fijo de 600px con un contenedor que colapsa a ancho completo en móvil.
- Sin unsubscribe: son emails transaccionales (el destinatario es el titular
  de la cuenta que ha pedido la acción), no marketing.

Sistema de diseño "Unified Command System": primario #511877, acento coral
#FF5722, fondo #f4f2f7, tarjeta blanca, tipografía Plus Jakarta Sans.
"""

import html

# --- Tokens del diseño ------------------------------------------------------
BRAND_PRIMARY = "#511877"  # morado de marca: títulos, botón, código
BRAND_ACCENT = "#FF5722"  # coral: CTA secundario y detalles de urgencia
PAGE_BACKGROUND = "#f4f2f7"
CARD_BORDER = "#e6e0f0"
CODE_BACKGROUND = "#f7f4fb"
MUTED_TEXT = "#6b6180"
BODY_TEXT = "#2c2440"
SUPPORT_TEXT = "#4a4160"

# Pila tipográfica: la fuente de marca primero y una cadena de sistema detrás,
# porque en un email no se puede asumir que haya webfont disponible.
FONT_STACK = (
    "'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', "
    "Roboto, 'Helvetica Neue', Arial, sans-serif"
)
# El código va en monoespaciada + cifras tabulares: los 6 dígitos deben alinearse
# en vertical para poder compararse de un vistazo y evitar confusiones 0/O.
CODE_FONT_STACK = (
    "'SFMono-Regular', 'Roboto Mono', 'DejaVu Sans Mono', Menlo, Consolas, "
    "'Liberation Mono', monospace"
)

_TABLE_WIDTH = "600"


def esc(value: object) -> str:
    """Escape SIEMPRE de cualquier valor interpolado en el HTML.

    El nombre del usuario es el único dato realmente ajeno al sistema, así que
    es también el único vector de XSS: sin este escape, un nombre como
    `<script>alert(1)</script>` o `"><img onerror=...>` se ejecutaría en el
    visor de emails del cliente.
    """
    return html.escape("" if value is None else str(value), quote=True)


def _button(label: str, url: str) -> str:
    """Botón CTA. Recibe el valor EN CRUDO y lo escapa aquí: los parámetros de
    estas plantillas nunca deben venir pre-escapados o se verían dobles
    escapes (`&amp;amp;`) al renderizar."""
    safe_url = esc(url)
    return f"""
              <tr>
                <td style="padding:8px 0 24px 0;">
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td align="center" bgcolor="{BRAND_PRIMARY}" style="border-radius:10px;">
                        <a href="{safe_url}" style="display:inline-block;padding:14px 28px;font-family:{FONT_STACK};font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:10px;">{esc(label)}</a>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
              <tr>
                <td style="padding:0 0 24px 0;font-family:{FONT_STACK};font-size:13px;line-height:20px;color:{MUTED_TEXT};text-align:center;word-break:break-all;">
                  Si el botón no funciona, copia esta dirección en tu navegador:<br>
                  <a href="{safe_url}" style="color:{BRAND_PRIMARY};text-decoration:underline;">{esc(url)}</a>
                </td>
              </tr>"""


def _code_block(display_code: str) -> str:
    """Bloque destacado con el código. Contraste #511877 sobre #f7f4fb (muy por
    encima de AA) y borde izquierdo de 6px para que se localice de un vistazo
    al abrir el correo."""
    return f"""
              <tr>
                <td style="padding:0 0 8px 0;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{CODE_BACKGROUND};border:1px solid {CARD_BORDER};border-left:6px solid {BRAND_PRIMARY};border-radius:12px;">
                    <tr>
                      <td align="center" style="padding:26px 16px 22px 16px;">
                        <div style="font-family:{CODE_FONT_STACK};font-size:40px;line-height:48px;font-weight:700;letter-spacing:12px;text-indent:12px;color:{BRAND_PRIMARY};font-variant-numeric:tabular-nums;">{esc(display_code)}</div>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>"""


def _paragraph(text: str) -> str:
    return f"""
              <tr>
                <td style="padding:0 0 16px 0;font-family:{FONT_STACK};font-size:15px;line-height:24px;color:{BODY_TEXT};">{text}</td>
              </tr>"""


def _footer(sender_address: str, reason: str, reply_to_url: str | None) -> str:
    """Pie transaccional: remitente, motivo y soporte. Sin enlace de baja
    (estos correos no son marketing: el usuario titular de la cuenta es quien
    pidió la acción)."""
    support = ""
    if reply_to_url:
        support = f"""
                    <br>
                    <a href="{esc(reply_to_url)}" style="color:{BRAND_PRIMARY};text-decoration:underline;">Soporte</a>"""
    return f"""
            <tr>
              <td style="padding:24px 32px 32px 32px;border-top:1px solid {CARD_BORDER};font-family:{FONT_STACK};font-size:12px;line-height:18px;color:{MUTED_TEXT};text-align:center;">
                <p style="margin:0 0 8px 0;">
                  Este mensaje se envió a ti porque {esc(reason)}
                </p>
                <p style="margin:0;">
                  {esc(sender_address)}{support}
                </p>
              </td>
            </tr>"""


def _card(body_rows: str) -> str:
    return f"""
          <tr>
            <td align="center" style="padding:0;">
              <!--[if mso]><table role="presentation" width="{_TABLE_WIDTH}" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
              <table role="presentation" width="{_TABLE_WIDTH}" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:{_TABLE_WIDTH}px;background-color:#ffffff;border:1px solid {CARD_BORDER};border-radius:14px;">
{body_rows}
              </table>
              <!--[if mso]></td></tr></table><![endif]-->
            </td>
          </tr>"""


def render_layout(*, title: str, preheader: str, body_rows: str, sender_address: str, reason: str, reply_to_url: str | None) -> str:
    """Envuelve el contenido de una plantilla en el documento HTML completo.

    `preheader` es el texto que muestran las bandejas de entrada (Gmail,
    Outlook) junto al asunto: normalmente conviene que sea el código o el
    resumen de la acción, para no tener que abrir el correo.
    """
    header = f"""
            <tr>
              <td style="padding:28px 32px 8px 32px;font-family:{FONT_STACK};font-size:20px;font-weight:800;letter-spacing:-0.2px;color:{BRAND_PRIMARY};">
                NexusManager
              </td>
            </tr>"""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="x-apple-disable-message-reformatting">
<title>{esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
  @media only screen and (max-width:620px) {{
    .nm-cell {{ padding-left:16px !important; padding-right:16px !important; }}
    .nm-code {{ font-size:30px !important; letter-spacing:6px !important; text-indent:6px !important; }}
  }}
  body, table, td, a {{ -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }}
  table, td {{ mso-table-lspace:0pt; mso-table-rspace:0pt; }}
  img {{ -ms-interpolation-mode:bicubic; border:0; height:auto; line-height:100%; outline:none; text-decoration:none; }}
</style>
</head>
<body style="margin:0;padding:0;width:100%;background-color:{PAGE_BACKGROUND};">
<!-- Preheader: visible en la bandeja de entrada, oculto en el cuerpo. -->
<div style="display:none;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;font-family:{FONT_STACK};font-size:1px;line-height:1px;color:{PAGE_BACKGROUND};">{esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:{PAGE_BACKGROUND};">
  <tr>
    <td align="center" class="nm-cell" style="padding:32px 12px;">
      <table role="presentation" width="{_TABLE_WIDTH}" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:{_TABLE_WIDTH}px;">
{_card(header + body_rows + _footer(sender_address, reason, reply_to_url))}
      </table>
      <table role="presentation" width="{_TABLE_WIDTH}" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:{_TABLE_WIDTH}px;">
        <tr>
          <td style="padding:16px 24px 40px 24px;font-family:{FONT_STACK};font-size:11px;line-height:16px;color:{MUTED_TEXT};text-align:center;">
            NexusManager · Gestión de redes sociales para negocios y agencias
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""
