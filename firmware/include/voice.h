#pragma once

// Se llamaba network.h. Arduino 3.x trae una libreria Network con su propio
// Network.h, y en Windows —donde el sistema de archivos no distingue
// mayusculas— el include del core terminaba resolviendo contra este archivo,
// porque include/ va primero en la ruta de busqueda.

#include <Arduino.h>

void sendAudioAndPlayResponse(size_t recordedPcmBytes);

// Vuelve a arrancar la reproduccion del mismo archivo ya guardado, sin tocar la
// red. Devuelve false si ya se reintento o si no se pudo reabrir.
bool retryPlayback();
