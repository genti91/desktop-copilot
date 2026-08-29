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
# Lámparas Tuya
# --------------------------------------------------------------------------- #


def test_tuya_turn_off_only_calls_off(monkeypatch):
    fake = FakeBulb()
    monkeypatch.setattr(lights, "_bulb", lambda nombre: fake)
    frase = lights.aplicar_accion_luz(
        lights.TUYA_TOOL, {"lampara": "velador", "encender": False}, []
    )
    assert fake.calls == [("off",)]
    assert "apagué" in frase.lower()


def test_tuya_color_change_turns_the_lamp_on_first(monkeypatch):
    fake = FakeBulb()
    monkeypatch.setattr(lights, "_bulb", lambda nombre: fake)
    lights.aplicar_accion_luz(
        lights.TUYA_TOOL,
        {"lampara": "velador", "color_rgb": "0,255,0", "brillo": 80},
        [],
    )
    assert fake.calls == [("on",), ("colour", 0, 255, 0), ("brightness", 80)]


def test_tuya_unknown_lamp_is_reported(monkeypatch):
    monkeypatch.setattr(lights, "_bulb", lambda nombre: None)
    frase = lights.aplicar_accion_luz(lights.TUYA_TOOL, {"lampara": "cocina"}, [])
    assert "no encontré" in frase.lower()


def test_tuya_offline_lamp_does_not_raise(monkeypatch):
    class Boom:
        def turn_on(self):
            raise OSError("timeout")

    monkeypatch.setattr(lights, "_bulb", lambda nombre: Boom())
    frase = lights.aplicar_accion_luz(
        lights.TUYA_TOOL, {"lampara": "velador", "encender": True}, []
    )
    assert "no pude conectar" in frase.lower()


# --------------------------------------------------------------------------- #
# Declaraciones y catálogo
# --------------------------------------------------------------------------- #


def test_only_the_esp_tool_is_offered_without_lamps():
    tools = lights.build_light_tools()
    assert len(tools) == 1
    assert [d.name for d in tools[0].function_declarations] == [lights.ESP_TOOL]
    assert lights.TUYA_TOOL not in lights.tools_prompt()


def test_tuya_tool_appears_with_lamp_names_as_enum():
    lights.registrar_lamparas(
        [{"nombre": "Velador", "id": "a", "key": "b", "ip": "1.2.3.4"}]
    )
    declaraciones = {d.name: d for d in lights.build_light_tools()[0].function_declarations}
    assert set(declaraciones) == {lights.ESP_TOOL, lights.TUYA_TOOL}
    assert declaraciones[lights.TUYA_TOOL].parameters.properties["lampara"].enum == ["velador"]
    assert "velador" in lights.tools_prompt()


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
