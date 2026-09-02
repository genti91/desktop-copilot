"""Fixtures para los tests del router de dispositivo.

El router de `app.device` no toca Gemini/Groq, así que se monta en una app
mínima: los tests corren sin claves de API ni base vectorial.
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app import device  # noqa: E402


@pytest.fixture
def anyio_backend():
    """anyio corre los tests async; acá sólo nos interesa asyncio."""
    return "asyncio"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Redirige el almacenamiento del dispositivo a un directorio temporal."""
    uploads = tmp_path / "images"
    firmware = tmp_path / "firmware"
    uploads.mkdir()
    firmware.mkdir()

    monkeypatch.setattr(device, "DATA_DIR", tmp_path)
    monkeypatch.setattr(device, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(device, "FIRMWARE_DIR", firmware)
    monkeypatch.setattr(device, "CONFIG_PATH", tmp_path / "device_config.json")
    monkeypatch.setattr(device, "FIRMWARE_BINARY", firmware / "firmware.bin")
    monkeypatch.setattr(device, "MANIFEST_PATH", firmware / "manifest.json")
    device._raw_cache.clear()
    return tmp_path


@pytest.fixture
def client(data_dir):
    application = FastAPI()
    application.include_router(device.router)
    with TestClient(application) as test_client:
        yield test_client
