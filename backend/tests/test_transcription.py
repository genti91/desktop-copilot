"""El filtro que decide si una transcripción es un pedido o ruido.

Lo que motivó el filtro: el equipo abre una ventana de escucha después de
contestar, y Whisper, cuando en esa ventana sólo hubo ruido, no devuelve vacío
—inventa una muletilla de los subtítulos con los que se entrenó—. El backend
contestaba esas muletillas, y desde afuera se veía como un equipo que habla solo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.transcription import es_ruido, normalizar  # noqa: E402


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "   ",
        "...",
        "a",
        "¡Gracias!",
        "Gracias por ver el video.",
        "Muchas gracias",
        "Subtítulos realizados por la comunidad de Amara.org",
        "¡Suscríbete al canal!",
    ],
)
def test_las_alucinaciones_y_el_silencio_no_se_contestan(texto):
    assert es_ruido(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "prendé la luz del escritorio",
        "¿qué tengo pendiente del proyecto?",
        "llamá a Franco",
        "no",
        # "sí" tiene que pasar: es la respuesta natural a una repregunta dentro
        # de la ventana de seguimiento, que es justamente lo que hay que cuidar.
        "sí",
        "dale",
        # Contiene una muletilla, pero además dice algo: sólo se descarta cuando
        # la muletilla es TODO lo que llegó.
        "gracias, apagá la luz",
    ],
)
def test_los_pedidos_de_verdad_pasan(texto):
    assert not es_ruido(texto)


def test_la_comparacion_ignora_tildes_puntuacion_y_mayusculas():
    # Whisper devuelve la misma alucinación escrita de mil formas; la lista se
    # guarda en una sola y la normalización hace el resto.
    assert normalizar("¡GRACIAS, por ver el vídeo!") == "gracias por ver el video"
    assert es_ruido("Subtitulos realizados por la comunidad de Amara.org")
