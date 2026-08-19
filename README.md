# Desktop Co-Pilot

Asistente de escritorio con IA + hardware (ESP32-S3) para:

- Cargar notas de reuniones y guardarlas como memoria consultable (RAG).
- Hacer preguntas sobre esas notas en lenguaje natural.
- Hablar con el asistente por voz.
- Controlar luces del hardware desde comandos generados por la IA.

Proyecto pensado para uso diario de trabajo: capturas notas, las dejas indexadas, y despues consultas contexto cuando lo necesites.

## Foto del hardware


```md
![Desktop Co-Pilot](https://raw.githubusercontent.com/genti91/desktop-copilot/refs/heads/main/images/IMG_6478.gif)
```

## Que hace hoy

- Backend FastAPI para:
  - Procesar notas de reuniones (`/process-notes`).
  - Recibir audio del ESP32 y responder con audio (`/voice-assistant`).
  - Recuperar contexto desde ChromaDB (RAG) para responder preguntas.
  - Guardar tareas/notas en Notion.
- Firmware para ESP32-S3 (Seeed XIAO ESP32S3) que:
  - Se conecta por Wi-Fi (WiFiManager + portal cautivo).
  - Graba audio mientras mantenes presionado el touch sensor.
  - Envia audio al backend.
  - Reproduce la respuesta TTS del asistente.
  - Ejecuta comandos de luces recibidos en el header HTTP `X-Action`.

## Estado actual

- Grabacion activada por touch sensor (mantener presionado).
- Control de luces funcionando por comandos:
  - `LED_RGB:R,G,B`
  - `LED_BRIGHTNESS:V`
  - `FILAMENT_ON`
  - `FILAMENT_OFF`
- Trabajo en progreso: keyword spotting para iniciar grabacion sin tocar boton/sensor.

## Arquitectura

```text
ESP32 (mic + speaker + LEDs)
  -> envia WAV por HTTP multipart
FastAPI (/voice-assistant)
  -> STT (Groq Whisper)
  -> RAG (Gemini Embeddings + ChromaDB)
  -> respuesta IA (Gemini)
  -> TTS (edge-tts)
ESP32
  <- recibe MP3 + X-Action y ejecuta luces
```

## Estructura del repo

```text
desktop-copilot/
|- backend/
|  |- app/
|  |- chroma_db/
|  |- requirements.txt
|  \- .env.example
\- firmware/
   |- include/
   |- src/
   \- platformio.ini
```

## Requisitos

### Backend

- Python 3.10+
- API key de Gemini (obligatoria)
- API key de Groq (recomendada para STT)
- Notion API + Database ID

### Firmware

- PlatformIO
- Placa objetivo: `seeed_xiao_esp32s3`
- Librerias (definidas en `platformio.ini`):
  - `ESP8266Audio`
  - `Adafruit NeoPixel`
  - `TFT_eSPI`
  - `WiFiManager`

## Setup rapido

### 1) Backend

Desde `backend/`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Completa `backend/.env`:

```env
GEMINI_API_KEY=
GROQ_API_KEY=
NOTION_API_KEY=
NOTION_DATABASE_ID=
# CHROMA_PATH=./chroma_db
```

Levantar servidor:

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Dashboard:

- `http://127.0.0.1:8000/dashboard`

### 2) Firmware

Desde `firmware/`:

```powershell
pio run -e xiao_esp32s3
pio run -e xiao_esp32s3 -t upload
pio device monitor -b 115200
```

En el primer arranque:

- El ESP32 abre portal Wi-Fi (`ESP32_Asistente`) si no tiene red guardada.
- Configura SSID/password y la URL del backend (ejemplo: `http://192.168.100.99:8000/voice-assistant`).
- La URL queda persistida en LittleFS (`/config.txt`).

## Uso

1. Cargas notas de reunion al backend (`/process-notes`) para indexarlas.
2. Mantienes presionado el sensor touch del hardware para hablar.
3. El backend transcribe, consulta RAG y responde por voz.
4. Si en la respuesta hay comandos de luces, el firmware los ejecuta.

## Endpoints principales

- `POST /process-notes`
  - Body JSON: `{"notes_text": "..."}`
  - Extrae proyectos, resumen, tareas, feedback y lo guarda (RAG/Notion).
- `POST /voice-assistant`
  - Multipart con `file` (audio) y `session_id`.
  - Devuelve `audio/mpeg` + header `X-Action` para hardware.
- `POST /update-personality`
  - Permite cambiar personalidad del asistente.
- `GET /dashboard`
  - UI simple para gestionar personalidad.
