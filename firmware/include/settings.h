#pragma once

#include <Arduino.h>

// Ruta en LittleFS de la imagen de reposo ya convertida a RGB565 por el backend.
constexpr const char* IDLE_IMAGE_PATH = "/idle565.raw";
constexpr const char* SETTINGS_PATH = "/settings.json";
// Un sondeo es un GET sobre HTTP plano, en la LAN o por el tunel de WireGuard:
// en los dos casos son decenas de milisegundos. Antes habia un intervalo largo
// aparte para el caso remoto, cuando cada sondeo arrastraba un handshake TLS.
constexpr uint32_t SETTINGS_POLL_INTERVAL_MS = 5000;
constexpr uint32_t SETTINGS_RETRY_INTERVAL_MS = 60000;

// Aplica lo último que se guardó en flash (sirve para arrancar sin backend).
void initDeviceSettings();

// Consulta /device/config y aplica los cambios si subió la revisión.
bool refreshDeviceSettings(bool force);

// Versión con intervalo, la llama la tarea de mantenimiento.
void updateDeviceSettings();
