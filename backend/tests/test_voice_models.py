"""La voz tiene un modelo de respaldo para cuando el primario está saturado.

Medido contra la API real, 1 de cada 6 llamadas a gemini-3.1-flash-lite vuelve
con 503 "high demand". Con la política de reintentos que trae el SDK de fábrica
eso se lleva unos 20 segundos antes de rendirse, y en ese rato el dispositivo no
dice nada.
"""

from app.config import (
    FALLBACK_GENAI_CONFIG,
    FAST_GENAI_CONFIG,
    GENAI_HTTP_OPTIONS,
    VOICE_MODEL,
    VOICE_MODEL_FALLBACK,
)
from app.integrations import voice_models


def test_el_primario_va_primero_y_el_respaldo_es_otro_modelo():
    modelos = voice_models()
    assert [nombre for nombre, _ in modelos] == [VOICE_MODEL, VOICE_MODEL_FALLBACK]
    # Reintentar contra el mismo modelo saturado no sirve de nada.
    assert VOICE_MODEL != VOICE_MODEL_FALLBACK


def test_el_respaldo_va_sin_thinking_config():
    """gemini-3.5-flash-lite contesta 400 INVALID_ARGUMENT si le llega thinking_budget."""
    assert FAST_GENAI_CONFIG.thinking_config is not None
    assert FALLBACK_GENAI_CONFIG.thinking_config is None


def test_los_dos_modelos_comparten_los_limites_de_la_respuesta():
    # El respaldo tiene que sonar igual: misma longitud y misma temperatura.
    assert FALLBACK_GENAI_CONFIG.max_output_tokens == FAST_GENAI_CONFIG.max_output_tokens
    assert FALLBACK_GENAI_CONFIG.temperature == FAST_GENAI_CONFIG.temperature


def test_no_hay_reintentos_contra_el_mismo_modelo():
    """El timeout del SDK es por intento: cada reintento multiplica la espera.

    Medido, un modelo saturado tardaba 19 s en rendirse y con dos intentos de
    20 s llegó a 41 s de ReadTimeout. El reintento es el modelo de respaldo.
    """
    assert GENAI_HTTP_OPTIONS.retry_options.attempts <= 1
    assert GENAI_HTTP_OPTIONS.timeout <= 20_000, "milisegundos, y se paga una vez por modelo"
