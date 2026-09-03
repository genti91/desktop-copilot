"""Páginas web del panel.

Cada sección es una página propia que comparte el layout y la barra de
navegación de `templates/layout.html`. El render es sustitución de tokens
`__NOMBRE__`, igual que hacía el dashboard original, para no sumar un motor
de plantillas.
"""

import json

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from .auth import auth_enabled
from .config import BASE_DIR
from .state import DEFAULT_PERSONALITY

TEMPLATES_DIR = BASE_DIR / "app" / "templates"

NAV_ITEMS = (
    ("notes", "/notes", "Notas"),
    ("personality", "/personality", "Personalidad"),
    ("device", "/device", "Dispositivo"),
    ("firmware", "/firmware", "Firmware"),
)

router = APIRouter(tags=["pages"])


def _nav(active: str) -> str:
    links = []
    for key, url, label in NAV_ITEMS:
        classes = "nav-link is-active" if key == active else "nav-link"
        current = ' aria-current="page"' if key == active else ""
        links.append(f'<a class="{classes}"{current} href="{url}">{label}</a>')
    return "".join(links)


def render_page(name: str, heading: str, subtitle: str, **tokens) -> HTMLResponse:
    content = (TEMPLATES_DIR / f"{name}.html").read_text(encoding="utf-8")
    for token, value in tokens.items():
        content = content.replace(f"__{token.upper()}__", value)

    layout = (TEMPLATES_DIR / "layout.html").read_text(encoding="utf-8")
    page = (
        layout.replace("__TITLE__", heading)
        .replace("__HEADING__", heading)
        .replace("__SUBTITLE__", subtitle)
        .replace("__NAV__", _nav(name))
        .replace("__LOGOUT__", '<a class="logout" href="/logout">Salir</a>' if auth_enabled() else "")
        .replace("__CONTENT__", content)
    )
    return HTMLResponse(content=page)


@router.get("/", include_in_schema=False)
@router.get("/dashboard", include_in_schema=False)
def home():
    return RedirectResponse("/notes")


@router.get("/notes", response_class=HTMLResponse)
def notes_page():
    return render_page(
        "notes",
        "Notas de reunión",
        "Subí el texto de una reunión para indexarlo y consultarlo después por voz.",
    )


@router.get("/personality", response_class=HTMLResponse)
def personality_page():
    return render_page(
        "personality",
        "Personalidad",
        "Cómo habla el asistente de cada equipo y si usa notas/RAG.",
        # Va dentro de un <script>, donde las entidades HTML no se decodifican.
        default_personality=json.dumps(DEFAULT_PERSONALITY),
    )


@router.get("/device", response_class=HTMLResponse)
def device_page():
    return render_page(
        "device",
        "Dispositivo",
        "Luces, pantalla e imagen de reposo del ESP32. Todo se guarda y el dispositivo lo aplica solo.",
    )


@router.get("/firmware", response_class=HTMLResponse)
def firmware_page():
    return render_page(
        "firmware",
        "Firmware",
        "Cada push a main se compila en GitHub y llega al ESP32 por OTA. Acá ves el estado y podés forzarlo.",
    )
