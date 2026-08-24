#include <LittleFS.h>
#include <WiFiClientSecure.h>
#include "backend.h"

char server_url[SERVER_URL_SIZE] = "http://192.168.100.52:8000/voice-assistant";
char device_token[DEVICE_TOKEN_SIZE] = "";

namespace {

constexpr const char* CONFIG_PATH = "/config.txt";

// ISRG Root X1: Tailscale Funnel sirve certificados de Let's Encrypt, que
// encadenan a esta raiz. Vence en junio de 2035.
const char LETSENCRYPT_ROOT_CA[] PROGMEM =
    "-----BEGIN CERTIFICATE-----\n"
    "MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw\n"
    "TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh\n"
    "cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4\n"
    "WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu\n"
    "ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY\n"
    "MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc\n"
    "h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+\n"
    "0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U\n"
    "A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW\n"
    "T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH\n"
    "B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC\n"
    "B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv\n"
    "KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn\n"
    "OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn\n"
    "jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw\n"
    "qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI\n"
    "rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV\n"
    "HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq\n"
    "hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL\n"
    "ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ\n"
    "3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK\n"
    "NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5\n"
    "ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur\n"
    "TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC\n"
    "jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc\n"
    "oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq\n"
    "4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA\n"
    "mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d\n"
    "emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=\n"
    "-----END CERTIFICATE-----\n";

WiFiClient plainClient;
WiFiClientSecure secureClient;
bool secureClientReady = false;

String normalizedUrl() {
  String url = String(server_url);
  url.trim();
  if (!url.startsWith("http://") && !url.startsWith("https://")) url = "http://" + url;
  return url;
}

String authority() {
  String url = normalizedUrl();
  int schemeEnd = url.indexOf("://") + 3;
  int pathStart = url.indexOf('/', schemeEnd);
  return pathStart > 0 ? url.substring(schemeEnd, pathStart) : url.substring(schemeEnd);
}

}  // namespace

bool backendUsesTls() {
  return normalizedUrl().startsWith("https://");
}

String backendHost() {
  String host = authority();
  int colonIndex = host.indexOf(':');
  return colonIndex > 0 ? host.substring(0, colonIndex) : host;
}

uint16_t backendPort() {
  String host = authority();
  int colonIndex = host.indexOf(':');
  // Funnel expone https sin puerto explicito, asi que hay que asumir el default.
  if (colonIndex < 0) return backendUsesTls() ? 443 : 80;
  return host.substring(colonIndex + 1).toInt();
}

String backendPath() {
  String url = normalizedUrl();
  int schemeEnd = url.indexOf("://") + 3;
  int pathStart = url.indexOf('/', schemeEnd);
  return pathStart > 0 ? url.substring(pathStart) : "/voice-assistant";
}

String backendBaseUrl() {
  String url = normalizedUrl();
  int schemeEnd = url.indexOf("://") + 3;
  int pathStart = url.indexOf('/', schemeEnd);
  return pathStart > 0 ? url.substring(0, pathStart) : url;
}

WiFiClient& backendTransport() {
  if (!backendUsesTls()) {
    plainClient.stop();
    return plainClient;
  }

  secureClient.stop();
  if (!secureClientReady) {
    secureClient.setCACert(LETSENCRYPT_ROOT_CA);
    secureClientReady = true;
  }
  return secureClient;
}

void writeDeviceTokenHeader(WiFiClient& client) {
  if (device_token[0] == 0) return;
  client.printf("X-Device-Token: %s\r\n", device_token);
}

bool beginBackendRequest(HTTPClient& http, const String& url) {
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
  configFile.close();
  Serial.println("Configuracion del backend guardada en LittleFS.");
}
