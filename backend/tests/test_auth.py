"""Tests de la autenticación del panel.

Lo que más importa acá: que el ESP32 siga entrando desde la LAN sin tocar el
firmware, y que nada quede abierto por omisión.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth, config, device, pages

PASSWORD = "clave-de-prueba"
LAN_CLIENT = ("192.168.1.50", 51000)
TUNNEL_CLIENT = ("127.0.0.1", 51000)
REMOTE_CLIENT = ("203.0.113.9", 51000)


def build_app() -> FastAPI:
    application = FastAPI()
    application.include_router(pages.router)
    application.include_router(device.router)
    auth.install_auth(application)
    return application


def client_from(source=None) -> TestClient:
    kwargs = {"client": source} if source else {}
    return TestClient(build_app(), **kwargs)


@pytest.fixture
def secured(monkeypatch, data_dir):
    monkeypatch.setattr(config, "PANEL_PASSWORD", PASSWORD)
    monkeypatch.setattr(config, "DEVICE_TOKEN", "")
    monkeypatch.setattr(config, "TRUSTED_NETWORKS", "192.168.0.0/16,10.0.0.0/8")
    auth._login_attempts.clear()


def login(test_client: TestClient, password: str = PASSWORD):
    return test_client.post(
        "/login", data={"password": password, "next": "/device"}, follow_redirects=False
    )


# --------------------------------------------------------------------------- #
# Sin password configurada
# --------------------------------------------------------------------------- #


def test_without_password_everything_stays_open(monkeypatch, data_dir):
    monkeypatch.setattr(config, "PANEL_PASSWORD", "")
    with client_from() as test_client:
        assert test_client.get("/device").status_code == 200
        assert test_client.get("/device/config").status_code == 200


# --------------------------------------------------------------------------- #
# Panel
# --------------------------------------------------------------------------- #


def test_pages_redirect_to_login(secured):
    with client_from() as test_client:
        response = test_client.get(
            "/device", headers={"accept": "text/html"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/device"


def test_api_calls_get_401_instead_of_a_redirect(secured):
    with client_from() as test_client:
        response = test_client.post("/device/config", json={"rgb_enabled": False})
        assert response.status_code == 401


def test_login_grants_access_and_redirects_back(secured):
    with client_from() as test_client:
        response = login(test_client)
        assert response.status_code == 303
        assert response.headers["location"] == "/device"
        assert auth.COOKIE_NAME in response.cookies

        assert test_client.get("/device").status_code == 200
        assert test_client.post("/device/config", json={"rgb_enabled": False}).status_code == 200


def test_wrong_password_is_rejected(secured):
    with client_from() as test_client:
        response = login(test_client, "otra-cosa")
        assert response.status_code == 200
        assert "Password incorrecta" in response.text
        assert auth.COOKIE_NAME not in response.cookies


def test_logout_clears_the_session(secured):
    with client_from() as test_client:
        login(test_client)
        assert test_client.get("/device").status_code == 200

        test_client.get("/logout", follow_redirects=False)
        response = test_client.get("/device", headers={"accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 303


def test_open_redirects_are_not_allowed(secured):
    with client_from() as test_client:
        response = test_client.post(
            "/login",
            data={"password": PASSWORD, "next": "//evil.example.com/"},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/notes"


def test_changing_the_password_invalidates_sessions(secured, monkeypatch):
    token = auth.issue_session()
    assert auth.valid_session(token)

    monkeypatch.setattr(config, "PANEL_PASSWORD", "otra-password")
    assert not auth.valid_session(token)


def test_brute_force_is_rate_limited(secured):
    with client_from(REMOTE_CLIENT) as test_client:
        for _ in range(auth.MAX_ATTEMPTS):
            login(test_client, "mal")
        response = login(test_client, "mal")
        assert "Demasiados intentos" in response.text

        # Ni siquiera la password correcta entra mientras dure el bloqueo.
        assert "Demasiados intentos" in login(test_client).text


# --------------------------------------------------------------------------- #
# ESP32
# --------------------------------------------------------------------------- #


def test_device_endpoints_work_from_the_lan_without_a_session(secured):
    with client_from(LAN_CLIENT) as test_client:
        assert test_client.get("/device/config").status_code == 200
        assert test_client.get("/ota/manifest").status_code == 200


def test_device_endpoints_are_closed_from_outside(secured):
    with client_from(REMOTE_CLIENT) as test_client:
        assert test_client.get("/device/config").status_code == 401
        assert test_client.get("/ota/manifest").status_code == 401


def test_loopback_is_not_trusted(secured):
    """Si mañana termina un túnel en 127.0.0.1, no debe saltear el login."""
    with client_from(TUNNEL_CLIENT) as test_client:
        assert test_client.get("/device/config").status_code == 401


def test_device_token_works_from_anywhere(secured, monkeypatch):
    monkeypatch.setattr(config, "DEVICE_TOKEN", "token-del-esp32")
    with client_from(REMOTE_CLIENT) as test_client:
        assert test_client.get("/device/config").status_code == 401
        response = test_client.get(
            "/device/config", headers={"X-Device-Token": "token-del-esp32"}
        )
        assert response.status_code == 200


def test_the_lan_cannot_publish_firmware(secured):
    """Estar en la red no alcanza para flashear el dispositivo."""
    with client_from(LAN_CLIENT) as test_client:
        response = test_client.post(
            "/ota/firmware",
            files={"file": ("firmware.bin", b"\xe9malicioso", "application/octet-stream")},
            data={"version": "9.9.9", "build": "999"},
        )
        assert response.status_code == 401
        assert test_client.get("/ota/manifest").json()["available"] is False


# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #


def test_unknown_routes_require_a_session_by_default(secured):
    """Un endpoint nuevo queda protegido sin que haya que acordarse de listarlo."""
    application = build_app()

    @application.get("/algo-nuevo")
    def nuevo():
        return {"ok": True}

    with TestClient(application, client=LAN_CLIENT) as test_client:
        assert test_client.get("/algo-nuevo").status_code == 401


def test_health_is_public(secured):
    with client_from(REMOTE_CLIENT) as test_client:
        response = test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "auth": True}


def test_every_device_route_exists_in_the_app(secured):
    """Evita que una ruta listada en DEVICE_ROUTES quede desalineada del router.

    Se mira el esquema en vez de llamar los endpoints porque varios devuelven
    404 de forma legítima cuando todavía no hay nada publicado.
    """
    schema = build_app().openapi()["paths"]
    for method, path in auth.DEVICE_ROUTES:
        if path == "/voice-assistant":
            continue  # vive en main.py, que necesita las claves de IA
        assert path in schema, f"{path} no está registrada"
        assert method.lower() in schema[path], f"{method} {path} no está registrada"
