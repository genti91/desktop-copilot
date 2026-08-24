#pragma once

#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFiClient.h>

// La URL puede ser un https:// de Tailscale Funnel, mas larga que una IP local.
constexpr size_t SERVER_URL_SIZE = 192;
constexpr size_t DEVICE_TOKEN_SIZE = 96;

extern char server_url[SERVER_URL_SIZE];
extern char device_token[DEVICE_TOKEN_SIZE];

// Partes de server_url ya resueltas, con el puerto por defecto del esquema.
bool backendUsesTls();
String backendHost();
uint16_t backendPort();
String backendPath();

// Origen del backend (esquema + host + puerto) para armar /device/... y /ota/...
String backendBaseUrl();

// Transporte segun el esquema: WiFiClient plano, o WiFiClientSecure con el root
// CA de Let's Encrypt, que es quien firma los certificados de Funnel.
WiFiClient& backendTransport();

// El transporte es compartido, y la tarea de mantenimiento corre en paralelo al
// bucle que manda el audio. Hay que tomar el lock antes de usar la red.
// timeoutMs == 0 devuelve false en vez de esperar.
void initBackend();
bool backendLock(uint32_t timeoutMs);
void backendUnlock();

// Cierra el socket y, con TLS, libera el contexto de mbedtls. HTTPClient no
// siempre lo hace: si el servidor ya cerró la conexión, se saltea el stop() y
// el contexto queda ocupando decenas de KB de heap.
void backendDisconnect();

// Abre un pedido con HTTPClient sobre ese transporte, ya con X-Device-Token.
bool beginBackendRequest(HTTPClient& http, const String& url);

// Escribe la cabecera del token en un pedido HTTP armado a mano.
void writeDeviceTokenHeader(WiFiClient& client);

// /config.txt: primera linea la URL, segunda el token (opcional).
void loadBackendConfig();
void saveBackendConfig();
