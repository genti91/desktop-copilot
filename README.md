# Desktop Co-Pilot

Asistente de escritorio con IA + hardware (ESP32-S3) para:

- Cargar notas de reuniones y guardarlas como memoria consultable (RAG).
- Hacer preguntas sobre esas notas en lenguaje natural.
- Hablar con el asistente por voz.
- Controlar luces del hardware desde comandos generados por la IA.
- Configurar el dispositivo desde la web (luces, pantalla, imagen de reposo) y actualizarlo por OTA.

Proyecto pensado para uso diario de trabajo: capturas notas, las dejas indexadas, y despues consultas contexto cuando lo necesites.

## Foto del hardware


<img src="./images/IMG_6478.gif" width="500" alt="Desktop Co-Pilot">

## Que hace hoy

- Backend FastAPI para:
  - Procesar notas de reuniones (`/process-notes`).
  - Recibir audio del ESP32 y responder con audio (`/voice-assistant`).
  - Recuperar contexto desde ChromaDB (RAG) para responder preguntas.
  - Guardar tareas/notas en Notion.
  - Servir la configuracion del dispositivo (`/device`), las imagenes de pantalla y el firmware OTA.
- Firmware para ESP32-S3 (Seeed XIAO ESP32S3) que:
  - Se conecta por Wi-Fi (WiFiManager + portal cautivo).
  - Graba audio mientras mantenes presionado el touch sensor.
  - Envia audio al backend.
  - Reproduce la respuesta TTS del asistente.
  - Ejecuta comandos de luces recibidos en el header HTTP `X-Action`.
  - Sondea la configuracion remota y aplica colores, encendidos e imagen de pantalla.
  - Se autoactualiza por OTA al arrancar si el backend publico un build mas nuevo.

## Estado actual

- Grabacion activada por touch sensor (mantener presionado).
- Control de luces funcionando por comandos:
  - `LED_RGB:R,G,B`
  - `LED_BRIGHTNESS:V`
  - `FILAMENT_ON`
  - `FILAMENT_OFF`
- Configuracion remota del dispositivo en `http://<backend>:8000/device`:
  - Encendido independiente del LED RGB, el LED de filamento y la pantalla.
  - Color y brillo del NeoPixel.
  - Imagen de reposo elegida del catalogo (extraido de `Imagenes.pdf`) o subida propia.
  - Publicacion de firmware para OTA.
- Actualizacion OTA verificada por SHA-256, con barra de progreso en la pantalla.
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
|- Imagenes.pdf           # laminas de origen para las imagenes de pantalla
|- backend/
|  |- app/
|  |  |- assets/default_images/  # PNG 240x240 extraidos del PDF
|  |  \- templates/              # dashboard.html y device.html
|  |- chroma_db/
|  |- data/               # config del dispositivo + firmware OTA (no versionado)
|  |- scripts/
|  |- tests/
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
  - `ArduinoJson`
- Tabla de particiones `default_8MB.csv` (la del board): trae `app0`/`app1`, necesarias para OTA.

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

Paginas web:

- `http://127.0.0.1:8000/dashboard` - personalidad y notas de reunion.
- `http://127.0.0.1:8000/device` - luces, pantalla, imagenes y firmware OTA.

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
- `GET /device`
  - Pagina de configuracion del ESP32 (luces, pantalla, imagenes y OTA).
- `GET /device/config`
  - Vista compacta que sondea el ESP32 cada 5 s. Trae `revision`, estado de cada
    salida, color/brillo del RGB y la imagen activa con su checksum.
- `POST /device/config`
  - Actualizacion parcial (`rgb_enabled`, `rgb_color`, `rgb_brightness`,
    `filament_enabled`, `display_enabled`, `image_id`, `clear_image`).
    Cada cambio sube `revision`, que es lo que dispara el refresco en el firmware.
- `GET /device/images` / `POST /device/images` / `DELETE /device/images/{id}`
  - Catalogo, subida y borrado de imagenes. Todo se normaliza a 240x240.
- `GET /device/image`
  - Imagen activa en RGB565 little-endian (115200 bytes), lista para `pushImage`.
- `GET /ota/manifest`
  - `{"available", "version", "build", "url", "sha256", "size", ...}`.
- `POST /ota/firmware`
  - Multipart con `file` (firmware.bin), `version`, `build` y `notes`.
- `GET /ota/download`
  - Binario publicado.

## Configuracion del dispositivo

La pagina `/device` guarda todo en el backend y el ESP32 lo aplica solo:

- **LED RGB, LED de filamento y pantalla** se prenden y apagan por separado.
  El comando de voz `ALL_OFF` sigue siendo un apagado temporal: no pisa la
  configuracion guardada y el proximo toque del sensor devuelve todo a como estaba.
- **Color y brillo** del NeoPixel se eligen desde la web y quedan persistidos.
- **Imagen de pantalla**: se elige del catalogo o se sube una propia. El backend la
  convierte a RGB565 y el ESP32 la descarga una sola vez (se compara por checksum)
  a `/idle565.raw` en LittleFS.

Comportamiento de la imagen:

1. En reposo el ESP32 muestra la imagen elegida.
2. Al tocar el sensor para hablar, la imagen desaparece al instante y vuelve la cara animada.
3. Durante grabacion, espera y reproduccion se anima la cara como siempre.
4. Al terminar la interaccion reaparece la imagen.
5. Si no hay imagen elegida, la cara animada funciona igual que antes.

La configuracion tambien se guarda en el ESP32 (`/settings.json` en LittleFS), asi
que el dispositivo arranca con el ultimo estado conocido aunque el backend este caido.

## Actualizacion OTA

1. Subir `FIRMWARE_BUILD` (y `FIRMWARE_VERSION`) en `firmware/include/version.h`.
2. Compilar: `pio run -e xiao_esp32s3`.
3. Publicar `firmware/.pio/build/xiao_esp32s3/firmware.bin` desde `/device`,
   indicando la misma version y build.
4. Reiniciar el ESP32. En el setup consulta `/ota/manifest`, y si el `build` remoto
   es mayor descarga el binario, verifica el SHA-256 y se reinicia con el firmware nuevo.
   Si el hash no coincide, aborta y sigue con el firmware actual.

La particion `default_8MB.csv` del board ya reserva `app0`/`app1` (3.1 MB cada una),
asi que no hace falta tocar el esquema de particiones.
