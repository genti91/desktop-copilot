"""Autenticación del panel con una sola password y sesión por cookie firmada.

El criterio es fail-closed: todo pide sesión salvo lo que esté explícitamente
listado. Así, un endpoint nuevo queda protegido por omisión en vez de quedar
abierto por olvido.

El ESP32 no maneja cookies, así que los endpoints que consume se resuelven
aparte: se aceptan desde una red confiable (la LAN) o con `X-Device-Token`.
Ojo que loopback NO es confiable a propósito: si mañana termina un túnel en
127.0.0.1, no queremos que eso saltee el login.
"""

import hashlib
import hmac
import ipaddress
import secrets
import time
from typing import Optional

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import config

COOKIE_NAME = "copilot_session"
LOGIN_PATH = "/login"

# Rutas que consume el ESP32: sin cookie, pero sólo desde red confiable o con token.
DEVICE_ROUTES = frozenset(
    {
        ("POST", "/voice-assistant"),
        ("GET", "/device/config"),
        ("GET", "/device/image"),
        ("GET", "/ota/manifest"),
        ("GET", "/ota/download"),
    }
)

# Rutas que tienen que responder sin ninguna credencial.
PUBLIC_ROUTES = frozenset({("GET", LOGIN_PATH), ("POST", LOGIN_PATH), ("GET", "/health")})

router = APIRouter(tags=["auth"])

_login_attempts: dict[str, tuple[int, float]] = {}
MAX_ATTEMPTS = 8
ATTEMPT_WINDOW_SECONDS = 300


# --------------------------------------------------------------------------- #
# Sesión
# --------------------------------------------------------------------------- #


def auth_enabled() -> bool:
    return bool(config.PANEL_PASSWORD)


def _signing_key() -> bytes:
    """Se deriva de la password: cambiarla invalida todas las sesiones."""
    return hashlib.sha256(f"desktop-copilot::{config.PANEL_PASSWORD}".encode()).digest()


def issue_session() -> str:
    expiry = int(time.time()) + config.SESSION_HOURS * 3600
    signature = hmac.new(_signing_key(), str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{signature}"


def valid_session(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    expiry, _, signature = token.partition(".")
    if not expiry.isdigit() or int(expiry) < time.time():
        return False
    expected = hmac.new(_signing_key(), expiry.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# --------------------------------------------------------------------------- #
# Origen de la petición
# --------------------------------------------------------------------------- #


def _trusted_networks() -> list[ipaddress._BaseNetwork]:
    networks = []
    for entry in config.TRUSTED_NETWORKS.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            print(f"[Auth] Red confiable inválida, la ignoro: {entry}")
    return networks


def from_trusted_network(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    try:
        address = ipaddress.ip_address(client.host)
    except ValueError:
        return False
    return any(address in network for network in _trusted_networks())


def _device_authorized(request: Request) -> bool:
    if config.DEVICE_TOKEN:
        header = request.headers.get("x-device-token", "")
        if header and hmac.compare_digest(header, config.DEVICE_TOKEN):
            return True
    return from_trusted_network(request)


def is_authorized(request: Request) -> bool:
    method, path = request.method, request.url.path
    if (method, path) in PUBLIC_ROUTES:
        return True
    if valid_session(request.cookies.get(COOKIE_NAME)):
        return True
    return (method, path) in DEVICE_ROUTES and _device_authorized(request)


# --------------------------------------------------------------------------- #
# Middleware
# --------------------------------------------------------------------------- #


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


async def auth_middleware(request: Request, call_next):
    if not auth_enabled() or is_authorized(request):
        return await call_next(request)

    if _wants_html(request):
        destination = request.url.path
        if request.url.query:
            destination += f"?{request.url.query}"
        return RedirectResponse(f"{LOGIN_PATH}?next={destination}", status_code=303)
    return Response(status_code=401, content='{"detail":"No autorizado."}', media_type="application/json")


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


def _rate_limited(client_key: str) -> bool:
    attempts, first_attempt = _login_attempts.get(client_key, (0, 0.0))
    if time.time() - first_attempt > ATTEMPT_WINDOW_SECONDS:
        return False
    return attempts >= MAX_ATTEMPTS


def _record_failure(client_key: str) -> None:
    attempts, first_attempt = _login_attempts.get(client_key, (0, 0.0))
    if time.time() - first_attempt > ATTEMPT_WINDOW_SECONDS:
        attempts, first_attempt = 0, time.time()
    _login_attempts[client_key] = (attempts + 1, first_attempt)


def _safe_next(destination: str) -> str:
    """Sólo rutas internas: evita que ?next= mande a otro sitio."""
    if destination.startswith("/") and not destination.startswith("//"):
        return destination
    return "/notes"


def _render_login(message: str = "", destination: str = "/notes") -> HTMLResponse:
    from .pages import TEMPLATES_DIR  # import diferido: pages importa config, no auth

    template = (TEMPLATES_DIR / "login.html").read_text(encoding="utf-8")
    return HTMLResponse(
        content=template.replace("__MESSAGE__", message).replace("__NEXT__", destination)
    )


@router.get(LOGIN_PATH, response_class=HTMLResponse)
def login_page(request: Request, next: str = "/notes"):
    if not auth_enabled() or valid_session(request.cookies.get(COOKIE_NAME)):
        return RedirectResponse(_safe_next(next), status_code=303)
    return _render_login(destination=_safe_next(next))


@router.post(LOGIN_PATH)
def login(request: Request, password: str = Form(""), next: str = Form("/notes")):
    destination = _safe_next(next)
    client_key = request.client.host if request.client else "desconocido"

    if _rate_limited(client_key):
        return _render_login("Demasiados intentos. Esperá unos minutos.", destination)

    if not secrets.compare_digest(password, config.PANEL_PASSWORD):
        _record_failure(client_key)
        return _render_login("Password incorrecta.", destination)

    _login_attempts.pop(client_key, None)
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        issue_session(),
        max_age=config.SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
        # El túnel termina en HTTPS pero el backend habla HTTP en la LAN, así que
        # marcar la cookie como secure la rompería para el acceso local.
        secure=False,
    )
    return response


@router.post("/logout")
@router.get("/logout")
def logout():
    response = RedirectResponse(LOGIN_PATH, status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/health")
def health():
    from .config import GEMINI_API_KEY

    return {"status": "ok", "auth": auth_enabled(), "ai": bool(GEMINI_API_KEY)}


def install_auth(app: FastAPI) -> None:
    app.include_router(router)
    app.middleware("http")(auth_middleware)
    if not auth_enabled():
        print("⚠️  [Auth] PANEL_PASSWORD vacía: el panel queda abierto a quien alcance el backend.")
