"""Puente entre el texto que va largando Gemini y el audio que sale al ESP32.

El pipeline viejo era estrictamente secuencial: se esperaba la respuesta entera
de Gemini, después se sintetizaba entera, y recién ahí salía el primer byte. Acá
las dos etapas se solapan: apenas hay una oración cerrada se manda a sintetizar
mientras el modelo sigue escribiendo la siguiente.
"""

import asyncio
import re
import threading
from typing import AsyncIterator, Iterable

# Cierre de oración seguido de espacio, o un salto de línea. El lookbehind deja
# el signo de puntuación del lado de la oración que termina.
_SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+|\n+")

# Debajo de esto no vale la pena cortar: una síntesis de tres palabras tarda casi
# lo mismo que una de treinta, así que fragmentar de más suma latencia en vez de
# sacarla.
_MIN_CHUNK_CHARS = 60


def split_ready_sentences(buffer: str, *, flush: bool = False) -> tuple[list[str], str]:
    """Parte lo que ya está cerrado y devuelve el resto sin cerrar.

    Con flush=True se entrega todo, aunque la última oración no haya terminado:
    es lo que corresponde cuando el modelo dejó de escribir.
    """
    pieces = _SENTENCE_END.split(buffer)
    if not flush:
        # El último trozo todavía puede estar creciendo.
        buffer = pieces.pop() if pieces else ""
    else:
        buffer = ""

    ready: list[str] = []
    pending = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        pending = f"{pending} {piece}".strip() if pending else piece
        if len(pending) >= _MIN_CHUNK_CHARS:
            ready.append(pending)
            pending = ""

    if pending:
        if flush:
            ready.append(pending)
        else:
            # Todavía es corto: vuelve al buffer a esperar más texto.
            buffer = f"{pending} {buffer}".strip() if buffer else pending

    return ready, buffer


async def aiter_to_thread(make_iterator) -> AsyncIterator[str]:
    """Convierte un generador bloqueante en uno async.

    El SDK de Gemini expone el streaming como un iterador sincrónico. Consumirlo
    derecho desde el endpoint frenaría el event loop entero —y con él, el envío
    del audio que ya tenemos listo—, así que va en su propio hilo y los pedazos
    viajan por una cola.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    def produce():
        try:
            iterator: Iterable = make_iterator()
            for item in iterator:
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as error:  # se re-lanza del lado async
            loop.call_soon_threadsafe(queue.put_nowait, error)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, done)

    threading.Thread(target=produce, daemon=True).start()

    while True:
        item = await queue.get()
        if item is done:
            return
        if isinstance(item, Exception):
            raise item
        yield item
