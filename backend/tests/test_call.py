"""Relay TCP de la videollamada y la tool que la dispara.

El relay se prueba levantando el server real en un puerto efímero y hablando
con dos sockets, sin tocar Gemini ni la app.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from app import call


@asynccontextmanager
async def relay():
    server = await asyncio.start_server(call._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), 2)
        except (asyncio.TimeoutError, Exception):
            pass


async def join(port: int, room: str):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write((room + "\n").encode())
    await writer.drain()
    return reader, writer


# --------------------------------------------------------------------------- #
# Relay
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_relay_pipes_bytes_both_ways_between_paired_sockets():
    async with relay() as port:
        a_reader, a_writer = await join(port, "franco+jose")
        b_reader, b_writer = await join(port, "franco+jose")
        await asyncio.sleep(0.05)  # que el server empareje

        a_writer.write(b"\x00\x00\x00\x04ABCD")
        await a_writer.drain()
        assert await asyncio.wait_for(b_reader.readexactly(8), 1) == b"\x00\x00\x00\x04ABCD"

        b_writer.write(b"hola-franco")
        await b_writer.drain()
        assert await asyncio.wait_for(a_reader.readexactly(11), 1) == b"hola-franco"

        a_writer.close()

        for w in (a_writer, b_writer):
            w.close()


@pytest.mark.anyio
async def test_relay_drops_the_other_side_when_one_hangs_up():
    async with relay() as port:
        a_reader, a_writer = await join(port, "sala")
        b_reader, b_writer = await join(port, "sala")
        await asyncio.sleep(0.05)

        a_writer.close()  # uno cuelga
        assert await asyncio.wait_for(b_reader.read(), 1) == b""  # el otro ve EOF

        b_writer.close()


@pytest.mark.anyio
async def test_relay_keeps_rooms_separate():
    async with relay() as port:
        _, a_writer = await join(port, "sala-uno")
        b_reader, b_writer = await join(port, "sala-uno")
        _, c_writer = await join(port, "sala-dos")
        d_reader, d_writer = await join(port, "sala-dos")
        await asyncio.sleep(0.05)

        a_writer.write(b"para-b")
        await a_writer.drain()
        c_writer.write(b"para-d")
        await c_writer.drain()

        assert await asyncio.wait_for(b_reader.readexactly(6), 1) == b"para-b"
        assert await asyncio.wait_for(d_reader.readexactly(6), 1) == b"para-d"

        for w in (a_writer, b_writer, c_writer, d_writer):
            w.close()


@pytest.mark.anyio
async def test_relay_drops_a_socket_that_never_sends_a_room():
    async with relay() as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"\n")  # sala vacía
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()


# --------------------------------------------------------------------------- #
# Tool
# --------------------------------------------------------------------------- #


def test_call_tool_declaration_shape():
    declaration = call.call_declaration()
    assert declaration.name == "iniciar_videollamada"
    assert declaration.parameters.required == ["persona"]
    assert "persona" in declaration.parameters.properties


def test_call_action_emits_a_sanitized_command():
    pending: list[str] = []
    frase = call.aplicar_accion_llamada({"persona": "  Franco!! "}, pending)
    assert pending == ["CALL:franco"]
    assert "franco" in frase.lower()


def test_call_action_without_a_name_does_nothing():
    pending: list[str] = []
    frase = call.aplicar_accion_llamada({"persona": "  "}, pending)
    assert pending == []
    assert "no entend" in frase.lower()


# --------------------------------------------------------------------------- #
# Llamada entrante (aviso por /device/config)
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _limpiar_incoming():
    call._incoming.clear()
    yield
    call._incoming.clear()


def test_calling_registers_an_incoming_for_the_callee():
    call.aplicar_accion_llamada({"persona": "josefina"}, [], caller="franco")
    assert call.incoming_call_for("josefina") == "franco"
    assert call.incoming_call_for("franco") is None  # el que llama no


def test_incoming_call_expires():
    call.note_incoming_call("josefina", "franco")
    call._incoming["josefina"] = ("franco", call.time.monotonic() - call._INCOMING_TTL_S - 1)
    assert call.incoming_call_for("josefina") is None


def test_pairing_clears_pending_incomings():
    call.note_incoming_call("josefina", "franco")
    call.clear_incoming("franco", "josefina")
    assert call.incoming_call_for("josefina") is None


def test_no_incoming_without_a_caller_name():
    call.aplicar_accion_llamada({"persona": "josefina"}, [], caller="")
    assert call.incoming_call_for("josefina") is None
