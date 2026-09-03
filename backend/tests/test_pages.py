import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import device, pages
from app.state import DEFAULT_PERSONALITY

PAGES = {
    "/notes": "Notas de reunión",
    "/personality": "Personalidad",
    "/device": "Dispositivo",
    "/firmware": "Firmware",
}


@pytest.fixture(autouse=True)
def _isolate_device_profiles(tmp_path, monkeypatch):
    """El nav server-side lee el perfil del equipo; que no toque data/ real."""
    devices = tmp_path / "devices"
    devices.mkdir()
    monkeypatch.setattr(device, "DEVICES_DIR", devices)
    monkeypatch.setattr(device, "KNOWN_PATH", devices / "known.json")
    monkeypatch.setattr(device, "LEGACY_CONFIG_PATH", tmp_path / "device_config.json")
    monkeypatch.setattr(device.config, "RAG_DISABLED_DEVICES", ["franco"])


@pytest.fixture
def client():
    application = FastAPI()
    application.include_router(pages.router)
    with TestClient(application) as test_client:
        yield test_client


@pytest.mark.parametrize("path, heading", PAGES.items())
def test_every_section_renders_with_its_heading(client, path, heading):
    response = client.get(path)
    assert response.status_code == 200
    assert f"<h1>{heading}</h1>" in response.text


@pytest.mark.parametrize("path", PAGES)
def test_navigation_is_shared_and_marks_the_current_section(client, path):
    body = client.get(path).text
    for url in PAGES:
        assert f'href="{url}"' in body

    active = re.findall(r'<a class="nav-link is-active" aria-current="page" href="([^"]+)"', body)
    assert active == [path]


def test_root_and_dashboard_redirect_to_the_first_useful_section(client):
    for path in ("/", "/dashboard"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (307, 308)
        assert response.headers["location"] == "/notes"


def test_who_am_i_picker_lives_in_the_shared_layout(client):
    body = client.get("/firmware").text
    assert 'id="whoChip"' in body
    assert 'id="whoOverlay"' in body
    assert "/devices" in body


def test_a_device_without_rag_loses_the_notes_section(client):
    client.cookies.set("copilot_device", "franco")

    personality = client.get("/personality").text
    assert 'href="/notes"' not in personality
    assert 'href="/personality"' in personality

    assert client.get("/notes", follow_redirects=False).headers["location"] == "/personality"
    assert client.get("/", follow_redirects=False).headers["location"] == "/personality"


def test_personality_page_loads_the_profile_by_fetch(client):
    body = client.get("/personality").text
    # La personalidad ya no se renderiza server-side: se trae el perfil del
    # equipo elegido por fetch, con el toggle de notas/RAG.
    assert "/device/config/full?device=" in body
    assert "/update-personality" in body
    assert 'id="ragEnabled"' in body
    assert 'id="lampsEnabled"' in body
    assert "window.copilotDevice()" in body


def test_personality_default_is_injected_as_a_js_literal(client):
    body = client.get("/personality").text
    match = re.search(r"const DEFAULT_PERSONALITY = (.+);", body)
    assert match
    # json.dumps produce un literal JS válido, no entidades HTML.
    assert json.loads(match.group(1)) == DEFAULT_PERSONALITY


def test_sections_only_carry_their_own_controls(client):
    notes = client.get("/notes").text
    personality = client.get("/personality").text
    device_page = client.get("/device").text
    firmware = client.get("/firmware").text

    assert "/process-notes" in notes and "/update-personality" not in notes
    assert "/update-personality" in personality and "/process-notes" not in personality
    assert "/device/config" in device_page and "/ota/firmware" not in device_page
    assert "/ota/firmware" in firmware and "/device/config" not in firmware


def test_no_placeholder_tokens_survive_rendering(client):
    for path in PAGES:
        assert not re.search(r"__[A-Z_]+__", client.get(path).text)
