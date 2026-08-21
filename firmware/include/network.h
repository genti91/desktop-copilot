#pragma once

#include <Arduino.h>

void sendAudioAndPlayResponse(size_t recordedPcmBytes);

// Origen del backend (esquema + host + puerto) derivado de la URL guardada en
// /config.txt, para poder pegarle a /device/... y /ota/... sin duplicar config.
String backendBaseUrl();
