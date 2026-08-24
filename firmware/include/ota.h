#pragma once

#include <Arduino.h>

// Cada cuánto se vuelve a preguntar por firmware nuevo mientras el dispositivo
// está en reposo. El backend espeja los releases de GitHub, así que con esto
// alcanza para que un push termine aplicándose solo.
constexpr uint32_t OTA_CHECK_INTERVAL_MS = 15UL * 60UL * 1000UL;

// Consulta /ota/manifest y, si el backend publicó un build mayor al compilado,
// descarga el binario, verifica su SHA-256 y reinicia con el firmware nuevo.
void checkForFirmwareUpdate();

// Versión con intervalo, para llamar desde loop() cuando no hay interacción.
void updateFirmwareCheck();
