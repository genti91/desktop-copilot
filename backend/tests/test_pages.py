import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import pages
from app.state import DEFAULT_PERSONALITY, state_memory

PAGES = {
    "/notes": "Notas de reunión",
    "/personality": "Personalidad",
    "/device": "Dispositivo",
    "/firmware": "Firmware",
}


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


def test_legacy_dashboard_and_root_redirect_to_notes(client):
    for path in ("/", "/dashboard"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (307, 308)
        assert response.headers["location"] == "/notes"


def test_personality_page_shows_current_value_escaped(client):
    state_memory["assistant_personality"] = 'Sos <b>directo</b> & "conciso"'
    try:
        body = client.get("/personality").text
        assert "Sos &lt;b&gt;directo&lt;/b&gt; &amp; &quot;conciso&quot;" in body
        assert "<b>directo</b>" not in body
    finally:
        state_memory["assistant_personality"] = DEFAULT_PERSONALITY


def test_personality_default_is_injected_as_a_js_literal(client):
    body = client.get("/personality").text
    match = re.search(r"const DEFAULT_PERSONALITY = (.+);", body)
    assert match
    # json.dumps produce un literal JS válido, no entidades HTML.
    assert json.loads(match.group(1)) == DEFAULT_PERSONALITY


def test_sections_only_carry_their_own_controls(client):
    notes = client.get("/notes").text
    personality = client.get("/personality").text
    device = client.get("/device").text
    firmware = client.get("/firmware").text

    assert "/process-notes" in notes and "/update-personality" not in notes
    assert "/update-personality" in personality and "/process-notes" not in personality
    assert "/device/config" in device and "/ota/firmware" not in device
    assert "/ota/firmware" in firmware and "/device/config" not in firmware


def test_no_placeholder_tokens_survive_rendering(client):
    for path in PAGES:
        assert not re.search(r"__[A-Z_]+__", client.get(path).text)
