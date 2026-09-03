"""Ejecución de las tools de luces (function calling manual).

`app.lights` no toca Gemini ni la red al importarse, así que corre solo.
"""

import pytest

from app import lights


@pytest.fixture(autouse=True)
def catalogo_limpio():
    """Cada test arranca sin lámparas Tuya registradas."""
    lights.registrar_lamparas([])
    yield
    lights.registrar_lamparas([])


class FakeBulb:
    def __init__(self):
        self.calls: list[tuple] = []

    def turn_on(self):
        self.calls.append(("on",))

    def turn_off(self):
        self.calls.append(("off",))

    def set_colour(self, red, green, blue):
        self.calls.append(("colour", red, green, blue))

    def set_brightness_percentage(self, porcentaje):
        self.calls.append(("brightness", porcentaje))


# --------------------------------------------------------------------------- #
# LEDs del ESP (se traducen a la mini-sintaxis de X-Action)
# --------------------------------------------------------------------------- #


def test_esp_action_builds_x_action_tokens():
    pending: list[str] = []
    frase = lights.aplicar_accion_luz(
        lights.ESP_TOOL,
        {"color_rgb": "255, 0, 0", "brillo": 40, "filamento": "encender"},
        pending,
    )
    assert pending == ["LED_RGB:255,0,0", "LED_BRIGHTNESS:102", "FILAMENT_ON"]
    assert "color" in frase and "brillo" in frase


def test_esp_all_off_is_a_single_token():
    pending: list[str] = []
    lights.aplicar_accion_luz(lights.ESP_TOOL, {"apagar_todo": True}, pending)
    assert pending == ["ALL_OFF"]


def test_esp_ignores_malformed_color():
    pending: list[str] = []
    lights.aplicar_accion_luz(lights.ESP_TOOL, {"color_rgb": "rojo"}, pending)
    assert pending == []


def test_esp_clamps_brightness_to_255_scale():
    pending: list[str] = []
    lights.aplicar_accion_luz(lights.ESP_TOOL, {"brillo": 100}, pending)
    assert pending == ["LED_BRIGHTNESS:255"]


# --------------------------------------------------------------------------- #
# Lámparas de la habitación: Tuya (tinytuya, TCP)
# --------------------------------------------------------------------------- #


@pytest.fixture
def velador_tuya():
    lights.registrar_lamparas([{"nombre": "velador", "id": "a", "key": "b", "ip": "1.2.3.4"}])
    yield


def test_tuya_turn_off_only_calls_off(monkeypatch, velador_tuya):
    fake = FakeBulb()
    monkeypatch.setattr(lights, "_bulb", lambda nombre: fake)
    frase = lights.aplicar_accion_luz(
        lights.LAMP_TOOL, {"lampara": "velador", "encender": False}, []
    )
    assert fake.calls == [("off",)]
    assert "apagué" in frase.lower()


def test_tuya_color_change_turns_the_lamp_on_first(monkeypatch, velador_tuya):
    fake = FakeBulb()
    monkeypatch.setattr(lights, "_bulb", lambda nombre: fake)
    lights.aplicar_accion_luz(
        lights.LAMP_TOOL,
        {"lampara": "velador", "color_rgb": "0,255,0", "brillo": 80},
        [],
    )
    assert fake.calls == [("on",), ("colour", 0, 255, 0), ("brightness", 80)]


def test_unknown_lamp_is_reported(velador_tuya):
    frase = lights.aplicar_accion_luz(lights.LAMP_TOOL, {"lampara": "cocina"}, [])
    assert "no encontré" in frase.lower()


def test_tuya_offline_lamp_does_not_raise(monkeypatch, velador_tuya):
    class Boom:
        def turn_on(self):
            raise OSError("timeout")

    monkeypatch.setattr(lights, "_bulb", lambda nombre: Boom())
    frase = lights.aplicar_accion_luz(
        lights.LAMP_TOOL, {"lampara": "velador", "encender": True}, []
    )
    assert "no pude conectar" in frase.lower()


# --------------------------------------------------------------------------- #
# Lámparas de la habitación: WiZ (comando UDP que ejecuta el ESP)
# --------------------------------------------------------------------------- #


def test_wiz_lamp_queues_an_x_action_command_for_the_esp():
    lights.registrar_lamparas([], [{"nombre": "wizled", "ip": "10.0.0.9"}])
    pending: list[str] = []
    frase = lights.aplicar_accion_luz(
        lights.LAMP_TOOL,
        {"lampara": "wizled", "encender": True, "color_rgb": "0,0,255", "brillo": 80},
        pending,
    )
    assert pending == ["WIZ:10.0.0.9:state=1,r=0,g=0,b=255,dimming=80"]
    assert "wizled" in frase.lower()


def test_wiz_turn_off_is_a_single_state_command():
    lights.registrar_lamparas([], [{"nombre": "wizled", "ip": "10.0.0.9"}])
    pending: list[str] = []
    frase = lights.aplicar_accion_luz(
        lights.LAMP_TOOL, {"lampara": "wizled", "encender": False}, pending
    )
    assert pending == ["WIZ:10.0.0.9:state=0"]
    assert "apagué" in frase.lower()


# --------------------------------------------------------------------------- #
# Declaraciones y catálogo
# --------------------------------------------------------------------------- #


def test_only_the_esp_tool_is_offered_without_lamps():
    tools = lights.build_light_tools()
    assert len(tools) == 1
    assert [d.name for d in tools[0].function_declarations] == [lights.ESP_TOOL]
    assert lights.LAMP_TOOL not in lights.tools_prompt()


def test_lamp_tool_mixes_tuya_and_wiz_names_as_enum():
    lights.registrar_lamparas(
        [{"nombre": "Velador", "id": "a", "key": "b", "ip": "1.2.3.4"}],
        [{"nombre": "Techo", "ip": "10.0.0.9"}],
    )
    declaraciones = {d.name: d for d in lights.build_light_tools()[0].function_declarations}
    assert set(declaraciones) == {lights.ESP_TOOL, lights.LAMP_TOOL}
    assert declaraciones[lights.LAMP_TOOL].parameters.properties["lampara"].enum == ["techo", "velador"]
    assert "velador" in lights.tools_prompt() and "techo" in lights.tools_prompt()


def test_lamps_are_scoped_by_the_equipos_field():
    lights.registrar_lamparas(
        [{"nombre": "franco-tuya", "id": "a", "key": "b", "ip": "1.1.1.1", "equipos": ["franco"]}],
        [{"nombre": "jose-wiz", "ip": "2.2.2.2", "equipos": ["josefina"]}],
    )
    assert lights.lamp_names_for("franco") == ["franco-tuya"]
    assert lights.lamp_names_for("josefina") == ["jose-wiz"]
    # Franco no recibe la lámpara de Josefina en su declaración.
    franco_tools = lights.build_light_tools("franco")[0].function_declarations
    assert franco_tools[1].parameters.properties["lampara"].enum == ["franco-tuya"]


def test_lamp_tool_is_withheld_from_a_device_without_permission():
    lights.registrar_lamparas([{"nombre": "velador", "id": "a", "key": "b", "ip": "1.2.3.4"}])
    withheld = [d.name for d in lights.build_light_tools("franco", lamps_enabled=False)[0].function_declarations]
    assert withheld == [lights.ESP_TOOL]
    assert lights.LAMP_TOOL not in lights.tools_prompt("franco", lamps_enabled=False)
    assert lights.LAMP_TOOL in {d.name for d in lights.build_light_tools("franco")[0].function_declarations}


def test_lamp_call_without_permission_is_a_no_op(monkeypatch):
    lights.registrar_lamparas(
        [{"nombre": "velador", "id": "a", "key": "b", "ip": "1.2.3.4", "equipos": ["franco"]}]
    )

    def _boom(_nombre):
        raise AssertionError("no debería tocar la lámpara")

    monkeypatch.setattr(lights, "_bulb", _boom)
    # Equipo con el flag de lámparas apagado.
    assert lights.aplicar_accion_luz(
        lights.LAMP_TOOL, {"lampara": "velador", "encender": True}, [], "franco", lamps_enabled=False
    ) == ""
    # Equipo al que esa lámpara no le pertenece (equipos=["franco"]).
    assert lights.aplicar_accion_luz(
        lights.LAMP_TOOL, {"lampara": "velador", "encender": True}, [], "josefina"
    ) == ""


def test_registrar_skips_incomplete_lamps():
    lights.registrar_lamparas(
        [
            {"nombre": "sin-key", "id": "a"},
            {"nombre": "completa", "id": "a", "key": "b"},
        ]
    )
    assert lights.LAMP_NAMES == ["completa"]


def test_default_confirmation_phrasing():
    assert lights.confirmacion_por_defecto([]) == "Listo."
    assert (
        lights.confirmacion_por_defecto(["cambié el color", "ajusté el brillo"])
        == "Cambié el color y ajusté el brillo."
    )
