#pragma once

#include <Arduino.h>

// Consulta /ota/manifest y, si el backend publicó un build mayor al compilado,
// descarga el binario, verifica su SHA-256 y reinicia con el firmware nuevo.
// Debe llamarse en setup(), con Wi-Fi ya conectado y antes de arrancar las
// tareas que usan la pantalla.
void checkForFirmwareUpdate();
