"""Empaqueta los modelos de voz de esp-sr y los suma a lo que se flashea.

Hacen falta dos cosas que PlatformIO no hace solo:

1. Generar srmodels.bin. esp-sr crea el objetivo `srmodels_bin` con la marca ALL
   de CMake, o sea que se construye al pedir `all`. PlatformIO no pide `all`,
   pide objetivos puntuales, asi que el objetivo existe en build.ninja y nunca
   corre. Aca se invoca movemodel.py directamente, igual que hace su CMakeLists.

2. Flashearlo. esp-sr lo registra con esptool_py_flash_to_partition(flash ...),
   que es el objetivo `flash` de ESP-IDF; PlatformIO arma su propia llamada a
   esptool con la lista FLASH_EXTRA_IMAGES. Sin esto la particion "model" queda
   vacia y esp_srmodel_init() no encuentra nada en tiempo de ejecucion.

El offset sale de partitions.csv y no esta escrito a mano, para que mover la
particion no rompa el flasheo en silencio.
"""

import csv
import os
import subprocess
import sys

Import("env")  # noqa: F821  (lo inyecta PlatformIO)


PARTICION = "model"
COMPONENTE = os.path.join("managed_components", "espressif__esp-sr")


def offset_de_la_particion(ruta_csv, nombre):
    with open(ruta_csv, newline="", encoding="utf-8") as tabla:
        for fila in csv.reader(tabla):
            if not fila or fila[0].strip().startswith("#"):
                continue
            if fila[0].strip() == nombre:
                return fila[3].strip()
    return None


def empaquetar_modelos(proyecto, build_dir):
    guion = os.path.join(proyecto, COMPONENTE, "model", "movemodel.py")
    sdkconfig = os.path.join(proyecto, "sdkconfig.%s" % env["PIOENV"])  # noqa: F821
    if not os.path.isfile(guion) or not os.path.isfile(sdkconfig):
        print("[srmodels] falta movemodel.py o el sdkconfig; no empaqueto")
        return

    # movemodel.py termina imprimiendo una linea decorativa con caracteres
    # Unicode. En una consola cp1252 eso lanza UnicodeEncodeError DESPUES de
    # escribir el binario, asi que el modelo queda bien pero el proceso vuelve
    # con error y frena la compilacion.
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8")
    resultado = subprocess.run(
        [sys.executable, guion, "-d1", sdkconfig,
         "-d2", os.path.join(proyecto, COMPONENTE), "-d3", build_dir],
        env=entorno, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if resultado.returncode != 0:
        print("[srmodels] movemodel.py fallo:\n%s" % resultado.stderr[-800:])


def main():
    proyecto = env.subst("$PROJECT_DIR")  # noqa: F821
    build_dir = env.subst("$BUILD_DIR")  # noqa: F821

    ruta_csv = os.path.join(proyecto, "partitions.csv")
    offset = offset_de_la_particion(ruta_csv, PARTICION) if os.path.isfile(ruta_csv) else None
    if not offset:
        print("[srmodels] no hay particion 'model' en partitions.csv; nada que flashear")
        return

    imagen = os.path.join(build_dir, "srmodels", "srmodels.bin")
    env.Append(FLASH_EXTRA_IMAGES=[(offset, imagen)])  # noqa: F821

    def antes_de_flashear(source, target, env):  # noqa: ARG001
        empaquetar_modelos(proyecto, build_dir)
        if os.path.isfile(imagen):
            print("[srmodels] %s -> %s (%d bytes)"
                  % (offset, imagen, os.path.getsize(imagen)))
        else:
            print("[srmodels] ATENCION: no se genero srmodels.bin, la wake word no va a arrancar")

    # El binario tiene que existir antes de que esptool arme el flasheo.
    env.AddPreAction("upload", antes_de_flashear)  # noqa: F821
    # Y tambien al compilar sin flashear, para que un `pio run` deje todo listo.
    env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", antes_de_flashear)  # noqa: F821


main()
