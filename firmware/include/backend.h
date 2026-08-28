#pragma once

// WiFi.h va primero a proposito: con Arduino compilado como componente de IDF,
// las cabeceras de red del core (HTTPClient, WiFiManager) no arrastran solas
// las definiciones de la libreria Network de la que dependen. Si entran antes
// que WiFi.h, el build falla con NetworkClient y arduino_event_id_t sin declarar.
#include <WiFi.h>

#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFiClient.h>

// La URL es un nombre del tailnet (http://octopi:8000/...) o una IP de la LAN.
// El firmware habla HTTP plano: adentro del tailnet el cifrado lo pone
// WireGuard, y para eso no hace falta TLS arriba.
constexpr size_t SERVER_URL_SIZE = 192;
constexpr size_t DEVICE_TOKEN_SIZE = 96;

extern char server_url[SERVER_URL_SIZE];
extern char device_token[DEVICE_TOKEN_SIZE];

// Partes de server_url ya resueltas, con el puerto 80 por defecto.
// Host tal cual quedo configurado, sin resolver: sirve para mostrarlo.
String backendHost();
uint16_t backendPort();
String backendPath();

// Origen del backend (esquema + host + puerto) para armar /device/... y /ota/...
// Dentro del tailnet el host viaja ya resuelto a su IP 100.x, porque el ESP32 no
// tiene forma de preguntarle MagicDNS a un servidor DNS.
String backendBaseUrl();

// URL del endpoint de voz, con el mismo host resuelto que backendBaseUrl().
String backendVoiceUrl();

// Socket compartido para todos los pedidos.
WiFiClient& backendTransport();

// El transporte es compartido, y la tarea de mantenimiento corre en paralelo al
// bucle que manda el audio. Hay que tomar el lock antes de usar la red.
// timeoutMs == 0 devuelve false en vez de esperar.
void initBackend();
bool backendLock(uint32_t timeoutMs);
void backendUnlock();

// Cierra el socket. HTTPClient no siempre lo hace: si el servidor ya cerró la
// conexión, se saltea el stop() y el descriptor queda abierto.
void backendDisconnect();

// Abre un pedido con HTTPClient sobre ese transporte, ya con X-Device-Token, y
// se asegura de que el tunel del tailnet este levantado si corresponde.
bool beginBackendRequest(HTTPClient& http, const String& url);

// Escribe la cabecera del token en un pedido HTTP armado a mano.
void writeDeviceTokenHeader(WiFiClient& client);

// /config.txt: la URL, el token y la auth key del tailnet, una por linea.
void loadBackendConfig();
void saveBackendConfig();
