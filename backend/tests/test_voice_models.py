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


def test_el_presupuesto_de_reintentos_esta_acotado():
    reintentos = GENAI_HTTP_OPTIONS.retry_options
    assert reintentos.attempts <= 2, "más intentos y no queda tiempo para el respaldo"
    assert reintentos.max_delay <= 2.0
