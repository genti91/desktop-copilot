#pragma once

// Se llamaba network.h. Arduino 3.x trae una libreria Network con su propio
// Network.h, y en Windows —donde el sistema de archivos no distingue
// mayusculas— el include del core terminaba resolviendo contra este archivo,
// porque include/ va primero en la ruta de busqueda.

#include <Arduino.h>

// Manda la grabacion al backend y arranca la reproduccion de la respuesta.
// Devuelve true solo si el equipo efectivamente contesto: false si fallo la red
// o si el backend descarto la grabacion por ruido (204). Quien llama lo usa para
// decidir si abre la ventana de seguimiento o vuelve a esperar "Jarvis".
bool sendAudioAndPlayResponse(size_t recordedPcmBytes);

// Vuelve a arrancar la reproduccion del mismo archivo ya guardado, sin tocar la
// red. Devuelve false si ya se reintento o si no se pudo reabrir.
bool retryPlayback();
