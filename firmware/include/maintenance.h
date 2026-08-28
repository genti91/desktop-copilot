#pragma once

#include <Arduino.h>

// El sondeo de configuración y el chequeo de OTA corren en su propia tarea.
//
// Cuando vivían en loop(), cada petición HTTP bloqueaba el bucle entero: no se
// leía el sensor táctil ni se alimentaba el decodificador de MP3 mientras
// duraba. Son decenas de milisegundos por pedido, pero el OTA descarga un
// binario entero y levantar el tunel del tailnet puede tardar segundos: ahí se
// empiezan a perder toques y a cortarse el audio.
void startMaintenanceTask();
