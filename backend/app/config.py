import os
from pathlib import Path

from dotenv import load_dotenv
from google.genai import types

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

APP_TITLE = "Desktop Co-Pilot API"
# Base de la memoria consultable (RAG). CHROMA_PATH queda sólo para que el
# script de migración encuentre la base vieja de ChromaDB.
VECTOR_STORE_PATH = os.getenv("VECTOR_STORE_PATH", str(BASE_DIR / "data" / "memory.sqlite3"))
CHROMA_PATH = os.getenv("CHROMA_PATH", str(BASE_DIR / "chroma_db"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
MAX_HISTORY_MESSAGES = 8
SESSION_TTL_SECONDS = 1800

# Espejado automático de firmware desde los releases de GitHub. CI no puede
# alcanzar este backend en la LAN, así que es el backend el que va a buscar.
FIRMWARE_REPO = os.getenv("FIRMWARE_REPO", "genti91/desktop-copilot")
FIRMWARE_RELEASE_PREFIX = os.getenv("FIRMWARE_RELEASE_PREFIX", "fw-")
FIRMWARE_AUTO_SYNC = os.getenv("FIRMWARE_AUTO_SYNC", "1").lower() not in ("0", "false", "no")
FIRMWARE_SYNC_INTERVAL_SECONDS = int(os.getenv("FIRMWARE_SYNC_INTERVAL_SECONDS", "300"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Autenticación del panel. Sin PANEL_PASSWORD el backend queda abierto: sólo
# tiene sentido en desarrollo o detrás de una red privada como Tailscale.
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "720"))

# El ESP32 no maneja cookies. Alcanza los endpoints que necesita desde la LAN,
# o con este token si algún día sale a internet.
DEVICE_TOKEN = os.getenv("DEVICE_TOKEN", "")
TRUSTED_NETWORKS = os.getenv("TRUSTED_NETWORKS", "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12")

FAST_GENAI_CONFIG = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_budget=0),
    max_output_tokens=300,
    temperature=0.3,
)

# El modelo de la voz sale por variable de entorno para poder cambiarlo sin
# tocar el código el día que alguno se ponga lento o desaparezca.
VOICE_MODEL = os.getenv("VOICE_MODEL", "gemini-3.1-flash-lite")

# Respaldo para cuando el primario contesta 503 "high demand", que medido acá
# pasa en 1 de cada 6 llamadas. Es otro modelo y no un reintento contra el
# mismo: si está saturado, insistirle no ayuda.
VOICE_MODEL_FALLBACK = os.getenv("VOICE_MODEL_FALLBACK", "gemini-3.5-flash-lite")

# El de respaldo va sin thinking_config: rechaza thinking_budget=0 con un
# 400 INVALID_ARGUMENT. Igual no le hace falta, no es un modelo que razone.
FALLBACK_GENAI_CONFIG = types.GenerateContentConfig(
    max_output_tokens=300,
    temperature=0.3,
)

# Presupuesto de reintentos del SDK. Con el default (5 intentos, hasta 60 s de
# espera) un 503 se lleva unos 20 segundos antes de rendirse, y el dispositivo
# se queda mudo todo ese rato. Así falla en ~2 s y queda tiempo para el respaldo.
GENAI_HTTP_OPTIONS = types.HttpOptions(
    timeout=20_000,  # milisegundos
    retry_options=types.HttpRetryOptions(
        attempts=2,
        initial_delay=0.5,
        max_delay=2.0,
    ),
)
