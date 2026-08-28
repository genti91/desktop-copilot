"""Tests del troceo de texto que alimenta la síntesis en streaming."""

import pytest

from app.streaming import aiter_to_thread, split_ready_sentences


def test_no_entrega_nada_hasta_cerrar_una_oracion():
    ready, buffer = split_ready_sentences("Hola, todavía estoy escribiendo")
    assert ready == []
    assert buffer == "Hola, todavía estoy escribiendo"


def test_corta_en_el_punto_y_deja_el_resto_pendiente():
    texto = "Hola, ¿cómo andás? Todo bien por acá, gracias por preguntar. Y vos"
    ready, buffer = split_ready_sentences(texto)
    assert ready == ["Hola, ¿cómo andás? Todo bien por acá, gracias por preguntar."]
    assert buffer == "Y vos"


def test_junta_oraciones_cortas_en_un_solo_pedazo():
    # Tres oraciones de pocas palabras: sintetizarlas por separado costaría más
    # que hacerlo junto, así que se acumulan hasta llegar al mínimo.
    texto = "Sí. Claro. Dale, lo anoto para más tarde así no se pierde. Listo"
    ready, buffer = split_ready_sentences(texto)
    assert ready == ["Sí. Claro. Dale, lo anoto para más tarde así no se pierde."]
    assert buffer == "Listo"


def test_flush_entrega_la_oracion_sin_terminar():
    ready, buffer = split_ready_sentences("Corto", flush=True)
    assert ready == ["Corto"]
    assert buffer == ""


def test_el_comando_viaja_en_el_primer_pedazo():
    # Importa que el [CMD:] quede en lo primero que se entrega: de ahí sale la
    # cabecera X-Action, que se manda antes que el audio.
    texto = "[CMD:LED_RGB:0,255,0] Listo, te puse la luz en verde como pediste. Otra cosa"
    ready, _ = split_ready_sentences(texto)
    assert ready[0].startswith("[CMD:LED_RGB:0,255,0]")


def test_corta_en_los_saltos_de_linea():
    ready, buffer = split_ready_sentences("Primer punto que ocupa bastante espacio\nSegundo")
    assert ready == ["Primer punto que ocupa bastante espacio"]
    assert buffer == "Segundo"


@pytest.mark.anyio
async def test_aiter_to_thread_propaga_los_elementos():
    recibidos = [item async for item in aiter_to_thread(lambda: iter(["a", "b", "c"]))]
    assert recibidos == ["a", "b", "c"]


@pytest.mark.anyio
async def test_aiter_to_thread_relanza_la_excepcion_del_hilo():
    def explota():
        yield "primero"
        raise RuntimeError("se cayó Gemini")

    recibidos = []
    with pytest.raises(RuntimeError, match="se cayó Gemini"):
        async for item in aiter_to_thread(explota):
            recibidos.append(item)

    assert recibidos == ["primero"]
