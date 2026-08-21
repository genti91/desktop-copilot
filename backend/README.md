# Desktop Co-Pilot Backend

API FastAPI para procesar notas de reuniones, consultar memoria RAG, guardar tareas en Notion, responder por voz y controlar el ESP32 (luces, imagen de pantalla y actualizaciones OTA).

## Estructura

```text
backend/
├── app/
│   ├── assets/default_images/  # Láminas extraídas de Imagenes.pdf (240x240)
│   ├── config.py          # Variables de entorno y configuración
│   ├── device.py          # Config del dispositivo, imágenes y OTA
│   ├── integrations.py    # Gemini, Groq, Notion, ChromaDB y TTS
│   ├── main.py            # Aplicación y endpoints HTTP
│   ├── models.py          # Schemas Pydantic
│   ├── services.py        # Procesamiento en segundo plano
│   └── templates/         # Dashboard e interfaz del dispositivo
├── chroma_db/             # Datos persistidos de ChromaDB
├── data/                  # Estado del dispositivo y firmware OTA (no versionado)
├── scripts/               # Utilidades (extracción de imágenes del PDF)
├── tests/                 # Tests del router de dispositivo
├── .env                  # Secretos locales, no versionar
├── .env.example          # Plantilla de configuración
├── main.py               # Entry point compatible con Uvicorn
└── requirements.txt
```

## Ejecutar

Desde `backend/`, con el entorno virtual activado:

```powershell
pip install -r requirements.txt
uvicorn main:app --reload
```

El dashboard queda disponible en `http://127.0.0.1:8000/dashboard`.

Además del dashboard, `http://127.0.0.1:8000/device` expone la configuración del ESP32.

## Tests

```powershell
pip install -r requirements-dev.txt
pytest tests
```

Los tests montan sólo `app.device` en una app mínima, así que corren sin claves de API.

## Imágenes predeterminadas

El catálogo de imágenes sale de `Imagenes.pdf` (raíz del repo) y ya está versionado en
`app/assets/default_images/`. Si el PDF cambia, se regenera con:

```powershell
python scripts/extract_default_images.py
```

El script necesita PyMuPDF (`pip install pymupdf`) o el binario `pdftoppm` de Poppler
para rasterizar; Pillow ya viene con las dependencias del backend.

## Almacenamiento del dispositivo

Todo lo configurable vive en `data/` y sobrevive reinicios del backend:

- `data/device_config.json` — colores, encendidos e imagen elegida (con `revision`).
- `data/images/` — imágenes subidas desde la web, normalizadas a 240x240 PNG.
- `data/firmware/firmware.bin` + `manifest.json` — firmware publicado para OTA.
