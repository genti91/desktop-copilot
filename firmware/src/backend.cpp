// WiFi.h primero: ver la nota en backend.h sobre el orden de los includes.
#include <WiFi.h>

#include <LittleFS.h>
#include "backend.h"
#include "tailnet.h"

char server_url[SERVER_URL_SIZE] = "http://octopi:8000/voice-assistant";
// Vacio a proposito: el token es un secreto y este repo es publico. Se carga
// desde /config.txt, que se completa una vez desde el portal cautivo y
// sobrevive a los flasheos porque LittleFS no se borra al subir firmware.
// Lo mismo vale para la auth key del tailnet, que vive en tailnet.cpp.
char device_token[DEVICE_TOKEN_SIZE] = "";
// Vacio por defecto: sin nombre no se pueden hacer videollamadas, pero todo lo
// demas anda igual. Se completa una vez desde el portal.
char device_name[DEVICE_NAME_SIZE] = "";

namespace {

constexpr const char* CONFIG_PATH = "/config.txt";

WiFiClient plainClient;
SemaphoreHandle_t backendMutex = NULL;

// El firmware habla HTTP plano y nada mas: adentro del tailnet cifra WireGuard,
// y en la LAN nunca hizo falta. Un https:// configurado a mano se normaliza a
// http:// en vez de fallar callado.
String normalizedUrl() {
  String url = String(server_url);
  url.trim();
  if (url.startsWith("https://")) url = "http://" + url.substring(8);
  if (!url.startsWith("http://")) url = "http://" + url;
  return url;
}

String authority() {
  String url = normalizedUrl();
  int schemeEnd = url.indexOf("://") + 3;
  int pathStart = url.indexOf('/', schemeEnd);
  return pathStart > 0 ? url.substring(schemeEnd, pathStart) : url.substring(schemeEnd);
}

// Se cachea porque resolver es mirar la tabla de peers, y la tabla recien esta
// completa un rato despues de conectar: un resultado vacio no se guarda, asi el
// proximo pedido lo vuelve a intentar.
String resolvedHost;

// Host al que hay que abrir el socket. Fuera del tailnet es el configurado; con
// el tailnet arriba es su IP 100.x, porque el ESP32 no tiene MagicDNS.
String connectHost() {
  String host = backendHost();
  if (!tailnetEnabled()) return host;

  if (resolvedHost.length() == 0) {
    String resolved = tailnetResolve(host);
    if (resolved.length() == 0) return host;
    if (resolved != host) Serial.printf("🔗 %s resuelve a %s\n", host.c_str(), resolved.c_str());
    resolvedHost = resolved;
  }
  return resolvedHost;
}

}  // namespace

String backendHost() {
  String host = authority();
  int colonIndex = host.indexOf(':');
  return colonIndex > 0 ? host.substring(0, colonIndex) : host;
}

uint16_t backendPort() {
  String host = authority();
  int colonIndex = host.indexOf(':');
  if (colonIndex < 0) return 80;
  return host.substring(colonIndex + 1).toInt();
}

String backendPath() {
  String url = normalizedUrl();
  int schemeEnd = url.indexOf("://") + 3;
  int pathStart = url.indexOf('/', schemeEnd);
  return pathStart > 0 ? url.substring(pathStart) : "/voice-assistant";
}

String backendBaseUrl() {
  String origin = "http://" + connectHost();

  // El puerto se omite cuando es el 80, igual que hacia el recorte de la URL
  // configurada: hay servidores que miran el Host y no les cae bien uno de mas.
  uint16_t port = backendPort();
  if (port != 80) origin += ":" + String(port);

  return origin;
}

String backendVoiceUrl() {
  return backendBaseUrl() + backendPath();
}

String backendConnectHost() {
  return connectHost();
}

WiFiClient& backendTransport() {
  plainClient.stop();
  return plainClient;
}

void initBackend() {
  if (backendMutex == NULL) backendMutex = xSemaphoreCreateMutex();
}

bool backendLock(uint32_t timeoutMs) {
  if (backendMutex == NULL) return true;  // todavía no hay tareas compitiendo
  TickType_t ticks = timeoutMs == portMAX_DELAY ? portMAX_DELAY : pdMS_TO_TICKS(timeoutMs);
  return xSemaphoreTake(backendMutex, ticks) == pdTRUE;
}

void backendUnlock() {
  if (backendMutex != NULL) xSemaphoreGive(backendMutex);
}

void backendDisconnect() {
  plainClient.stop();
}

void writeDeviceTokenHeader(WiFiClient& client) {
  if (device_token[0] == 0) return;
  client.printf("X-Device-Token: %s\r\n", device_token);
}

bool beginBackendRequest(HTTPClient& http, const String& url) {
  // Antes de abrir el socket: sin sesion de WireGuard viva contra el peer, lwIP
  // rutea el 100.x por el netif del tunel y connect() vuelve con EHOSTUNREACH.
  // tailnetEnsurePeer() no hace nada si el tunel se uso hace poco.
  if (tailnetEnabled()) tailnetEnsurePeer(connectHost(), backendPort());

  if (!http.begin(backendTransport(), url)) return false;
  if (device_token[0] != 0) http.addHeader("X-Device-Token", device_token);
  return true;
}

void loadBackendConfig() {
  if (!LittleFS.exists(CONFIG_PATH)) return;

  File configFile = LittleFS.open(CONFIG_PATH, "r");
  if (!configFile) return;

  String loadedUrl = configFile.readStringUntil('\n');
  loadedUrl.trim();
  if (loadedUrl.length() > 0) {
    strlcpy(server_url, loadedUrl.c_str(), sizeof(server_url));
    Serial.printf("URL cargada desde flash: %s\n", server_url);
  }

  String loadedToken = configFile.readStringUntil('\n');
  loadedToken.trim();
  strlcpy(device_token, loadedToken.c_str(), sizeof(device_token));
  if (device_token[0] != 0) Serial.println("Token del dispositivo cargado desde flash.");

  String loadedAuthKey = configFile.readStringUntil('\n');
  loadedAuthKey.trim();
  strlcpy(tailnet_auth_key, loadedAuthKey.c_str(), sizeof(tailnet_auth_key));
  if (tailnet_auth_key[0] != 0) Serial.println("Auth key del tailnet cargada desde flash.");

  // 4ta linea: agregada con la videollamada. Los /config.txt viejos no la traen
  // y readStringUntil devuelve "" sin romper nada.
  String loadedName = configFile.readStringUntil('\n');
  loadedName.trim();
  strlcpy(device_name, loadedName.c_str(), sizeof(device_name));
  if (device_name[0] != 0) Serial.printf("Nombre del equipo: %s\n", device_name);

  configFile.close();
}

void saveBackendConfig() {
  File configFile = LittleFS.open(CONFIG_PATH, "w");
  if (!configFile) {
    Serial.println("No pude guardar la configuracion del backend.");
    return;
  }
  configFile.println(server_url);
  configFile.println(device_token);
  configFile.println(tailnet_auth_key);
  configFile.println(device_name);
  configFile.close();
  Serial.println("Configuracion del backend guardada en LittleFS.");
}
