#pragma once

#include <Arduino.h>

// La auth key de Tailscale es larga (tskey-auth-<id>-<secreto>).
constexpr size_t TAILNET_AUTH_KEY_SIZE = 128;

// Vive en tailnet.cpp, la persiste backend.cpp junto al resto de /config.txt.
extern char tailnet_auth_key[TAILNET_AUTH_KEY_SIZE];

// Hay auth key cargada: es lo que decide si el firmware entra al tailnet o
// sigue hablando con el backend por la URL publica de siempre.
bool tailnetEnabled();

// Arranca MicroLink y espera hasta timeoutMs a que el control plane conteste y
// asigne la IP 100.x. El Wi-Fi ya tiene que estar conectado.
bool startTailnet(uint32_t timeoutMs);

bool tailnetConnected();

// IP del dispositivo dentro del tailnet, o "" si todavia no la asignaron.
String tailnetVpnIp();

// MagicDNS resuelto contra la tabla de peers, sin salir a la red. Un host que
// ya es una IP vuelve tal cual. Devuelve "" si el nombre todavia no llego en el
// MapResponse.
String tailnetResolve(const String& hostname);

// Deja el tunel WireGuard listo contra ese peer. Sin esto el primer connect()
// despues de un rato de silencio falla con EHOSTUNREACH: lwIP rutea 100.64/10
// por el netif de WireGuard, pero sin sesion viva no hay por donde salir.
// Se saltea sola si el tunel se uso hace poco.
void tailnetEnsurePeer(const String& ip, uint16_t port);
