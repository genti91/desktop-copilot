import hashlib
from io import BytesIO

from PIL import Image

from app import device


def png_bytes(color=(255, 0, 0), size=(240, 240)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Conversión de imágenes
# --------------------------------------------------------------------------- #


def test_rgb565_conversion_is_little_endian():
    raw = device.to_rgb565(Image.new("RGB", (240, 240), (255, 0, 0)))
    assert len(raw) == device.RAW_IMAGE_BYTES == 240 * 240 * 2
    # Rojo puro -> 0xF800; el ESP32 lee uint16 little-endian.
    assert raw[0:2] == b"\x00\xf8"

    blue = device.to_rgb565(Image.new("RGB", (240, 240), (0, 0, 255)))
    assert blue[0:2] == b"\x1f\x00"


def test_non_square_images_are_letterboxed_without_distortion():
    fitted = device._fit_to_screen(Image.new("RGB", (480, 240), (12, 34, 56)))
    assert fitted.size == (240, 240)
    # El relleno usa el color de fondo original, no negro fijo.
    assert fitted.getpixel((0, 0)) == (12, 34, 56)
    assert device._fit_to_screen(Image.new("RGB", (100, 700), (0, 0, 0))).size == (240, 240)


def test_default_images_from_pdf_are_available_and_convertible():
    defaults = sorted(device.DEFAULT_IMAGES_DIR.glob("*.png"))
    assert defaults, "faltan las imágenes extraídas de Imagenes.pdf"
    for path in defaults:
        with Image.open(path) as image:
            assert image.size == (240, 240)


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #


def test_default_config_payload_shape(client):
    payload = client.get("/device/config").json()
    assert payload["revision"] == 1
    assert payload["rgb"] == {"enabled": True, "r": 255, "g": 42, "b": 0, "brightness": 70}
    assert payload["filament"]["enabled"] is True
    assert payload["display"]["enabled"] is True
    assert payload["image"] is None


def test_config_update_persists_and_bumps_revision(client, data_dir):
    response = client.post(
        "/device/config",
        json={"rgb_color": "#00ff80", "rgb_brightness": 120, "filament_enabled": False},
    )
    assert response.status_code == 200
    assert response.json()["rgb_color"] == "#00FF80"
    assert response.json()["revision"] == 2

    assert (data_dir / "device_config.json").exists()
    payload = client.get("/device/config").json()
    assert payload["rgb"] == {"enabled": True, "r": 0, "g": 255, "b": 128, "brightness": 120}
    assert payload["filament"]["enabled"] is False

    # Cada cambio incrementa la revisión: es lo que dispara el refresco en el ESP32.
    assert client.post("/device/config", json={"display_enabled": False}).json()["revision"] == 3


def test_outputs_toggle_independently(client):
    client.post(
        "/device/config",
        json={"rgb_enabled": False, "filament_enabled": True, "display_enabled": False},
    )
    payload = client.get("/device/config").json()
    assert payload["rgb"]["enabled"] is False
    assert payload["filament"]["enabled"] is True
    assert payload["display"]["enabled"] is False


def test_invalid_color_is_rejected(client):
    assert client.post("/device/config", json={"rgb_color": "rojo"}).status_code == 400
    assert client.post("/device/config", json={"rgb_brightness": 999}).status_code == 422


def test_unknown_image_selection_is_rejected(client):
    assert client.post("/device/config", json={"image_id": "no-existe"}).status_code == 404
    assert client.post("/device/config", json={"image_id": "../../etc/passwd"}).status_code == 400


# --------------------------------------------------------------------------- #
# Imágenes
# --------------------------------------------------------------------------- #


def test_catalog_lists_defaults(client):
    images = client.get("/device/images").json()
    assert images
    assert all(image["source"] == "default" for image in images)
    first = images[0]
    assert first["preview_url"] == f"/device/images/{first['id']}/preview.png"
    assert len(first["checksum"]) == 16


def test_selecting_an_image_exposes_it_to_the_device(client):
    image_id = client.get("/device/images").json()[0]["id"]
    assert client.post("/device/config", json={"image_id": image_id}).status_code == 200

    payload = client.get("/device/config").json()
    assert payload["image"]["id"] == image_id
    assert payload["image"]["bytes"] == device.RAW_IMAGE_BYTES
    assert payload["image"]["url"] == "/device/image"

    raw = client.get("/device/image")
    assert raw.status_code == 200
    assert len(raw.content) == device.RAW_IMAGE_BYTES
    assert raw.headers["x-image-checksum"] == payload["image"]["checksum"]
    assert hashlib.sha256(raw.content).hexdigest()[:16] == payload["image"]["checksum"]


def test_clearing_the_image_restores_the_animated_face(client):
    image_id = client.get("/device/images").json()[0]["id"]
    client.post("/device/config", json={"image_id": image_id})

    assert client.post("/device/config", json={"clear_image": True}).json()["image_id"] is None
    assert client.get("/device/config").json()["image"] is None
    assert client.get("/device/image").status_code == 404


def test_upload_convert_select_and_delete_roundtrip(client):
    response = client.post(
        "/device/images",
        files={"file": ("mi foto.jpg", png_bytes((0, 128, 255), (600, 400)), "image/png")},
    )
    assert response.status_code == 200
    uploaded = response.json()
    assert uploaded["id"] == "up-mi-foto"
    assert uploaded["source"] == "upload"

    assert client.get(uploaded["preview_url"]).headers["content-type"] == "image/png"
    assert len(client.get(uploaded["raw_url"]).content) == device.RAW_IMAGE_BYTES

    client.post("/device/config", json={"image_id": uploaded["id"]})
    assert client.get("/device/config").json()["image"]["id"] == uploaded["id"]

    assert client.delete(f"/device/images/{uploaded['id']}").status_code == 200
    # Borrar la imagen activa vuelve a la cara animada en vez de dejar una referencia rota.
    assert client.get("/device/config").json()["image"] is None
    assert client.get(uploaded["raw_url"]).status_code == 404


def test_duplicate_upload_names_do_not_collide(client):
    first = client.post("/device/images", files={"file": ("logo.png", png_bytes(), "image/png")})
    second = client.post("/device/images", files={"file": ("logo.png", png_bytes(), "image/png")})
    assert first.json()["id"] == "up-logo"
    assert second.json()["id"] == "up-logo-2"


def test_default_images_cannot_be_deleted(client):
    image_id = client.get("/device/images").json()[0]["id"]
    assert client.delete(f"/device/images/{image_id}").status_code == 403


def test_broken_upload_is_rejected(client):
    response = client.post(
        "/device/images", files={"file": ("nope.png", b"esto no es una imagen", "image/png")}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# OTA
# --------------------------------------------------------------------------- #


def fake_firmware(payload: bytes = b"contenido del firmware") -> bytes:
    return b"\xe9" + payload


def test_manifest_reports_nothing_published_by_default(client):
    manifest = client.get("/ota/manifest").json()
    assert manifest["available"] is False
    assert manifest["build"] == 0
    assert client.get("/ota/download").status_code == 404


def test_publish_download_and_unpublish_firmware(client):
    binary = fake_firmware()
    response = client.post(
        "/ota/firmware",
        files={"file": ("firmware.bin", binary, "application/octet-stream")},
        data={"version": "1.0.1", "build": "2", "notes": "OTA + imágenes"},
    )
    assert response.status_code == 200
    manifest = response.json()
    assert manifest == {
        "available": True,
        "version": "1.0.1",
        "build": 2,
        "url": "/ota/download",
        "sha256": hashlib.sha256(binary).hexdigest(),
        "size": len(binary),
        "notes": "OTA + imágenes",
        "uploaded_at": manifest["uploaded_at"],
    }

    assert client.get("/ota/manifest").json() == manifest

    download = client.get("/ota/download")
    assert download.content == binary
    assert download.headers["x-firmware-build"] == "2"
    assert download.headers["x-firmware-sha256"] == manifest["sha256"]

    assert client.delete("/ota/firmware").status_code == 200
    assert client.get("/ota/manifest").json()["available"] is False


def test_non_esp32_binaries_are_rejected(client):
    response = client.post(
        "/ota/firmware",
        files={"file": ("firmware.bin", b"PK\x03\x04zip", "application/octet-stream")},
        data={"version": "1.0.1", "build": "2"},
    )
    assert response.status_code == 422
    assert client.get("/ota/manifest").json()["available"] is False


def test_build_must_be_positive(client):
    response = client.post(
        "/ota/firmware",
        files={"file": ("firmware.bin", fake_firmware(), "application/octet-stream")},
        data={"version": "1.0.1", "build": "0"},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Página web
# --------------------------------------------------------------------------- #


def test_device_page_renders(client):
    response = client.get("/device")
    assert response.status_code == 200
    assert "Configuración del Dispositivo" in response.text
    assert "/ota/firmware" in response.text
