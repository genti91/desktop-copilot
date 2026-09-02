"""Control de luces para el asistente de voz, con function calling manual.

El modelo hace UNA sola llamada. Si en la respuesta viene un `function_call`,
`main.py` lo ejecuta acá mismo y habla una confirmación breve: no hay segunda
llamada al modelo. Ver la nota en `voice_assistant`.

Dos destinos, misma interfaz para el modelo:

- ``controlar_asistente_escritorio``: el LED RGB, el filamento y el apagado del
  propio asistente ESP32. No se le habla acá: se arma la orden en la
  mini-sintaxis que ya entiende ``commands.cpp`` (``LED_RGB:r,g,b``,
  ``LED_BRIGHTNESS:v``, ``FILAMENT_ON/OFF``, ``ALL_OFF``) y ``main.py`` la manda
  en la cabecera ``X-Action`` de la respuesta de voz.
- ``controlar_lampara_habitacion``: las lámparas Tuya de la habitación, por LAN,
  con tinytuya. El Pi las alcanza directo.
"""

from typing import Optional

from google.genai import types

from . import config

try:  # tinytuya sólo hace falta en el Pi; los tests y el arranque no deben depender de él.
    import tinytuya
except ImportError:  # pragma: no cover - depende del entorno
    tinytuya = None


ESP_TOOL = "controlar_asistente_escritorio"
TUYA_TOOL = "controlar_lampara_habitacion"

# nombre normalizado -> especificación cruda del .env
_lamparas: dict[str, dict] = {}
# nombre normalizado -> instancia tinytuya reutilizada (socket persistente)
_bulbs: dict[str, object] = {}
# nombres para el enum de la función y el prompt
LAMP_NAMES: list[str] = []


def registrar_lamparas(lamparas: list[dict]) -> None:
    """Rearma el catálogo de lámparas Tuya. Idempotente; los tests la reusan."""
    global LAMP_NAMES
    _lamparas.clear()
    _bulbs.clear()
    for especificacion in lamparas or []:
        nombre = str(especificacion.get("nombre", "")).strip().lower()
        if nombre and especificacion.get("id") and especificacion.get("key"):
            _lamparas[nombre] = especificacion
    LAMP_NAMES = sorted(_lamparas)


registrar_lamparas(config.TUYA_LAMPS)


# --------------------------------------------------------------------------- #
# Declaraciones que ve el modelo
# --------------------------------------------------------------------------- #


def _esp_declaration() -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=ESP_TOOL,
        description=(
            "Controla las luces del propio asistente de escritorio: el LED RGB, "
            "la lámpara de filamento y el apagado general. Usar sólo si lo piden."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "color_rgb": types.Schema(
                    type=types.Type.STRING,
                    description='Color del LED RGB como "R,G,B", cada canal de 0 a 255. Ej: "255,0,0" es rojo.',
                ),
                "brillo": types.Schema(
                    type=types.Type.INTEGER,
                    description="Brillo del LED RGB, de 0 a 100 (porcentaje).",
                ),
                "filamento": types.Schema(
                    type=types.Type.STRING,
                    enum=["encender", "apagar"],
                    description="Prende o apaga la lámpara de filamento.",
                ),
                "apagar_todo": types.Schema(
                    type=types.Type.BOOLEAN,
                    description="Si es true, apaga LED, filamento y pantalla hasta el próximo toque del sensor.",
                ),
            },
        ),
    )


def _tuya_declaration(nombres: list[str]) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=TUYA_TOOL,
        description="Prende, apaga o cambia el color y el brillo de las lámparas de la habitación.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            required=["lampara"],
            properties={
                "lampara": types.Schema(
                    type=types.Type.STRING,
                    enum=nombres,
                    description="Qué lámpara de la habitación tocar.",
                ),
                "encender": types.Schema(
                    type=types.Type.BOOLEAN,
                    description="true para prender, false para apagar. Omitir si sólo se cambia color o brillo.",
                ),
                "color_rgb": types.Schema(
                    type=types.Type.STRING,
                    description='Color como "R,G,B", cada canal de 0 a 255.',
                ),
                "brillo": types.Schema(
                    type=types.Type.INTEGER,
                    description="Brillo de 1 a 100 (porcentaje).",
                ),
            },
        ),
    )


def light_declarations() -> list[types.FunctionDeclaration]:
    """Las declaraciones de luces. La de Tuya sólo si hay lámparas configuradas."""
    declaraciones = [_esp_declaration()]
    if LAMP_NAMES:
        declaraciones.append(_tuya_declaration(LAMP_NAMES))
    return declaraciones


def build_light_tools() -> list[types.Tool]:
    """Las tools para pasarle a generate_content. La de Tuya sólo si hay lámparas."""
    return [types.Tool(function_declarations=light_declarations())]


def tools_prompt() -> str:
    """Bloque para el system prompt: qué funciones hay y cuándo usarlas."""
    lineas = [
        "[CONTROL DE LUCES]",
        "Tenés funciones para las luces. Llamalas SÓLO si te lo piden explícitamente:",
        f"- {ESP_TOOL}: LED RGB, filamento y apagado del asistente de escritorio.",
    ]
    if LAMP_NAMES:
        lineas.append(
            f"- {TUYA_TOOL}: las lámparas de la habitación ({', '.join(LAMP_NAMES)})."
        )
    lineas.append("Cuando llames una función, incluí igual una frase corta de confirmación hablada.")
    lineas.append("Si no te piden tocar las luces, no llames ninguna función.")
    return "\n".join(lineas)


# --------------------------------------------------------------------------- #
# Ejecución
# --------------------------------------------------------------------------- #


def _parse_rgb(valor) -> Optional[tuple[int, int, int]]:
    if not isinstance(valor, str):
        return None
    partes = valor.replace(" ", "").split(",")
    if len(partes) != 3:
        return None
    try:
        canales = [max(0, min(255, int(parte))) for parte in partes]
    except ValueError:
        return None
    return canales[0], canales[1], canales[2]


def _unir(fragmentos: list[str]) -> str:
    utiles = [fragmento for fragmento in fragmentos if fragmento]
    if not utiles:
        return ""
    if len(utiles) == 1:
        return utiles[0]
    return ", ".join(utiles[:-1]) + " y " + utiles[-1]


def confirmacion_por_defecto(fragmentos: list[str]) -> str:
    """Frase hablada cuando el modelo llamó una función pero no dijo nada."""
    frase = _unir(fragmentos)
    if not frase:
        return "Listo."
    return frase[0].upper() + frase[1:] + "."


def _bulb(nombre: str):
    if nombre in _bulbs:
        return _bulbs[nombre]
    especificacion = _lamparas.get(nombre)
    if especificacion is None or tinytuya is None:
        return None
    bulb = tinytuya.BulbDevice(
        dev_id=especificacion["id"],
        address=especificacion.get("ip"),
        local_key=especificacion["key"],
        version=float(especificacion.get("version", 3.4)),
    )
    bulb.set_socketTimeout(3)
    bulb.set_socketPersistent(True)
    _bulbs[nombre] = bulb
    return bulb


def _aplicar_esp(args: dict, pending_esp: list[str]) -> str:
    hechos: list[str] = []

    rgb = _parse_rgb(args.get("color_rgb"))
    if rgb:
        pending_esp.append(f"LED_RGB:{rgb[0]},{rgb[1]},{rgb[2]}")
        hechos.append("cambié el color")

    if args.get("brillo") is not None:
        try:
            porcentaje = max(0, min(100, int(args["brillo"])))
            pending_esp.append(f"LED_BRIGHTNESS:{round(porcentaje * 255 / 100)}")
            hechos.append("ajusté el brillo")
        except (TypeError, ValueError):
            pass

    filamento = args.get("filamento")
    if filamento == "encender":
        pending_esp.append("FILAMENT_ON")
        hechos.append("prendí el filamento")
    elif filamento == "apagar":
        pending_esp.append("FILAMENT_OFF")
        hechos.append("apagué el filamento")

    if args.get("apagar_todo"):
        pending_esp.append("ALL_OFF")
        hechos.append("apagué todo")

    return _unir(hechos)


def _aplicar_tuya(args: dict) -> str:
    nombre = str(args.get("lampara", "")).strip().lower()
    bulb = _bulb(nombre)
    if bulb is None:
        return f"no encontré la lámpara {nombre or 'que me pediste'}"

    encender = args.get("encender")
    rgb = _parse_rgb(args.get("color_rgb"))
    brillo = args.get("brillo")

    try:
        if encender is False and rgb is None and brillo is None:
            bulb.turn_off()
            return f"apagué la lámpara {nombre}"

        if encender is not False:
            bulb.turn_on()
        if rgb:
            bulb.set_colour(*rgb)
        if brillo is not None:
            bulb.set_brightness_percentage(max(1, min(100, int(brillo))))
    except Exception as error:  # noqa: BLE001 - la lámpara puede estar offline
        print(f"[Tuya] {nombre}: {type(error).__name__}: {error}")
        return f"no pude conectar con la lámpara {nombre}"

    if encender and rgb is None and brillo is None:
        return f"prendí la lámpara {nombre}"
    return f"listo con la lámpara {nombre}"


def aplicar_accion_luz(name: str, args: dict, pending_esp: list[str]) -> str:
    """Ejecuta un function_call del modelo. Devuelve una frase de confirmación."""
    if name == ESP_TOOL:
        return _aplicar_esp(args or {}, pending_esp)
    if name == TUYA_TOOL:
        return _aplicar_tuya(args or {})
    print(f"[Luces] función desconocida: {name}")
    return ""
