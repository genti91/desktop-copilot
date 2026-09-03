#pragma once

// La versión es semántica y se edita a mano; es lo que se muestra en pantalla
// y lo que nombra el release de GitHub.
#define FIRMWARE_VERSION "1.3.0"

// El build lo inyecta scripts/build_number.py a partir de la cantidad de
// commits, tanto en local como en CI. El fallback sólo aplica si se compila
// fuera de PlatformIO o sin git.
#ifndef FIRMWARE_BUILD
#define FIRMWARE_BUILD 0
#endif
