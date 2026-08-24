"""Tests del espejado de firmware desde GitHub, con la API mockeada."""

import hashlib
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, device, ota_sync

BINARY = b"\xe9" + b"firmware compilado por CI" * 40
DIGEST = hashlib.sha256(BINARY).hexdigest()
ASSET_BASE = "https://api.github.com/repos/genti91/desktop-copilot/releases/assets"


def manifest_asset(build: int, version: str = "1.2.0", sha256: str | None = DIGEST) -> bytes:
    return json.dumps(
        {
            "available": True,
            "version": version,
            "build": build,
            "url": "/ota/download",
            "sha256": sha256,
            "size": len(BINARY),
            "notes": "publicado por Actions",
            "uploaded_at": "2026-08-21T12:00:00",
        }
    ).encode()


def release(tag: str, *, draft: bool = False, assets: tuple[str, ...] = ("firmware.bin", "manifest.json")) -> dict:
    return {
        "tag_name": tag,
        "draft": draft,
        "assets": [{"name": name, "url": f"{ASSET_BASE}/{tag}/{name}"} for name in assets],
    }


def github(releases: list[dict], *, binary: bytes = BINARY, manifest: bytes | None = None):
    """Transporte falso que sirve el listado de releases y sus assets."""
    payload = manifest if manifest is not None else manifest_asset(9)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/releases"):
            return httpx.Response(200, json=releases)
        if request.url.path.endswith("manifest.json"):
            return httpx.Response(200, content=payload)
        if request.url.path.endswith("firmware.bin"):
            return httpx.Response(200, content=binary)
        return httpx.Response(404)

    return httpx.MockTransport(handler), calls


async def run_sync(releases, force=False, **kwargs):
    transport, calls = github(releases, **kwargs)
    async with httpx.AsyncClient(transport=transport) as client:
        status = await ota_sync.sync_once(force=force, client=client)
    return status, calls


@pytest.fixture
def sync_client(data_dir):
    application = FastAPI()
    application.include_router(device.router)
    application.include_router(ota_sync.router)
    with TestClient(application) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# Selección del release
# --------------------------------------------------------------------------- #


def test_picks_the_newest_complete_firmware_release():
    picked = ota_sync.pick_release(
        [
            release("v2.0.0-app"),                                  # no es de firmware
            release("fw-1.3.0-9", draft=True),                      # borrador
            release("fw-1.2.0-8", assets=("firmware.bin",)),        # le falta el manifest
            release("fw-1.1.0-7"),                                  # este sí
            release("fw-1.0.0-6"),
        ]
    )
    assert picked["tag_name"] == "fw-1.1.0-7"


def test_no_release_yet_is_not_an_error():
    assert ota_sync.pick_release([release("v2.0.0-app")]) is None


# --------------------------------------------------------------------------- #
# Sincronización
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_installs_the_release_and_serves_it_to_the_device(data_dir, sync_client):
    status, _ = await run_sync([release("fw-1.2.0-9")])

    assert status.error == ""
    assert "instalado 1.2.0 (build 9)" in status.outcome
    assert status.last_release == "fw-1.2.0-9"

    # A partir de acá el ESP32 lo ve como cualquier firmware publicado a mano.
    manifest = sync_client.get("/ota/manifest").json()
    assert manifest["available"] is True
    assert manifest["build"] == 9
    assert manifest["url"] == "/ota/download"
    assert manifest["sha256"] == DIGEST
    assert sync_client.get("/ota/download").content == BINARY


@pytest.mark.anyio
async def test_skips_when_already_up_to_date(data_dir, sync_client):
    await run_sync([release("fw-1.2.0-9")])
    status, calls = await run_sync([release("fw-1.2.0-9")])

    assert status.outcome == "al día en build 9"
    # No se vuelve a bajar el binario, sólo el listado y el manifest.
    assert not any(url.endswith("firmware.bin") for url in calls)


@pytest.mark.anyio
async def test_force_reinstalls_the_same_build(data_dir, sync_client):
    await run_sync([release("fw-1.2.0-9")])
    status, calls = await run_sync([release("fw-1.2.0-9")], force=True)

    assert "instalado" in status.outcome
    assert any(url.endswith("firmware.bin") for url in calls)


@pytest.mark.anyio
async def test_older_build_does_not_downgrade(data_dir, sync_client):
    await run_sync([release("fw-1.2.0-9")])
    status, _ = await run_sync([release("fw-1.1.0-4")], manifest=manifest_asset(4))

    assert status.outcome == "al día en build 9"
    assert sync_client.get("/ota/manifest").json()["build"] == 9


@pytest.mark.anyio
async def test_checksum_mismatch_leaves_the_previous_firmware(data_dir, sync_client):
    await run_sync([release("fw-1.2.0-9")])
    status, _ = await run_sync(
        [release("fw-1.3.0-10")],
        binary=b"\xe9otra-cosa",
        manifest=manifest_asset(10, version="1.3.0"),
    )

    assert status.outcome == "falló la sincronización"
    assert "SHA-256" in status.error
    assert sync_client.get("/ota/manifest").json()["build"] == 9
    assert sync_client.get("/ota/download").content == BINARY


@pytest.mark.anyio
async def test_non_esp32_asset_is_rejected(data_dir):
    status, _ = await run_sync(
        [release("fw-1.2.0-9")],
        binary=b"PK\x03\x04",
        manifest=manifest_asset(9, sha256=None),
    )
    assert "ESP32" in status.error


@pytest.mark.anyio
async def test_github_failure_is_recorded_not_raised(data_dir):
    transport = httpx.MockTransport(lambda request: httpx.Response(403, json={"message": "rate limit"}))
    async with httpx.AsyncClient(transport=transport) as client:
        status = await ota_sync.sync_once(client=client)

    assert status.outcome == "falló la sincronización"
    assert "403" in status.error


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


def test_status_endpoint_reflects_the_environment(data_dir, sync_client):
    status = sync_client.get("/ota/sync").json()
    assert status["repo"] == config.FIRMWARE_REPO
    assert status["enabled"] == config.FIRMWARE_AUTO_SYNC
    assert status["outcome"] == "nunca se sincronizó"


@pytest.mark.anyio
async def test_status_persists_between_runs(data_dir, sync_client):
    await run_sync([release("fw-1.2.0-9")])
    assert (data_dir / "firmware" / "sync.json").exists()

    status = sync_client.get("/ota/sync").json()
    assert status["last_release"] == "fw-1.2.0-9"
    assert "instalado" in status["outcome"]
