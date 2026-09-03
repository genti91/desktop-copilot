"""Páginas web del panel.

Cada sección es una página propia que comparte el layout y la barra de
navegación de `templates/layout.html`. El render es sustitución de tokens
`__NOMBRE__`, igual que hacía el dashboard original, para no sumar un motor
de plantillas.
"""

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .auth import auth_enabled
from .config import BASE_DIR
from .state import DEFAULT_PERSONALITY

TEMPLATES_DIR = BASE_DIR / "app" / "templates"

# Al entrar al panel se elige "quién sos" y queda en esta cookie. El equipo
# manda la config que se ve en /device y /personality, y si el equipo no usa
# notas/RAG la sección de Notas ni aparece.
DEVICE_COOKIE = "copilot_device"

NAV_ITEMS = (
    ("notes", "/notes", "Notas"),
    ("personality", "/personality", "Personalidad"),
    ("device", "/device", "Dispositivo"),
    ("firmware", "/firmware", "Firmware"),
)

router = APIRouter(tags=["pages"])


def selected_device(request: Request) -> str:
    from .device import safe_device

    return safe_device(request.cookies.get(DEVICE_COOKIE))


def device_has_rag(request: Request) -> bool:
    from .device import load_config

    return load_config(selected_device(request)).rag_enabled


def _nav(active: str, *, show_notes: bool) -> str:
    links = []
    for key, url, label in NAV_ITEMS:
        if key == "notes" and not show_notes:
            continue
        classes = "nav-link is-active" if key == active else "nav-link"
        current = ' aria-current="page"' if key == active else ""
        links.append(f'<a class="{classes}"{current} href="{url}">{label}</a>')
    return "".join(links)


def render_page(name: str, heading: str, subtitle: str, request: Request, **tokens) -> HTMLResponse:
    content = (TEMPLATES_DIR / f"{name}.html").read_text(encoding="utf-8")
    for token, value in tokens.items():
        content = content.replace(f"__{token.upper()}__", value)

    layout = (TEMPLATES_DIR / "layout.html").read_text(encoding="utf-8")
    page = (
        layout.replace("__TITLE__", heading)
        .replace("__HEADING__", heading)
        .replace("__SUBTITLE__", subtitle)
        .replace("__NAV__", _nav(name, show_notes=device_has_rag(request)))
        .replace("__LOGOUT__", '<a class="logout" href="/logout">Salir</a>' if auth_enabled() else "")
        .replace("__CONTENT__", content)
    )
    return HTMLResponse(content=page)


@router.get("/", include_in_schema=False)
@router.get("/dashboard", include_in_schema=False)
def home(request: Request):
    return RedirectResponse("/personality" if not device_has_rag(request) else "/notes")


@router.get("/notes", response_class=HTMLResponse)
def notes_page(request: Request):
    # El equipo elegido no usa notas/RAG: no hay nada que hacer acá.
    if not device_has_rag(request):
        return RedirectResponse("/personality", status_code=303)
    return render_page(
        "notes",
        "Notas de reunión",
        "Subí el texto de una reunión para indexarlo y consultarlo después por voz.",
        request,
    )


@router.get("/personality", response_class=HTMLResponse)
def personality_page(request: Request):
    return render_page(
        "personality",
        "Personalidad",
        "Cómo habla el asistente de este equipo y si usa notas/RAG.",
        request,
        # Va dentro de un <script>, donde las entidades HTML no se decodifican.
        default_personality=json.dumps(DEFAULT_PERSONALITY),
    )


@router.get("/device", response_class=HTMLResponse)
def device_page(request: Request):
    return render_page(
        "device",
        "Dispositivo",
        "Luces, pantalla e imagen de reposo del ESP32. Todo se guarda y el dispositivo lo aplica solo.",
        request,
    )


@router.get("/firmware", response_class=HTMLResponse)
def firmware_page(request: Request):
    return render_page(
        "firmware",
        "Firmware",
        "Cada push a main se compila en GitHub y llega al ESP32 por OTA. Acá ves el estado y podés forzarlo.",
        request,
    )
