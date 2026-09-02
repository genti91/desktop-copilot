import io

import edge_tts
from google import genai
from groq import Groq

from .config import (
    FALLBACK_GENAI_CONFIG,
    FAST_GENAI_CONFIG,
    GEMINI_API_KEY,
    GENAI_HTTP_OPTIONS,
    GROQ_API_KEY,
    VECTOR_STORE_PATH,
    VOICE_MODEL,
    VOICE_MODEL_FALLBACK,
)
from .vectorstore import Collection


# Sin key, el cliente de Gemini falla al construirse y se lleva puesto el import
# de toda la app. Queda en None para que el panel, la configuración del
# dispositivo y el OTA sigan funcionando: nada de eso usa Gemini.
gemini = (
    genai.Client(api_key=GEMINI_API_KEY, http_options=GENAI_HTTP_OPTIONS)
    if GEMINI_API_KEY
    else None
)


def voice_models():
    """Los modelos a probar para la voz, en orden, con su configuración.

    Van en una lista y no en un try/except suelto porque los dos caminos —el
    bloqueante y el de streaming— tienen que recorrer lo mismo.
    """
    return [
        (VOICE_MODEL, FAST_GENAI_CONFIG),
        (VOICE_MODEL_FALLBACK, FALLBACK_GENAI_CONFIG),
    ]


groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
collection = Collection(VECTOR_STORE_PATH)


async def generate_speech_bytes(text: str) -> bytes:
    communicate = edge_tts.Communicate(text, "es-AR-TomasNeural")
    buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    return buffer.getvalue()
