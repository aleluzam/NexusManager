"""Suite de las plantillas de email (`app/templates/email/`).

Un email es el único sitio donde el nombre del usuario (dato ajeno al sistema)
se interpola en un documento que va a ejecutar un visor de correo: por eso se
comprueba el escapado, la autosuficiencia del HTML (todo el estilo inline, sin
JS) y que la paleta sea la del design system "Unified Command System".

Se renderizan las plantillas directamente (no a través de la API): lo que se
comprueba aquí es el contrato de `render_*`, no el del endpoint.
"""

import re

import pytest

from app.templates.email import (
    build_reset_url,
    render_password_reset_code_email,
    render_verification_code_email,
)
from app.templates.email.layout import (
    BRAND_ACCENT,
    BRAND_PRIMARY,
    CODE_BACKGROUND,
    PAGE_BACKGROUND,
    esc,
)

MINUTES = 10
FRONTEND = "https://app.nexusmanager.test"
XSS = '<script>alert(1)</script>'
CODE = "042731"
DISPLAY_CODE = "042 731"

#: Todo lo que el HTML puede enlazar fuera del mensaje.
_EXTERNAL_URL_RE = re.compile(r"""https?://[^"'\s>]+""")
#: Atributos de evento en línea (onclick=, onerror=, ...): JS embebido.
_EVENT_ATTR_RE = re.compile(r"""\son[a-z]+\s*=""", re.IGNORECASE)

RENDERERS = {
    "verificacion": lambda **kw: render_verification_code_email(
        user_name=kw.get("user_name", "Ana Gómez"),
        code=kw.get("code", CODE),
        minutes=MINUTES,
        to="ana@empresa.com",
        frontend_url=kw.get("frontend_url", FRONTEND),
        reply_to_url=kw.get("reply_to_url", "mailto:soporte@nexusmanager.test"),
    ),
    "reseteo": lambda **kw: render_password_reset_code_email(
        user_name=kw.get("user_name", "Ana Gómez"),
        code=kw.get("code", CODE),
        minutes=MINUTES,
        to="ana@empresa.com",
        frontend_url=kw.get("frontend_url", FRONTEND),
        reply_to_url=kw.get("reply_to_url", "mailto:soporte@nexusmanager.test"),
    ),
}

ALL_TEMPLATES = list(RENDERERS)


def strip_head_extras(html: str) -> str:
    """Quita la hoja de estilos y el <link> de la fuente: con eso simulado como
    bloqueado, el email debe seguir siendo legible y completo."""
    html = re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL)
    html = re.sub(r"<link[^>]*>", "", html)
    return html


# ---------------------------------------------------------------------------
# Contenido
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_code_appears_in_html_and_plain_text(nombre):
    mensaje = RENDERERS[nombre]()
    # En el texto plano, tal cual; en el HTML, agrupado en tercios
    assert CODE in mensaje.text
    assert DISPLAY_CODE in mensaje.html
    assert f"    {CODE}" in mensaje.text  # en línea propia, fácil de dictar
    # Y el preheader lo lleva para verlo en la bandeja de entrada
    assert DISPLAY_CODE in mensaje.html
    assert str(MINUTES) in mensaje.text


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_message_metadata(nombre):
    mensaje = RENDERERS[nombre]()
    assert mensaje.to == "ana@empresa.com"
    assert mensaje.subject
    assert mensaje.html.lstrip().startswith("<!DOCTYPE html>")
    assert 'lang="es"' in mensaje.html
    assert "NexusManager" in mensaje.subject
    purposes = [t.get("value") for t in mensaje.tags if t.get("name") == "purpose"]
    assert purposes == [("email_verification" if nombre == "verificacion" else "password_reset")]
    assert {"name": "app", "value": "nexusmanager"} in mensaje.tags


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_code_is_never_put_in_a_url(nombre):
    """El código es lo que teclea el usuario: no puede viajar en un enlace (queda
    en el historial, en los proxies y en los logs del cliente de correo)."""
    html = RENDERERS[nombre]().html
    for url in _EXTERNAL_URL_RE.findall(html):
        assert CODE not in url, f"el código viaja en una URL: {url}"
    # Lo que sí viaja en el enlace del CTA es el email precargado, no el código
    assert "email=ana%40empresa.com" in build_reset_url(FRONTEND, "ana@empresa.com")


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_ttl_is_rendered(nombre):
    mensaje = RENDERERS[nombre]()
    assert f"{MINUTES} minutos" in mensaje.text
    assert f"{MINUTES} minutos" in mensaje.html


# ---------------------------------------------------------------------------
# Escapado del nombre (XSS)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_user_name_is_escaped_in_html(nombre):
    mensaje = RENDERERS[nombre](user_name=XSS)
    html = mensaje.html
    assert XSS not in html
    assert "<script>" not in html
    assert "alert(1)" in html  # sigue siendo texto visible...
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html  # ...pero inerte
    # El escapado no rompe el resto del documento
    assert html.count("<html") == 1 and html.rstrip().endswith("</html>")


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_user_name_quotes_are_escaped(nombre):
    """Sin `quote=True`, un nombre con comillas rompería el atributo de estilo
    y permitiría inyectar `onmouseover=`. La prueba no es "no aparece la
    palabra" (aparece, pero escapada) sino que el nombre NO puede añadir ni un
    solo tag nuevo: la estructura del documento es idéntica."""
    benigno = RENDERERS[nombre](user_name="Ana Gómez")
    peligroso = RENDERERS[nombre](user_name='" onmouseover="alert(1)')
    assert '" onmouseover="' not in peligroso.html
    assert "&quot; onmouseover=&quot;alert(1)" in peligroso.html
    # Mismo número de tags: el nombre no ha abierto ni cerrado ninguna etiqueta
    assert peligroso.html.count("<") == benigno.html.count("<")
    assert peligroso.html.count(">") == benigno.html.count(">")


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_plain_text_version_keeps_the_name_readable(nombre):
    """En text/plain NO se escapa (no hay interpretaion): el usuario debe ver su
    nombre tal cual."""
    mensaje = RENDERERS[nombre](user_name=XSS)
    assert mensaje.text.startswith(f"Hola, {XSS},")
    assert "&lt;" not in mensaje.text


def _visible_text(html: str) -> str:
    """Texto que un humano lee en el email: sin estilos, sin etiquetas, sin
    entidades y sin espacios (para que comparar no dependa del formato)."""
    import html as html_mod

    sin_estilos = re.sub(r"<style.*?</style>", " ", html, flags=re.DOTALL)
    sin_tags = re.sub(r"<[^>]+>", " ", sin_estilos)
    return re.sub(r"\s+", "", html_mod.unescape(sin_tags))


def _normalizar(texto: str) -> str:
    return re.sub(r"[\s.:;!]+$", "", re.sub(r"\s+", "", texto))


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_html_and_plain_text_say_the_same_thing(nombre):
    """REGRESIÓN: las dos versiones del email se editan a mano, una en `_text` y
    otra en `_paragraph`, y nada impedía que se desincronizaran. Pasó: la de
    html decía "nooccurrirá nada" y la de texto "no ocurrirá nada", y ninguna
    batería se enteró.

    Aquí cada párrafo del texto plano tiene que aparecer también en el texto
    visible del HTML. Se comparan sin espacios y sin puntuación final (las dos
    versiones cortan la frase en puntos distintos) y se descartan los párrafos
    que solo existen en una: el saludo, el código, el enlace alternativo al
    botón y la firma."""
    mensaje = RENDERERS[nombre]()
    visible = _visible_text(mensaje.html)

    comprobados = 0
    for parrafo in mensaje.text.split("\n\n"):
        limpio = parrafo.strip()
        if not limpio:
            continue
        if limpio.startswith("Hola,"):
            continue  # saludo: en el HTML no lleva la coma
        if re.fullmatch(r"\d{6}", limpio):
            continue  # el bloque de código: el HTML lo agrupa en "042 731"
        if "http" in limpio or limpio.startswith("—"):
            continue  # enlace alternativo al botón y firma
        comprobados += 1
        assert _normalizar(limpio) in visible, (
            f"[{nombre}] el párrafo de texto plano no aparece en el HTML:\n"
            f"  texto: {limpio!r}"
        )
    # Sanity: el test no está pasando por comprobar cero párrafos.
    assert comprobados >= 3, comprobados


def test_esc_helper():
    assert esc("<b>") == "&lt;b&gt;"
    assert esc('a"b') == "a&quot;b"
    assert esc("a'b") == "a&#x27;b"
    assert esc(None) == ""
    assert esc(42) == "42"


# ---------------------------------------------------------------------------
# Autosuficiencia del HTML (nada de CSS externo ni JS)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_html_has_no_javascript(nombre):
    html = RENDERERS[nombre]().html
    assert "<script" not in html.lower()
    assert "javascript:" not in html.lower()
    assert not _EVENT_ATTR_RE.search(html)


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_html_styling_is_inline(nombre):
    """Los clientes de correo borran o ignoran <style>: todo lo que da color,
    tamaño y anchura tiene que estar en atributos style=""."""
    html = RENDERERS[nombre]().html
    assert html.count('style="') >= 10
    # Estilos clave en línea: el fondo de la página, la tarjeta y el código
    assert f"background-color:{PAGE_BACKGROUND}" in html
    assert "max-width:600px" in html
    assert "font-size:40px" in html  # el bloque del código
    assert f"background-color:{CODE_BACKGROUND}" in html


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_html_survives_blocking_the_external_stylesheet(nombre):
    """Con la fuente externa y la hoja de estilos bloqueadas, el email sigue
    conteniendo el código, el saludo y la paleta de marca."""
    html = RENDERERS[nombre]().html
    limpio = strip_head_extras(html)
    assert "<link" not in limpio and "<style>" not in limpio
    assert DISPLAY_CODE in limpio
    assert "Hola, Ana Gómez" in limpio
    assert BRAND_PRIMARY in limpio and BRAND_ACCENT in limpio


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_only_font_provider_is_external(nombre):
    """Único recurso externo: la webfont de marca, y es una mejora progresiva
    (hay una pila de system fonts detrás)."""
    html = RENDERERS[nombre]().html
    urls = set(_EXTERNAL_URL_RE.findall(html))
    fuera = {u for u in urls if not u.startswith(("https://fonts.googleapis.com", "https://fonts.gstatic.com"))}
    if nombre == "reseteo":
        assert all(u.startswith(FRONTEND) for u in fuera), fuera
    else:
        assert fuera == set(), fuera  # la verificación no lleva CTA
    assert "fonts.googleapis.com" in html  # la fuente sí está declarada
    assert "sans-serif" in html  # y hay fallback de sistema


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_layout_uses_tables_not_flex_or_grid(nombre):
    """Outlook (Word) no soporta flex/grid: el layout es de tablas."""
    html = RENDERERS[nombre]().html
    assert 'role="presentation"' in html
    for prohibido in ("display:flex", "display:grid", "display:flex;", "display:grid;"):
        assert prohibido not in html


# ---------------------------------------------------------------------------
# Paleta del design system
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_brand_colors(nombre):
    """Tokens del design system "Unified Command System" (design/*/DESIGN.md):
    primario #511877 y acento coral #FF5722."""
    assert BRAND_PRIMARY == "#511877"
    assert BRAND_ACCENT == "#FF5722"
    html = RENDERERS[nombre]().html
    assert BRAND_PRIMARY in html  # títulos, botón, código
    assert BRAND_ACCENT in html  # la línea de caducidad
    assert PAGE_BACKGROUND in html


def test_plus_jakarta_sans_is_the_brand_font():
    html = RENDERERS["reseteo"]().html
    assert "Plus Jakarta Sans" in html


# ---------------------------------------------------------------------------
# Pie, soporte y CTA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_footer_explains_why_and_links_support(nombre):
    mensaje = RENDERERS[nombre]()
    assert "Este mensaje se envió a ti porque" in mensaje.html
    assert "mailto:soporte@nexusmanager.test" in mensaje.html
    # Email transaccional: NO hay enlace de baja (el destinatario es el titular)
    assert "unsubscribe" not in mensaje.html.lower()
    assert "darte de baja" not in mensaje.html.lower()


@pytest.mark.parametrize("nombre", ALL_TEMPLATES)
def test_footer_omits_support_when_not_configured(nombre):
    """Sin `support_email` el pie omite el enlace en vez de inventar una URL."""
    mensaje = RENDERERS[nombre](reply_to_url=None)
    assert "mailto:" not in mensaje.html
    assert "Soporte" not in mensaje.html
    assert "Este mensaje se envió a ti porque" in mensaje.html


def test_reset_email_has_a_cta_with_the_email_prefilled():
    mensaje = RENDERERS["reseteo"]()
    assert "Restablecer contraseña" in mensaje.html
    assert "/auth?mode=reset" in mensaje.html
    # El email va urlencodeado y escapado para HTML
    assert "ana%40empresa.com" in mensaje.text
    assert "ana%40empresa.com" in mensaje.html.replace("&amp;", "&")


def test_reset_email_without_frontend_url_still_carries_the_code():
    """Sin `frontend_url` la plantilla cae al formato solo-código: sigue siendo
    funcional, no inventa un enlace."""
    mensaje = RENDERERS["reseteo"](frontend_url=None)
    assert '<a href="http' not in mensaje.html
    assert "Restablecer contraseña" not in mensaje.html
    assert CODE in mensaje.text
    assert "También puedes ir directamente a" not in mensaje.text


def test_verification_email_has_no_cta():
    """Quien recibe este correo aún no tiene sesión: el único camino es la app,
    así que no hay botón (y por tanto ningún enlace externo)."""
    mensaje = RENDERERS["verificacion"]()
    assert '<a href="http' not in mensaje.html
    assert "fonts.googleapis.com" in mensaje.html  # la única cosa externa


def test_build_reset_url():
    url = build_reset_url(FRONTEND, "ana+prueba@empresa.com")
    assert url.startswith(f"{FRONTEND}/auth?mode=reset&email=")
    assert "ana%2Bprueba%40empresa.com" in url
    # Sin barra duplicada
    assert build_reset_url(FRONTEND + "/", "a@b.com") == f"{FRONTEND}/auth?mode=reset&email=a%40b.com"


def test_build_reset_url_escapes_an_injected_email():
    """El email se urlencodea: no puede inyectar otro parámetro en el enlace."""
    url = build_reset_url(FRONTEND, 'a@b.com&x=1" onmouseover="alert(1)')
    assert "&x=1" not in url.replace("%26x%3D1", "")
    assert '"' not in url
    assert "onmouseover" in url  # va dentro, escapado, no como atributo
