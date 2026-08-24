#pragma once

#include <Arduino.h>

// El sondeo de configuración y el chequeo de OTA corren en su propia tarea.
//
// Cuando vivían en loop(), cada petición HTTP bloqueaba el bucle entero: no se
// leía el sensor táctil ni se alimentaba el decodificador de MP3 mientras
// duraba. Con la URL de la LAN eran decenas de milisegundos; a través de
// Tailscale Funnel es un handshake TLS completo cada 5 segundos, y ahí se
// empiezan a perder toques y a cortarse el audio.
void startMaintenanceTask();
