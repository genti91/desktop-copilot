"""Videollamada ESP↔ESP sin audio: relay TCP + la tool que la dispara.

El relay no mira el contenido. Cada ESP abre un socket, manda una línea con el
nombre de la sala (`\\n` al final) y a partir de ahí el relay copia todo lo que
llega de un lado al otro. El framing —largo de 4 bytes + JPEG— lo maneja el
firmware.

La sala la arma cada ESP como ``sorted(nombre_propio, destino)`` unida por "+":
"llamar a franco" desde el ESP de jose y "llamar a jose" desde el de franco
caen en la misma sala sin que el backend sepa quién es quién.
"""

import asyncio
import re

from google.genai import types

from .config import CALL_RELAY_HOST, CALL_RELAY_PORT

CALL_TOOL = "iniciar_videollamada"

_HANDSHAKE_TIMEOUT_S = 10
_PAIR_WAIT_TIMEOUT_S = 45
_ROOM_MAX_LEN = 80
_CHUNK = 8192

# sala -> (reader, writer, partner_llego: Future, puente_termino: Event)
_waiting: dict[str, tuple] = {}


# --------------------------------------------------------------------------- #
# Relay
# --------------------------------------------------------------------------- #


def _shut(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
    except OSError:
        pass


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await src.read(_CHUNK)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
    except (OSError, asyncio.CancelledError):
        pass


async def _bridge(
    a_reader: asyncio.StreamReader,
    a_writer: asyncio.StreamWriter,
    b_reader: asyncio.StreamReader,
    b_writer: asyncio.StreamWriter,
) -> None:
    """Copia bytes en los dos sentidos. En cuanto un lado corta, cae la llamada."""
    tasks = [
        asyncio.create_task(_pipe(a_reader, b_writer)),
        asyncio.create_task(_pipe(b_reader, a_writer)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        _shut(a_writer)
        _shut(b_writer)


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        raw = await asyncio.wait_for(reader.readline(), _HANDSHAKE_TIMEOUT_S)
    except (asyncio.TimeoutError, OSError):
        _shut(writer)
        return

    room = raw.decode("utf-8", "replace").strip()[:_ROOM_MAX_LEN]
    if not room:
        _shut(writer)
        return

    partner = _waiting.pop(room, None)

    if partner is None:
        # Primero en la sala: queda esperando al segundo.
        llego = asyncio.get_event_loop().create_future()
        termino = asyncio.Event()
        _waiting[room] = (reader, writer, llego, termino)
        try:
            await asyncio.wait_for(llego, _PAIR_WAIT_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _waiting.pop(room, None)
            _shut(writer)
            return
        # El segundo maneja el puente; acá sólo se espera a que termine.
        await termino.wait()
        _shut(writer)
        return

    # Segundo en la sala: empareja y hace de puente en los dos sentidos.
    p_reader, p_writer, p_llego, p_termino = partner
    if not p_llego.done():
        p_llego.set_result(True)
    try:
        await _bridge(p_reader, p_writer, reader, writer)
    finally:
        p_termino.set()


async def call_relay_server() -> None:
    """Servidor del relay. Se lanza desde el lifespan de la app."""
    server = await asyncio.start_server(_handle, CALL_RELAY_HOST, CALL_RELAY_PORT)
    addr = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"📹 Relay de videollamada escuchando en {addr}")
    async with server:
        await server.serve_forever()


# --------------------------------------------------------------------------- #
# Tool
# --------------------------------------------------------------------------- #


def call_declaration() -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=CALL_TOOL,
        description=(
            "Inicia una videollamada sin audio con otra persona. La otra persona "
            "tiene que iniciar la llamada de su lado también para que se conecte. "
            "El sensor táctil corta la llamada."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            required=["persona"],
            properties={
                "persona": types.Schema(
                    type=types.Type.STRING,
                    description="Nombre de la persona a llamar, en minúsculas. Ej: 'franco', 'jose'.",
                ),
            },
        ),
    )


def aplicar_accion_llamada(args: dict, pending_esp: list[str]) -> str:
    """Traduce la tool a un comando CALL:<persona> para la cabecera X-Action."""
    persona = re.sub(r"[^a-z0-9-]", "", str(args.get("persona", "")).strip().lower())
    if not persona:
        return "no entendí a quién llamar"
    pending_esp.append(f"CALL:{persona}")
    return f"llamando a {persona}"
