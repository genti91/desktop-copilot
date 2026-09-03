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
- ``controlar_lampara_habitacion``: las lámparas de la habitación. Cada lámpara
  es Tuya o WiZ, pero para el modelo son iguales: sólo elige el nombre.
    * Tuya: la controla el Pi por LAN con tinytuya (tiene que estar en la misma
      red que el Pi).
    * WiZ: la controla el ESP. El backend arma un comando ``WIZ:<ip>:<params>``
      y lo manda en ``X-Action``; el ESP —que sí está en la red de la lámpara—
      le manda el paquete UDP (protocolo WiZ, puerto 38899).
  Cada lámpara puede limitarse a ciertos equipos con su campo ``equipos``.
"""

from typing import Optional

from google.genai import types

from . import config

try:  # tinytuya sólo hace falta en el Pi; los tests y el arranque no deben depender de él.
    import tinytuya
except ImportError:  # pragma: no cover - depende del entorno
    tinytuya = None


ESP_TOOL = "controlar_asistente_escritorio"
LAMP_TOOL = "controlar_lampara_habitacion"

# nombre normalizado -> especificación normalizada (incluye "tipo" y "equipos")
_lamparas: dict[str, dict] = {}
# nombre -> instancia tinytuya reutilizada (socket persistente). WiZ no cachea:
# es UDP sin estado y crear el objeto es barato.
_bulbs: dict[str, object] = {}
# todos los nombres registrados (haya o no filtro por equipo)
LAMP_NAMES: list[str] = []


def _normalize(especificacion: dict, tipo: str) -> Optional[dict]:
    nombre = str(especificacion.get("nombre", "")).strip().lower()
    if not nombre:
        return None
    if tipo == "tuya" and not (especificacion.get("id") and especificacion.get("key")):
        return None
    if tipo == "wiz" and not especificacion.get("ip"):
        return None
    equipos = especificacion.get("equipos")
    return {
        **especificacion,
        "nombre": nombre,
        "tipo": tipo,
        "equipos": {str(e).strip().lower() for e in equipos} if equipos else None,
    }


def registrar_lamparas(tuya: list[dict], wiz: Optional[list[dict]] = None) -> None:
    """Rearma el catálogo de lámparas. Idempotente; los tests la reusan."""
    global LAMP_NAMES
    _lamparas.clear()
    _bulbs.clear()
    for tipo, lista in (("tuya", tuya), ("wiz", wiz or [])):
        for especificacion in lista or []:
            normalizada = _normalize(especificacion, tipo)
            if normalizada:
                _lamparas[normalizada["nombre"]] = normalizada
    LAMP_NAMES = sorted(_lamparas)


registrar_lamparas(config.TUYA_LAMPS, config.WIZ_LAMPS)


def lamp_names_for(device: str) -> list[str]:
    """Lámparas que puede tocar `device`: las sin filtro y las que lo listan."""
    device = (device or "").strip().lower()
    return sorted(
        nombre
        for nombre, spec in _lamparas.items()
        if not spec["equipos"] or device in spec["equipos"]
    )


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


def _lamp_declaration(nombres: list[str]) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=LAMP_TOOL,
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


def light_declarations(device: str = "", lamps_enabled: bool = True) -> list[types.FunctionDeclaration]:
    """Las declaraciones de luces. La de las lámparas sólo si el equipo tiene
    permiso (`lamps_enabled`) y hay al menos una lámpara a su alcance."""
    declaraciones = [_esp_declaration()]
    nombres = lamp_names_for(device) if lamps_enabled else []
    if nombres:
        declaraciones.append(_lamp_declaration(nombres))
    return declaraciones


def build_light_tools(device: str = "", lamps_enabled: bool = True) -> list[types.Tool]:
    """Las tools para pasarle a generate_content."""
    return [types.Tool(function_declarations=light_declarations(device, lamps_enabled))]


def tools_prompt(device: str = "", lamps_enabled: bool = True) -> str:
    """Bloque para el system prompt: qué funciones hay y cuándo usarlas."""
    lineas = [
        "[CONTROL DE LUCES]",
        "Tenés funciones para las luces. Llamalas SÓLO si te lo piden explícitamente:",
        f"- {ESP_TOOL}: LED RGB, filamento y apagado del asistente de escritorio.",
    ]
    nombres = lamp_names_for(device) if lamps_enabled else []
    if nombres:
        lineas.append(
            f"- {LAMP_TOOL}: las lámparas de la habitación ({', '.join(nombres)})."
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


def _frase_lampara(nombre: str, encender, rgb, brillo) -> str:
    if encender is False and rgb is None and brillo is None:
        return f"apagué la lámpara {nombre}"
    if encender and rgb is None and brillo is None:
        return f"prendí la lámpara {nombre}"
    return f"listo con la lámpara {nombre}"


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


# --- Tuya (tinytuya, TCP) --------------------------------------------------- #


def _bulb(nombre: str):
    if nombre in _bulbs:
        return _bulbs[nombre]
    especificacion = _lamparas.get(nombre)
    if especificacion is None or especificacion.get("tipo") != "tuya" or tinytuya is None:
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


def _aplicar_tuya(nombre: str, args: dict) -> str:
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

    return _frase_lampara(nombre, encender, rgb, brillo)


# --- WiZ (comando UDP que ejecuta el ESP) -------------------------------- #

WIZ_PORT = 38899


def _wiz_command(ip: str, args: dict) -> str:
    """Arma ``WIZ:<ip>:<k=v,...>`` para la cabecera X-Action. El ESP lo traduce
    a un ``{"method":"setPilot","params":{...}}`` y lo manda por UDP."""
    encender = args.get("encender")
    rgb = _parse_rgb(args.get("color_rgb"))
    brillo = args.get("brillo")

    if encender is False and rgb is None and brillo is None:
        params = ["state=0"]
    else:
        params = ["state=1"]
        if rgb:
            params += [f"r={rgb[0]}", f"g={rgb[1]}", f"b={rgb[2]}"]
        if brillo is not None:
            try:
                params.append(f"dimming={max(10, min(100, int(brillo)))}")
            except (TypeError, ValueError):
                pass
    return f"WIZ:{ip}:{','.join(params)}"


def _aplicar_wiz(nombre: str, especificacion: dict, args: dict, pending_esp: list[str]) -> str:
    ip = especificacion.get("ip")
    if not ip:
        return f"no encontré la lámpara {nombre}"
    pending_esp.append(_wiz_command(ip, args))
    return _frase_lampara(
        nombre, args.get("encender"), _parse_rgb(args.get("color_rgb")), args.get("brillo")
    )


def _aplicar_lampara(args: dict, pending_esp: list[str]) -> str:
    nombre = str(args.get("lampara", "")).strip().lower()
    especificacion = _lamparas.get(nombre)
    if especificacion is None:
        return f"no encontré la lámpara {nombre or 'que me pediste'}"
    if especificacion["tipo"] == "wiz":
        return _aplicar_wiz(nombre, especificacion, args, pending_esp)
    return _aplicar_tuya(nombre, args)


def aplicar_accion_luz(
    name: str,
    args: dict,
    pending_esp: list[str],
    device: str = "",
    lamps_enabled: bool = True,
) -> str:
    """Ejecuta un function_call del modelo. Devuelve una frase de confirmación."""
    if name == ESP_TOOL:
        return _aplicar_esp(args or {}, pending_esp)
    if name == LAMP_TOOL:
        # Defensa: el equipo sin permiso (o sin esa lámpara a su alcance) ni
        # siquiera recibe la declaración; si un modelo la inventa igual, una
        # lámpara real que no le corresponde no se toca. Una lámpara inexistente
        # sí pasa, para que _aplicar_lampara conteste "no encontré…".
        args = args or {}
        nombre = str(args.get("lampara", "")).strip().lower()
        if nombre in _lamparas and not (lamps_enabled and nombre in lamp_names_for(device)):
            print(f"[Luces] {nombre} fuera del alcance de {device or 'sin equipo'}; se ignora.")
            return ""
        return _aplicar_lampara(args, pending_esp)
    print(f"[Luces] función desconocida: {name}")
    return ""
