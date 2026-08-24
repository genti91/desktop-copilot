#pragma once

#include <Arduino.h>

void sendAudioAndPlayResponse(size_t recordedPcmBytes);

// Vuelve a arrancar la reproduccion del mismo archivo ya guardado, sin tocar la
// red. Devuelve false si ya se reintento o si no se pudo reabrir.
bool retryPlayback();
