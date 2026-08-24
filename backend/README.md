# Desktop Co-Pilot Backend

API FastAPI para procesar notas de reuniones, consultar memoria RAG, guardar tareas en Notion, responder por voz y controlar el ESP32 (luces, imagen de pantalla y actualizaciones OTA).

## Estructura

```text
backend/
├── app/
│   ├── assets/default_images/  # Láminas extraídas de Imagenes.pdf (240x240)
│   ├── auth.py            # Login del panel y control de acceso
│   ├── config.py          # Variables de entorno y configuración
│   ├── device.py          # Config del dispositivo, imágenes y OTA
│   ├── ota_sync.py        # Espejado del firmware desde releases de GitHub
│   ├── pages.py           # Layout, navegación y páginas del panel
│   ├── integrations.py    # Gemini, Groq, Notion, ChromaDB y TTS
│   ├── main.py            # Aplicación y endpoints HTTP
│   ├── models.py          # Schemas Pydantic
│   ├── services.py        # Procesamiento en segundo plano
│   └── templates/         # layout.html + una plantilla por sección
├── chroma_db/             # Datos persistidos de ChromaDB
├── data/                  # Estado del dispositivo y firmware OTA (no versionado)
├── scripts/               # Utilidades (extracción de imágenes del PDF)
├── tests/                 # Tests de páginas, dispositivo y sync de firmware
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

El panel queda en `http://127.0.0.1:8000/` y se divide en cuatro secciones, con una
barra de navegación compartida:

| Ruta | Sección |
|---|---|
| `/notes` | Subir notas de reunión |
| `/personality` | Personalidad del asistente |
| `/device` | Luces, pantalla e imagen de reposo |
| `/firmware` | Publicar firmware para OTA |

`/dashboard` sigue funcionando y redirige a `/notes`.

## Autenticación

Con `PANEL_PASSWORD` seteada, todo pide sesión salvo lo que esté explícitamente
permitido. El criterio es fail-closed: un endpoint nuevo queda protegido sin que
haya que acordarse de listarlo.

El ESP32 no maneja cookies, así que los endpoints que consume
(`/voice-assistant`, `/device/config`, `/device/image`, `/ota/manifest`,
`/ota/download`) se aceptan desde una red confiable o con `X-Device-Token`.
**Loopback no es confiable a propósito**: si un túnel termina en `127.0.0.1`,
no debe saltear el login.

| Variable | Default | Para qué |
|---|---|---|
| `PANEL_PASSWORD` | — | Password del panel. Vacía deja todo abierto. |
| `SESSION_HOURS` | `720` | Duración de la sesión (30 días). |
| `DEVICE_TOKEN` | — | Sólo si el ESP32 deja de estar en la misma LAN. |
| `TRUSTED_NETWORKS` | rangos privados | Desde dónde entra el ESP32 sin sesión. |

Cambiar `PANEL_PASSWORD` invalida todas las sesiones: la clave de firma se
deriva de la password, no se guarda nada en disco.

## Tests

```powershell
pip install -r requirements-dev.txt
pytest tests
```

Los tests montan sólo `app.device`, `app.pages` y `app.ota_sync` en una app mínima, así que
corren sin claves de API. La API de GitHub va mockeada con `httpx.MockTransport`: no hay red.

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

## Firmware automático

Al levantar, el backend arranca una tarea que consulta los releases del repo cada
`FIRMWARE_SYNC_INTERVAL_SECONDS` (5 min por defecto). Cuando encuentra un release
`fw-*` con un build mayor al publicado, descarga `firmware.bin`, verifica el
SHA-256 contra el `manifest.json` del release y lo instala en `data/firmware/`.
Desde ahí el ESP32 lo toma por el flujo OTA de siempre.

Variables en `.env`:

| Variable | Default | Para qué |
|---|---|---|
| `FIRMWARE_REPO` | `genti91/desktop-copilot` | Repo del que se bajan los releases |
| `FIRMWARE_AUTO_SYNC` | `1` | `0` desactiva el chequeo periódico |
| `FIRMWARE_SYNC_INTERVAL_SECONDS` | `300` | Cada cuánto consulta GitHub |
| `GITHUB_TOKEN` | — | Sólo para repos privados o límites de rate |
| `FIRMWARE_RELEASE_PREFIX` | `fw-` | Qué tags cuentan como firmware |
