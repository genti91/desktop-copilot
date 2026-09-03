import json
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

# Las notas son archivos .md en un vault de Obsidian, que es la única fuente de
# verdad: el vector store (RAG) se reconstruye a partir de ellos y lo que editás
# en Obsidian se reindexa solo.
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", str(BASE_DIR / "data" / "vault"))
# El watcher reindexa los .md que cambian por fuera del backend (los que tocás
# en Obsidian). Es un poll simple, sin dependencias nuevas; lo que escribe el
# propio backend se indexa al instante y no espera a esto.
VAULT_WATCH_ENABLED = os.getenv("VAULT_WATCH_ENABLED", "1").lower() not in ("0", "false", "no")
VAULT_WATCH_INTERVAL_SECONDS = int(os.getenv("VAULT_WATCH_INTERVAL_SECONDS", "5"))
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


def _device_list(raw: str) -> list[str]:
    return [name.strip().lower() for name in raw.split(",") if name.strip()]


# Cada ESP se identifica con la cabecera X-Device-Name (la completa en el portal
# cautivo). Cada equipo tiene su propio perfil: luces, imagen, personalidad y si
# usa notas/RAG. DEVICE_NAMES es la semilla del selector del panel; cualquier
# equipo que aparezca sondeando se agrega solo.
DEVICE_NAMES = _device_list(os.getenv("DEVICE_NAMES", "franco,josefina"))

# Equipos que arrancan SIN notas ni RAG: sólo le hablan a Gemini (con luces y
# videollamadas), sin recuperar contexto del vault ni generar capturas. Es sólo
# el valor inicial del perfil; después se cambia desde /personality.
RAG_DISABLED_DEVICES = _device_list(os.getenv("RAG_DISABLED_DEVICES", "franco"))

# Lámparas Tuya de la habitación, controladas por LAN con tinytuya. El asistente
# de voz las prende/apaga y les cambia el color por function calling.
#
# Formato: una lista JSON en la variable de entorno, un objeto por lámpara con
#   nombre, id, key, ip y (opcional) version del protocolo local.
# Ej: TUYA_LAMPS=[{"nombre":"velador","id":"...","key":"...","ip":"192.168.1.50","version":"3.4"}]
# Los valores salen de `python -m tinytuya wizard`. Sin esto no se registra
# ninguna lámpara y la función Tuya no se le ofrece al modelo.
# Relay TCP para la videollamada ESP↔ESP (sin audio). Los dos ESP se conectan a
# este puerto —vía tailnet o LAN—, mandan el nombre de la sala y el relay copia
# bytes de uno al otro. Corre en el mismo proceso que la API, en otro puerto.
CALL_RELAY_HOST = os.getenv("CALL_RELAY_HOST", "0.0.0.0")
CALL_RELAY_PORT = int(os.getenv("CALL_RELAY_PORT", "8001"))
CALL_RELAY_ENABLED = os.getenv("CALL_RELAY_ENABLED", "1").lower() not in ("0", "false", "no")

_tuya_lamps_raw = os.getenv("TUYA_LAMPS", "").strip()
try:
    TUYA_LAMPS = json.loads(_tuya_lamps_raw) if _tuya_lamps_raw else []
    if not isinstance(TUYA_LAMPS, list):
        raise ValueError("TUYA_LAMPS tiene que ser una lista JSON")
except (json.JSONDecodeError, ValueError) as error:
    print(f"[Config] TUYA_LAMPS inválido, se ignora: {error}")
    TUYA_LAMPS = []

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

# Sin reintentos a propósito. El timeout del SDK es POR INTENTO, así que cada
# reintento multiplica la espera: medido acá, un modelo saturado tardaba 19 s en
# rendirse y una vez llegó a 41 s de ReadTimeout con dos intentos de 20 s. El
# reintento nuestro es el modelo de respaldo, que además tiene sentido: al que
# está sobrecargado no sirve insistirle.
#
# El techo se paga una vez por modelo. Con 12 s se cortaban respuestas sanas
# —el primario real dio ReadTimeout en el camino normal— y una respuesta de 15 s
# es mejor que una disculpa a los 12. Con 18 s el peor caso son 36 s, que
# requiere que los dos modelos se cuelguen; el caso típico son ~5 s.
GENAI_HTTP_OPTIONS = types.HttpOptions(
    timeout=18_000,  # milisegundos, por intento
    retry_options=types.HttpRetryOptions(attempts=1),
)
