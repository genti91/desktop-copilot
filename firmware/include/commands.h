#pragma once

#include <Arduino.h>

void initDeviceOutputs();
void executeDeviceCommand(String command);
void wakeDeviceOutputs();

// Estado configurable desde el backend. Cada salida se prende y se apaga por
// separado; applyDeviceOutputs() vuelca ese estado al hardware.
void setRgbEnabled(bool enabled);
void setRgbColor(uint8_t red, uint8_t green, uint8_t blue);
void setRgbBrightness(uint8_t brightness);
void setFilamentEnabled(bool enabled);
void setDisplayEnabled(bool enabled);
void applyDeviceOutputs();
