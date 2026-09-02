"""El backend tiene que levantar aunque falten las claves de IA.

`genai.Client(api_key=None)` lanza al construirse, asi que una key vacia se
llevaba puesto el import de toda la app: no arrancaba ni el panel, ni la
configuracion del dispositivo, ni el OTA, que no usan Gemini para nada.
Se verifica en un subproceso porque es un comportamiento de import.
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

BOOT_SCRIPT = """
from fastapi.testclient import TestClient
from app.main import app
from app.integrations import gemini

assert gemini is None, "sin key, el cliente de Gemini deberia quedar en None"
with TestClient(app) as client:
    health = client.get("/health").json()
    assert health["status"] == "ok", health
    assert health["ai"] is False, health
    assert client.get("/device/config").status_code == 200
    assert client.get("/ota/manifest").status_code == 200
    assert client.get("/notes").status_code == 200
print("OK")
"""


def run_without_ai_keys(script: str) -> subprocess.CompletedProcess:
    environment = {
        "PATH": "/usr/bin:/bin",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        # El backend loguea con emojis; sin esto el subproceso muere en consolas cp1252.
        "PYTHONIOENCODING": "utf-8",
        "GEMINI_API_KEY": "",
        "GROQ_API_KEY": "",
        "NOTION_API_KEY": "",
        "PANEL_PASSWORD": "",
        "FIRMWARE_AUTO_SYNC": "0",
        "CALL_RELAY_ENABLED": "0",
    }
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        # El hijo escribe UTF-8; sin esto el padre lo decodifica con el locale.
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_backend_boots_without_gemini_key():
    result = run_without_ai_keys(BOOT_SCRIPT)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "OK" in result.stdout


def test_voice_endpoint_answers_out_loud_when_gemini_is_missing():
    """El ESP32 reproduce lo que reciba: un JSON de error sonaria a ruido."""
    script = """
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    response = client.post(
        "/voice-assistant",
        files={"file": ("audio.wav", b"RIFFfake", "audio/wav")},
        data={"session_id": "test"},
    )
    assert response.status_code == 200, response.status_code
    assert response.headers["content-type"] == "audio/mpeg", response.headers
    assert response.headers["x-action"] == "NONE"
print("OK")
"""
    result = run_without_ai_keys(script)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_process_notes_returns_a_clear_error_when_gemini_is_missing():
    script = """
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    response = client.post("/process-notes", json={"notes_text": "algo"})
    assert response.status_code == 503, response.status_code
    assert "GEMINI_API_KEY" in response.json()["detail"]
print("OK")
"""
    result = run_without_ai_keys(script)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


# --------------------------------------------------------------------------- #
# Function calling manual en /voice-assistant
# --------------------------------------------------------------------------- #

# Gemini va mockeado: se comprueba el pegado entre lo que devuelve el modelo y
# la cabecera X-Action / el audio, no la IA. Corre en subproceso como el resto
# del archivo para no arrastrar el import de app.main al proceso de tests.
FAKE_GEMINI_HARNESS = """
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as main


class FakePart:
    def __init__(self, text=None, function_call=None):
        self.text = text
        self.function_call = function_call


class FakeCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class FakeResult:
    def __init__(self, parts):
        content = type("C", (), {"parts": parts})()
        self.candidates = [type("Cand", (), {"content": content})()]


class FakeGemini:
    def __init__(self, parts):
        result = FakeResult(parts)
        self.models = type("M", (), {"generate_content": lambda self, **kw: result})()


async def fake_tts(text):
    return b"ID3" + text.encode("utf-8", "replace")


def run(parts):
    with patch.object(main, "gemini", FakeGemini(parts)), \
         patch.object(main, "generate_speech_bytes", fake_tts):
        client = TestClient(main.app)
        return client.post(
            "/voice-assistant",
            files={"file": ("a.wav", b"RIFFfake", "audio/wav")},
            data={"session_id": "t"},
        )
"""


def test_voice_function_call_becomes_x_action_and_keeps_spoken_text():
    script = FAKE_GEMINI_HARNESS + """
parts = [
    FakePart(text="Dale, lo pongo en rojo."),
    FakePart(function_call=FakeCall("controlar_asistente_escritorio", {"color_rgb": "255,0,0"})),
]
response = run(parts)
assert response.status_code == 200, response.status_code
assert response.headers["x-action"] == "LED_RGB:255,0,0", response.headers["x-action"]
assert response.content.startswith(b"ID3"), response.content[:8]
assert "rojo" in response.content.decode("utf-8", "replace").lower()
print("OK")
"""
    result = run_without_ai_keys(script)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_voice_function_call_without_model_text_speaks_a_default_confirmation():
    script = FAKE_GEMINI_HARNESS + """
parts = [FakePart(function_call=FakeCall("controlar_asistente_escritorio", {"apagar_todo": True}))]
response = run(parts)
assert response.status_code == 200, response.status_code
assert response.headers["x-action"] == "ALL_OFF", response.headers["x-action"]
# Sin texto del modelo, se habla una confirmación armada a partir de la acción.
assert "apagu" in response.content.decode("utf-8", "replace").lower()
print("OK")
"""
    result = run_without_ai_keys(script)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_voice_plain_answer_sends_no_action():
    script = FAKE_GEMINI_HARNESS + """
response = run([FakePart(text="Tu próxima entrega es el viernes.")])
assert response.status_code == 200, response.status_code
assert response.headers["x-action"] == "NONE", response.headers["x-action"]
print("OK")
"""
    result = run_without_ai_keys(script)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
