"""Genera el manifest del firmware compilado y expone los datos al workflow.

Se ejecuta en CI despues de `pio run`. Escribe `manifest.json` junto al binario
y publica `tag`, `version` y `build` en $GITHUB_OUTPUT para que el paso de
release los use.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent.parent
VERSION_HEADER = REPO_DIR / "firmware" / "include" / "version.h"
FIRMWARE_BINARY = REPO_DIR / "firmware" / ".pio" / "build" / "xiao_esp32s3" / "firmware.bin"
MANIFEST_PATH = REPO_DIR / "firmware" / ".pio" / "build" / "xiao_esp32s3" / "manifest.json"
RELEASE_PREFIX = "fw-"


def read_version() -> str:
    match = re.search(
        r'#define\s+FIRMWARE_VERSION\s+"([^"]+)"',
        VERSION_HEADER.read_text(encoding="utf-8"),
    )
    if not match:
        sys.exit(f"No encontre FIRMWARE_VERSION en {VERSION_HEADER}")
    return match.group(1)


def commit_count() -> int:
    """Mismo numero que inyecta firmware/scripts/build_number.py al compilar."""
    output = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_DIR,
    )
    return int(output.stdout.strip())


def main() -> int:
    if not FIRMWARE_BINARY.is_file():
        sys.exit(f"No existe el binario compilado en {FIRMWARE_BINARY}")

    binary = FIRMWARE_BINARY.read_bytes()
    if binary[0] != 0xE9:
        sys.exit("El binario no arranca con el magic byte 0xE9 de ESP32.")

    version = read_version()
    build = commit_count()
    manifest = {
        "available": True,
        "version": version,
        "build": build,
        "url": "/ota/download",
        "sha256": hashlib.sha256(binary).hexdigest(),
        "size": len(binary),
        "notes": os.getenv("FIRMWARE_NOTES", ""),
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps(manifest, indent=2))

    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"tag={RELEASE_PREFIX}{version}-{build}\n")
            handle.write(f"version={version}\n")
            handle.write(f"build={build}\n")
            handle.write(f"size={len(binary)}\n")
            handle.write(f"sha256={manifest['sha256']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
