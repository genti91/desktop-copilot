#pragma once

#include <Arduino.h>

// Ruta en LittleFS de la imagen de reposo ya convertida a RGB565 por el backend.
constexpr const char* IDLE_IMAGE_PATH = "/idle565.raw";
constexpr const char* SETTINGS_PATH = "/settings.json";
constexpr uint32_t SETTINGS_POLL_INTERVAL_MS = 5000;
// Por el tunel cada sondeo es un handshake TLS contra un endpoint publico:
// mucho mas caro que un GET en la LAN, y la configuracion cambia poco.
constexpr uint32_t SETTINGS_REMOTE_POLL_INTERVAL_MS = 30000;
constexpr uint32_t SETTINGS_RETRY_INTERVAL_MS = 60000;

// Aplica lo último que se guardó en flash (sirve para arrancar sin backend).
void initDeviceSettings();

// Consulta /device/config y aplica los cambios si subió la revisión.
bool refreshDeviceSettings(bool force);

// Versión con intervalo, la llama la tarea de mantenimiento.
void updateDeviceSettings();
