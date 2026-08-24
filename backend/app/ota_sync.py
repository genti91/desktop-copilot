"""Espejado automático del firmware publicado en los releases de GitHub.

GitHub Actions no puede alcanzar este backend (vive en la LAN), así que el
sentido del flujo se invierte: CI compila y publica un release, y el backend lo
va a buscar cada tantos minutos. El ESP32 sigue pidiéndole el firmware al
backend igual que siempre, sin saber que GitHub existe.
"""

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config, device
from .models import FirmwareManifest

GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 30
REQUIRED_ASSETS = ("firmware.bin", "manifest.json")

router = APIRouter(tags=["ota"])


class SyncStatus(BaseModel):
    enabled: bool = True
    repo: str = ""
    interval_seconds: int = 300
    last_checked: str = ""
    last_release: str = ""
    outcome: str = "nunca se sincronizó"
    error: str = ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _status_path() -> Path:
    return device.FIRMWARE_DIR / "sync.json"


def load_status() -> SyncStatus:
    stored: dict = {}
    path = _status_path()
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            print(f"[OTA Sync Status Error]: {error}")
    # El entorno manda sobre lo que haya quedado en disco.
    stored.update(
        enabled=config.FIRMWARE_AUTO_SYNC,
        repo=config.FIRMWARE_REPO,
        interval_seconds=config.FIRMWARE_SYNC_INTERVAL_SECONDS,
    )
    return SyncStatus(**stored)


def _save_status(status: SyncStatus) -> SyncStatus:
    device.FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    _status_path().write_text(status.model_dump_json(indent=2), encoding="utf-8")
    return status


def _headers(accept: str) -> dict[str, str]:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return headers


def pick_release(releases: list[dict]) -> Optional[dict]:
    """El release más nuevo de firmware que traiga los dos assets que hacen falta."""
    for release in releases:
        tag = str(release.get("tag_name", ""))
        if release.get("draft") or not tag.startswith(config.FIRMWARE_RELEASE_PREFIX):
            continue
        names = {asset.get("name") for asset in release.get("assets", [])}
        if all(required in names for required in REQUIRED_ASSETS):
            return release
    return None


def _asset(release: dict, name: str) -> dict:
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            return asset
    raise ValueError(f"El release {release.get('tag_name')} no tiene {name}.")


async def _download_asset(client: httpx.AsyncClient, asset: dict) -> bytes:
    # La URL de la API sirve tanto para repos públicos como privados.
    response = await client.get(
        asset["url"],
        headers=_headers("application/octet-stream"),
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.content


def _install(binary: bytes, manifest: FirmwareManifest) -> None:
    device.FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = device.FIRMWARE_BINARY.with_suffix(".tmp")
    temporary.write_bytes(binary)
    temporary.replace(device.FIRMWARE_BINARY)
    device.MANIFEST_PATH.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


async def sync_once(
    force: bool = False, client: Optional[httpx.AsyncClient] = None
) -> SyncStatus:
    """Trae el último firmware de GitHub si es más nuevo que el publicado acá."""
    status = load_status()
    status.last_checked = _now()
    status.error = ""

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)

    try:
        response = await client.get(
            f"{GITHUB_API}/repos/{config.FIRMWARE_REPO}/releases",
            headers=_headers("application/vnd.github+json"),
            params={"per_page": 10},
        )
        response.raise_for_status()

        release = pick_release(response.json())
        if release is None:
            status.outcome = "todavía no hay ningún release de firmware"
            return _save_status(status)

        status.last_release = release["tag_name"]
        remote = FirmwareManifest(
            **json.loads(await _download_asset(client, _asset(release, "manifest.json")))
        )
        installed = device.load_manifest()

        if not force and installed.available and remote.build <= installed.build:
            status.outcome = f"al día en build {installed.build}"
            return _save_status(status)

        binary = await _download_asset(client, _asset(release, "firmware.bin"))
        if len(binary) > device.MAX_FIRMWARE_BYTES:
            raise ValueError("El binario del release supera los 4 MB.")
        if not binary or binary[0] != 0xE9:
            raise ValueError("El asset no parece un binario de ESP32.")

        digest = hashlib.sha256(binary).hexdigest()
        if remote.sha256 and digest != remote.sha256:
            raise ValueError("El SHA-256 del binario no coincide con el manifest.")

        _install(
            binary,
            remote.model_copy(
                update={
                    "available": True,
                    "url": "/ota/download",
                    "sha256": digest,
                    "size": len(binary),
                    "uploaded_at": _now(),
                }
            ),
        )
        status.outcome = (
            f"instalado {remote.version} (build {remote.build}) desde {release['tag_name']}"
        )
    except Exception as error:
        status.outcome = "falló la sincronización"
        status.error = f"{type(error).__name__}: {error}"
        print(f"[OTA Sync Error]: {status.error}")
    finally:
        if owns_client:
            await client.aclose()

    return _save_status(status)


async def background_sync_loop() -> None:
    """Chequea GitHub periódicamente mientras el backend esté levantado."""
    while True:
        await sync_once()
        await asyncio.sleep(config.FIRMWARE_SYNC_INTERVAL_SECONDS)


@router.get("/ota/sync", response_model=SyncStatus)
def get_sync_status():
    return load_status()


@router.post("/ota/sync", response_model=SyncStatus)
async def trigger_sync(force: bool = False):
    if not config.FIRMWARE_REPO:
        raise HTTPException(status_code=400, detail="No hay FIRMWARE_REPO configurado.")
    return await sync_once(force=force)
