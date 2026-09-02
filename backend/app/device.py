"""Configuración remota del dispositivo (LEDs, pantalla, imágenes) y OTA.

Todo lo que expone este router es autónomo respecto de las integraciones de IA:
sólo depende de Pillow y del disco, así que puede testearse sin claves de API.
"""

import hashlib
import json
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError

from .call import incoming_call_for
from .config import BASE_DIR
from .models import DeviceConfig, DeviceConfigUpdate, DeviceImage, FirmwareManifest

SCREEN_SIZE = 240
RAW_IMAGE_BYTES = SCREEN_SIZE * SCREEN_SIZE * 2
MAX_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_FIRMWARE_BYTES = 4 * 1024 * 1024
RAW_CACHE_LIMIT = 32
IMAGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")
HEX_COLOR_PATTERN = re.compile(r"^#?[0-9a-fA-F]{6}$")

DEFAULT_IMAGES_DIR = BASE_DIR / "app" / "assets" / "default_images"
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "images"
FIRMWARE_DIR = DATA_DIR / "firmware"
CONFIG_PATH = DATA_DIR / "device_config.json"
FIRMWARE_BINARY = FIRMWARE_DIR / "firmware.bin"
MANIFEST_PATH = FIRMWARE_DIR / "manifest.json"

router = APIRouter(tags=["device"])

_raw_cache: dict[tuple[str, int, int], bytes] = {}


# --------------------------------------------------------------------------- #
# Persistencia de la configuración
# --------------------------------------------------------------------------- #


def _ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_config() -> DeviceConfig:
    if not CONFIG_PATH.exists():
        return DeviceConfig(updated_at=_now())
    try:
        return DeviceConfig(**json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception as error:
        print(f"[Device Config Load Error]: {error}")
        return DeviceConfig(updated_at=_now())


def save_config(config: DeviceConfig) -> DeviceConfig:
    _ensure_dirs()
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(CONFIG_PATH)
    return config


def _bump(config: DeviceConfig, **changes) -> DeviceConfig:
    return config.model_copy(
        update={**changes, "revision": config.revision + 1, "updated_at": _now()}
    )


# --------------------------------------------------------------------------- #
# Catálogo de imágenes
# --------------------------------------------------------------------------- #


def _validate_image_id(image_id: str) -> str:
    if not IMAGE_ID_PATTERN.match(image_id or ""):
        raise HTTPException(status_code=400, detail="Identificador de imagen inválido.")
    return image_id


def _image_path(image_id: str) -> Optional[Path]:
    _validate_image_id(image_id)
    for directory in (DEFAULT_IMAGES_DIR, UPLOADS_DIR):
        candidate = directory / f"{image_id}.png"
        if candidate.is_file():
            return candidate
    return None


def _require_image_path(image_id: str) -> Path:
    path = _image_path(image_id)
    if path is None:
        raise HTTPException(status_code=404, detail="La imagen no existe.")
    return path


def _humanize(image_id: str) -> str:
    label = re.sub(r"-+", " ", re.sub(r"^(\d+|up)-", "", image_id)).strip()
    return label[:1].upper() + label[1:] if label else image_id


def _slugify(raw: str) -> str:
    normalized = raw.lower()
    for accented, plain in zip("áéíóúüñ", "aeiouun"):
        normalized = normalized.replace(accented, plain)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug[:48] or "imagen"


def _unique_upload_id(raw: str) -> str:
    base = f"up-{_slugify(raw)}"
    candidate = base
    suffix = 2
    while _image_path(candidate) is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _describe(image_id: str, source: str) -> DeviceImage:
    return DeviceImage(
        id=image_id,
        label=_humanize(image_id),
        source=source,
        preview_url=f"/device/images/{image_id}/preview.png",
        raw_url=f"/device/images/{image_id}/raw",
        checksum=image_checksum(image_id),
    )


def list_images() -> list[DeviceImage]:
    images: list[DeviceImage] = []
    for source, directory in (("default", DEFAULT_IMAGES_DIR), ("upload", UPLOADS_DIR)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.png")):
            if IMAGE_ID_PATTERN.match(path.stem):
                images.append(_describe(path.stem, source))
    return images


def _fit_to_screen(image: Image.Image) -> Image.Image:
    """Encaja cualquier imagen en 240x240 sin deformarla, rellenando con su fondo."""
    image = image.convert("RGB")
    if image.size == (SCREEN_SIZE, SCREEN_SIZE):
        return image
    background = image.getpixel((0, 0))
    fitted = image.copy()
    fitted.thumbnail((SCREEN_SIZE, SCREEN_SIZE), Image.LANCZOS)
    canvas = Image.new("RGB", (SCREEN_SIZE, SCREEN_SIZE), background)
    canvas.paste(fitted, ((SCREEN_SIZE - fitted.width) // 2, (SCREEN_SIZE - fitted.height) // 2))
    return canvas


def to_rgb565(image: Image.Image) -> bytes:
    """Convierte a RGB565 little-endian, el orden que espera TFT_eSPI con swapBytes."""
    pixels = _fit_to_screen(image).tobytes()
    buffer = bytearray(RAW_IMAGE_BYTES)
    for index in range(SCREEN_SIZE * SCREEN_SIZE):
        red, green, blue = pixels[index * 3], pixels[index * 3 + 1], pixels[index * 3 + 2]
        value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        buffer[index * 2] = value & 0xFF
        buffer[index * 2 + 1] = value >> 8
    return bytes(buffer)


def image_raw_bytes(image_id: str) -> bytes:
    path = _require_image_path(image_id)
    stats = path.stat()
    key = (str(path), stats.st_size, stats.st_mtime_ns)
    cached = _raw_cache.get(key)
    if cached is not None:
        return cached
    try:
        with Image.open(path) as image:
            raw = to_rgb565(image)
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(status_code=422, detail="La imagen está corrupta.") from error
    if len(_raw_cache) >= RAW_CACHE_LIMIT:
        _raw_cache.clear()
    _raw_cache[key] = raw
    return raw


def image_checksum(image_id: str) -> str:
    return hashlib.sha256(image_raw_bytes(image_id)).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Manifest OTA
# --------------------------------------------------------------------------- #


def load_manifest() -> FirmwareManifest:
    if not (MANIFEST_PATH.exists() and FIRMWARE_BINARY.exists()):
        return FirmwareManifest()
    try:
        return FirmwareManifest(**json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    except Exception as error:
        print(f"[OTA Manifest Load Error]: {error}")
        return FirmwareManifest()


# --------------------------------------------------------------------------- #
# Endpoints: configuración
# --------------------------------------------------------------------------- #


def _color_channels(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def device_state_payload(config: DeviceConfig, incoming_from: Optional[str] = None) -> dict:
    """Vista compacta que consume el ESP32 en cada poll."""
    red, green, blue = _color_channels(config.rgb_color)
    image = None
    if config.image_id and _image_path(config.image_id) is not None:
        image = {
            "id": config.image_id,
            "checksum": image_checksum(config.image_id),
            "bytes": RAW_IMAGE_BYTES,
            "url": "/device/image",
        }
    payload = {
        "revision": config.revision,
        "rgb": {
            "enabled": config.rgb_enabled,
            "r": red,
            "g": green,
            "b": blue,
            "brightness": config.rgb_brightness,
        },
        "filament": {"enabled": config.filament_enabled},
        "display": {"enabled": config.display_enabled},
        "image": image,
    }
    if incoming_from:
        payload["incoming_call"] = {"from": incoming_from}
    return payload


@router.get("/device/config")
def get_device_config(request: Request):
    device_name = (request.headers.get("X-Device-Name") or "").strip().lower()
    return device_state_payload(load_config(), incoming_call_for(device_name))


@router.get("/device/config/full", response_model=DeviceConfig)
def get_device_config_full():
    return load_config()


@router.post("/device/config", response_model=DeviceConfig)
def update_device_config(payload: DeviceConfigUpdate):
    config = load_config()
    changes = payload.model_dump(exclude_unset=True)
    changes.pop("clear_image", None)

    if changes.get("rgb_color") is not None:
        if not HEX_COLOR_PATTERN.match(changes["rgb_color"]):
            raise HTTPException(status_code=400, detail="El color debe ser hexadecimal #RRGGBB.")
        changes["rgb_color"] = "#" + changes["rgb_color"].lstrip("#").upper()

    if payload.clear_image:
        changes["image_id"] = None
    elif changes.get("image_id"):
        _require_image_path(changes["image_id"])

    applied = {
        key: value
        for key, value in changes.items()
        if value is not None or key == "image_id"
    }
    return save_config(_bump(config, **applied))


# --------------------------------------------------------------------------- #
# Endpoints: imágenes
# --------------------------------------------------------------------------- #


@router.get("/device/images", response_model=list[DeviceImage])
def get_device_images():
    return list_images()


@router.get("/device/images/{image_id}/preview.png")
def get_image_preview(image_id: str):
    path = _require_image_path(image_id)
    return Response(
        content=path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/device/images/{image_id}/raw")
def get_image_raw(image_id: str):
    raw = image_raw_bytes(image_id)
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={
            "X-Image-Checksum": image_checksum(image_id),
            "X-Image-Size": str(SCREEN_SIZE),
        },
    )


@router.get("/device/image")
def get_active_image_raw():
    config = load_config()
    if not config.image_id or _image_path(config.image_id) is None:
        raise HTTPException(status_code=404, detail="No hay imagen seleccionada.")
    return get_image_raw(config.image_id)


@router.post("/device/images", response_model=DeviceImage)
async def upload_device_image(file: UploadFile = File(...), label: str = Form("")):
    _ensure_dirs()
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    if len(content) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="La imagen supera los 8 MB.")

    try:
        with Image.open(BytesIO(content)) as image:
            image.load()
            normalized = _fit_to_screen(image)
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(status_code=422, detail="Formato de imagen no reconocido.") from error

    image_id = _unique_upload_id(label or Path(file.filename or "imagen").stem)
    normalized.save(UPLOADS_DIR / f"{image_id}.png", format="PNG", optimize=True)
    return _describe(image_id, "upload")


@router.delete("/device/images/{image_id}")
def delete_device_image(image_id: str):
    _validate_image_id(image_id)
    path = UPLOADS_DIR / f"{image_id}.png"
    if not path.is_file():
        if (DEFAULT_IMAGES_DIR / f"{image_id}.png").is_file():
            raise HTTPException(status_code=403, detail="Las imágenes predeterminadas no se borran.")
        raise HTTPException(status_code=404, detail="La imagen no existe.")

    path.unlink()
    config = load_config()
    if config.image_id == image_id:
        save_config(_bump(config, image_id=None))
    return {"status": "deleted", "id": image_id}


# --------------------------------------------------------------------------- #
# Endpoints: OTA
# --------------------------------------------------------------------------- #


@router.get("/ota/manifest", response_model=FirmwareManifest)
def get_ota_manifest():
    return load_manifest()


@router.get("/ota/download")
def download_firmware():
    manifest = load_manifest()
    if not manifest.available or not FIRMWARE_BINARY.is_file():
        raise HTTPException(status_code=404, detail="No hay firmware publicado.")
    return Response(
        content=FIRMWARE_BINARY.read_bytes(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": 'attachment; filename="firmware.bin"',
            "X-Firmware-Version": manifest.version,
            "X-Firmware-Build": str(manifest.build),
            "X-Firmware-Sha256": manifest.sha256 or "",
        },
    )


@router.post("/ota/firmware", response_model=FirmwareManifest)
async def publish_firmware(
    file: UploadFile = File(...),
    version: str = Form(...),
    build: int = Form(...),
    notes: str = Form(""),
):
    _ensure_dirs()
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="El firmware está vacío.")
    if len(content) > MAX_FIRMWARE_BYTES:
        raise HTTPException(status_code=413, detail="El firmware supera los 4 MB.")
    if build <= 0:
        raise HTTPException(status_code=400, detail="El build debe ser un entero positivo.")
    if content[0] != 0xE9:
        raise HTTPException(status_code=422, detail="El archivo no parece un binario de ESP32.")

    FIRMWARE_BINARY.write_bytes(content)
    manifest = FirmwareManifest(
        available=True,
        version=version.strip() or "0.0.0",
        build=build,
        url="/ota/download",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        notes=notes.strip(),
        uploaded_at=_now(),
    )
    MANIFEST_PATH.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


@router.delete("/ota/firmware")
def unpublish_firmware():
    FIRMWARE_BINARY.unlink(missing_ok=True)
    MANIFEST_PATH.unlink(missing_ok=True)
    return {"status": "deleted"}
