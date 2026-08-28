#include <Arduino.h>
#include <WiFi.h>
#include "backend.h"
#include "tailnet.h"

#include "esp_log.h"
#include "microlink.h"
#include "nvs.h"

char tailnet_auth_key[TAILNET_AUTH_KEY_SIZE] = "";

namespace {

// Nombre con el que el dispositivo aparece en el admin panel de Tailscale.
constexpr const char* DEVICE_NAME = "desktop-copilot";

// WireGuard rota la clave de sesion cada 2 minutos de trafico y descarta la
// sesion despues de 3 minutos de silencio. Por debajo de eso el tunel sigue
// vivo y no hace falta volver a despertarlo.
constexpr uint32_t PEER_WARM_MS = 60000;

// Lo que tarda el handshake si el peer estaba dormido: DISCO tiene que hacer
// el agujero contra el otro extremo antes de que WireGuard cierre.
constexpr uint32_t PEER_WARM_TIMEOUT_MS = 15000;

// MicroLink registra cada DISCO, cada PONG y cada tick del keepalive. Sirve para
// diagnosticar el tunel, pero a ese ritmo tapa todo lo demas en el monitor.
// Subir a ESP_LOG_INFO cuando haya que mirar por que no levanta un peer; lo que
// importa en el dia a dia ya sale por los mensajes de este archivo.
constexpr esp_log_level_t TAILNET_LOG_LEVEL = ESP_LOG_ERROR;

// Los tags que usa el componente. Los printf crudos de wireguard_lwip
// (`[WG_TX]`, `[TAI64N]`) no pasan por esp_log y siguen saliendo igual.
const char* const MICROLINK_TAGS[] = {"ml_coord",   "ml_wg_mgr", "ml_derp",  "ml_net_io",
                                      "ml_stun",    "ml_tcp",    "ml_udp",   "ml_noise",
                                      "ml_h2",      "ml_zerocopy", "microlink"};

microlink_t* tailnetLink = NULL;
uint32_t lastWarmMs = 0;
bool everWarmed = false;

bool looksLikeIp(const String& host) {
  if (host.length() == 0) return false;
  for (size_t index = 0; index < host.length(); index++) {
    char character = host[index];
    if (!isdigit(character) && character != '.') return false;
  }
  return true;
}

void onStateChange(microlink_t* handle, microlink_state_t state, void* userData) {
  (void)userData;
  static const char* NAMES[] = {"inactivo",   "esperando Wi-Fi", "conectando", "registrando",
                                "conectado",  "reconectando",    "error"};
  const char* name =
      static_cast<size_t>(state) < (sizeof(NAMES) / sizeof(NAMES[0])) ? NAMES[state] : "desconocido";
  Serial.printf("🔗 Tailnet: %s\n", name);

  if (state == ML_STATE_CONNECTED) {
    char address[16];
    microlink_ip_to_str(microlink_get_vpn_ip(handle), address);
    Serial.printf("🔗 IP del tailnet: %s\n", address);
  }
}

}  // namespace

// La auth key es una credencial de alta, no de funcionamiento. MicroLink guarda
// la identidad del nodo —machine key, WireGuard y DISCO— en su propio namespace
// de NVS, y en el registro incluye la auth key solo si no esta vacia
// (ml_coord.c: `if (ml->config.auth_key && strlen(...) > 0)`). Un nodo ya dado
// de alta se vuelve a conectar con las claves guardadas.
//
// Esto dejo de ser teorico: al achicar la particion de LittleFS se perdio
// /config.txt con la auth key adentro, y el aparato quedo sin tailnet aunque su
// identidad seguia intacta en NVS, que esta en otra region y no se toco. Exigir
// la auth key para siempre convierte cualquier perdida del sistema de archivos
// en un re-alta manual.
bool tailnetHasStoredIdentity() {
  nvs_handle_t nvs;
  if (nvs_open("microlink", NVS_READONLY, &nvs) != ESP_OK) return false;

  uint8_t machineKey[32];
  size_t largo = sizeof(machineKey);
  const bool existe = nvs_get_blob(nvs, "machine_pri", machineKey, &largo) == ESP_OK;
  nvs_close(nvs);
  return existe;
}

bool tailnetEnabled() {
  return tailnet_auth_key[0] != 0 || tailnetHasStoredIdentity();
}

bool tailnetConnected() {
  return tailnetLink != NULL && microlink_is_connected(tailnetLink);
}

String tailnetVpnIp() {
  if (tailnetLink == NULL) return String();
  uint32_t address = microlink_get_vpn_ip(tailnetLink);
  if (address == 0) return String();

  char text[16];
  microlink_ip_to_str(address, text);
  return String(text);
}

bool startTailnet(uint32_t timeoutMs) {
  if (!tailnetEnabled()) return false;
  if (tailnetLink != NULL) return tailnetConnected();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("❌ Tailnet: hace falta Wi-Fi antes de registrar el dispositivo.");
    return false;
  }

  for (size_t index = 0; index < sizeof(MICROLINK_TAGS) / sizeof(MICROLINK_TAGS[0]); index++) {
    esp_log_level_set(MICROLINK_TAGS[index], TAILNET_LOG_LEVEL);
  }

  microlink_config_t config = {};
  config.auth_key = tailnet_auth_key;
  config.device_name = DEVICE_NAME;
  config.enable_derp = true;   // fallback por relay si el agujero no sale
  config.enable_stun = true;
  config.enable_disco = true;  // esto es lo que consigue el camino directo
  config.max_peers = 16;

  // El backend es el unico peer que importa: si la tabla se llena con nodos del
  // tailnet cacheados en NVS, este no puede quedar afuera.
  String host = backendHost();
  if (looksLikeIp(host)) config.priority_peer_ip = microlink_parse_ip(host.c_str());

  tailnetLink = microlink_init(&config);
  if (tailnetLink == NULL) {
    Serial.println("❌ Tailnet: no pude inicializar MicroLink.");
    return false;
  }

  microlink_set_state_callback(tailnetLink, onStateChange, NULL);

  if (microlink_start(tailnetLink) != ESP_OK) {
    Serial.println("❌ Tailnet: no pude arrancar MicroLink.");
    microlink_destroy(tailnetLink);
    tailnetLink = NULL;
    return false;
  }

  // El primer arranque incluye el registro contra el control plane y bajar el
  // MapResponse entero; los siguientes reusan las claves guardadas en NVS.
  uint32_t startedMs = millis();
  while (!microlink_is_connected(tailnetLink) && millis() - startedMs < timeoutMs) {
    delay(250);
  }

  if (!microlink_is_connected(tailnetLink)) {
    Serial.printf("⚠️ Tailnet: sin conexion despues de %lu ms. Sigo con la URL configurada.\n",
                  (unsigned long)timeoutMs);
    return false;
  }

  Serial.printf("✅ Tailnet listo (%d peers conocidos).\n", microlink_get_peer_count(tailnetLink));
  return true;
}

String tailnetResolve(const String& hostname) {
  if (looksLikeIp(hostname)) return hostname;
  if (!tailnetConnected()) return String();

  uint32_t address = microlink_resolve(tailnetLink, hostname.c_str());
  if (address == 0) return String();

  char text[16];
  microlink_ip_to_str(address, text);
  return String(text);
}

void tailnetEnsurePeer(const String& ip, uint16_t port) {
  if (!tailnetConnected() || !looksLikeIp(ip)) return;
  if (everWarmed && millis() - lastWarmMs < PEER_WARM_MS) return;

  uint32_t address = microlink_parse_ip(ip.c_str());
  if (address == 0) return;

  // No hay una primitiva publica de "levantar el tunel y nada mas": abrir y
  // cerrar un socket es lo que dispara el handshake y espera a que termine. El
  // backend ve una conexion que se cierra sin pedir nada.
  microlink_tcp_socket_t* probe =
      microlink_tcp_connect(tailnetLink, address, port, PEER_WARM_TIMEOUT_MS);
  if (probe != NULL) {
    microlink_tcp_close(probe);
    lastWarmMs = millis();
    everWarmed = true;
  } else {
    Serial.printf("⚠️ Tailnet: no pude levantar el tunel contra %s:%u.\n", ip.c_str(), port);
    everWarmed = false;
  }
}
