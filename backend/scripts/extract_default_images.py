"""Regenera el catálogo de imágenes predeterminadas a partir de `Imagenes.pdf`.

Cada página del PDF se convierte en un PNG cuadrado de 240x240 (el tamaño exacto
de la pantalla TFT) dentro de `app/assets/default_images/`.

Uso:
    python scripts/extract_default_images.py [ruta/al/Imagenes.pdf]

Requiere Pillow (ya es dependencia del backend) y, para rasterizar el PDF,
PyMuPDF (`pip install pymupdf`) o el binario `pdftoppm` de Poppler.
Los PNG resultantes se versionan en el repo, así que este script sólo hace falta
cuando cambia el PDF original.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
OUTPUT_DIR = BACKEND_DIR / "app" / "assets" / "default_images"
TARGET_SIZE = 240
RENDER_DPI = 200

# El PDF no trae metadatos de página, así que el nombre visible de cada lámina
# se mapea por número de página (1-indexado).
PAGE_LABELS = {
    1: "Poopie sesh",
    2: "En call, ya vuelvo",
    3: "Llamada",
    4: "Hello",
    5: "Lunes",
    6: "Por dormir",
    7: "Temple time",
    8: "Viernes",
    9: "Crazy pampers",
    10: "Confundido",
    11: "Nube",
    12: "Ranitas",
}


def find_pdf(explicit: str | None) -> Path | None:
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    for candidate in (
        REPO_DIR / "Imagenes.pdf",
        REPO_DIR / "images" / "Imagenes.pdf",
        BACKEND_DIR / "Imagenes.pdf",
    ):
        if candidate.is_file():
            return candidate
    return None


def render_pages(pdf_path: Path, output_dir: Path) -> list[Path]:
    try:
        import fitz  # type: ignore  # PyMuPDF

        rendered = []
        with fitz.open(pdf_path) as document:
            for index, page in enumerate(document, start=1):
                pixmap = page.get_pixmap(dpi=RENDER_DPI)
                target = output_dir / f"page-{index:02d}.png"
                pixmap.save(target)
                rendered.append(target)
        return rendered
    except ImportError:
        pass

    if not shutil.which("pdftoppm"):
        raise SystemExit(
            "No encontré cómo rasterizar el PDF. Instalá PyMuPDF (`pip install pymupdf`) "
            "o Poppler (provee `pdftoppm`)."
        )

    subprocess.run(
        ["pdftoppm", "-png", "-r", str(RENDER_DPI), str(pdf_path), str(output_dir / "page")],
        check=True,
    )
    return sorted(output_dir.glob("page-*.png"))


def to_square(image: Image.Image) -> Image.Image:
    """Encaja la lámina apaisada en un cuadrado usando su propio color de fondo."""
    image = image.convert("RGB")
    background = image.getpixel((0, 0))
    image.thumbnail((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)
    canvas = Image.new("RGB", (TARGET_SIZE, TARGET_SIZE), background)
    canvas.paste(image, ((TARGET_SIZE - image.width) // 2, (TARGET_SIZE - image.height) // 2))
    return canvas


def slugify(label: str) -> str:
    normalized = label.lower()
    for accented, plain in zip("áéíóúüñ", "aeiouun"):
        normalized = normalized.replace(accented, plain)
    return "".join(character if character.isalnum() else "-" for character in normalized).strip("-")


def main() -> int:
    pdf_path = find_pdf(sys.argv[1] if len(sys.argv) > 1 else None)
    if pdf_path is None:
        print("⚠️  No encontré Imagenes.pdf; no hay nada que extraer.")
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUTPUT_DIR.glob("*.png"):
        stale.unlink()

    with tempfile.TemporaryDirectory() as workspace:
        pages = render_pages(pdf_path, Path(workspace))
        for index, page_path in enumerate(pages, start=1):
            label = PAGE_LABELS.get(index, f"Lamina {index}")
            target = OUTPUT_DIR / f"{index:02d}-{slugify(label)}.png"
            with Image.open(page_path) as page_image:
                to_square(page_image).save(target, format="PNG", optimize=True)
            print(f"✅ {target.name}  ({label})")

    print(f"\n{len(pages)} imágenes generadas en {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
