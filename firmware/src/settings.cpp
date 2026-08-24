#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <WiFi.h>
#include "backend.h"
#include "commands.h"
#include "display.h"
#include "settings.h"

namespace {

struct DeviceSettings {
  uint32_t revision = 0;
  bool rgbEnabled = true;
  uint8_t red = 255;
  uint8_t green = 42;
  uint8_t blue = 0;
  uint8_t brightness = 70;
  bool filamentEnabled = true;
  bool displayEnabled = true;
  String imageChecksum = "";
};

DeviceSettings settings;
uint32_t lastPollMs = 0;
uint32_t pollIntervalMs = SETTINGS_POLL_INTERVAL_MS;
bool backendReachable = false;

void applySettings() {
  setRgbEnabled(settings.rgbEnabled);
  setRgbColor(settings.red, settings.green, settings.blue);
  setRgbBrightness(settings.brightness);
  setFilamentEnabled(settings.filamentEnabled);
  setDisplayEnabled(settings.displayEnabled);
  applyDeviceOutputs();
}

void persistSettings() {
  JsonDocument document;
  document["revision"] = settings.revision;
  document["rgb_enabled"] = settings.rgbEnabled;
  document["r"] = settings.red;
  document["g"] = settings.green;
  document["b"] = settings.blue;
  document["brightness"] = settings.brightness;
  document["filament_enabled"] = settings.filamentEnabled;
  document["display_enabled"] = settings.displayEnabled;
  document["image_checksum"] = settings.imageChecksum;

  File settingsFile = LittleFS.open(SETTINGS_PATH, "w");
  if (!settingsFile) {
    Serial.println("⚠️ No pude guardar la configuración del dispositivo.");
    return;
  }
  serializeJson(document, settingsFile);
  settingsFile.close();
}

bool restoreSettings() {
  File settingsFile = LittleFS.open(SETTINGS_PATH, "r");
  if (!settingsFile) return false;

  JsonDocument document;
  DeserializationError error = deserializeJson(document, settingsFile);
  settingsFile.close();
  if (error) {
    Serial.printf("⚠️ Configuración guardada ilegible (%s).\n", error.c_str());
    return false;
  }

  settings.revision = document["revision"] | 0u;
  settings.rgbEnabled = document["rgb_enabled"] | true;
  settings.red = document["r"] | 255;
  settings.green = document["g"] | 42;
  settings.blue = document["b"] | 0;
  settings.brightness = document["brightness"] | 70;
  settings.filamentEnabled = document["filament_enabled"] | true;
  settings.displayEnabled = document["display_enabled"] | true;
  settings.imageChecksum = document["image_checksum"] | "";
  return true;
}

bool downloadIdleImage(const String& imageUrl) {
  HTTPClient http;
  http.setTimeout(10000);
  if (!beginBackendRequest(http, imageUrl)) return false;

  int status = http.GET();
  if (status != HTTP_CODE_OK) {
    Serial.printf("⚠️ El backend respondió %d al pedir la imagen.\n", status);
    http.end();
    return false;
  }
  if (http.getSize() != static_cast<int>(IDLE_IMAGE_BYTES)) {
    Serial.printf("⚠️ Tamaño de imagen inesperado: %d bytes.\n", http.getSize());
    http.end();
    return false;
  }

  File imageFile = LittleFS.open(IDLE_IMAGE_PATH, "w");
  if (!imageFile) {
    http.end();
    return false;
  }

  WiFiClient* stream = http.getStreamPtr();
  uint8_t buffer[1024];
  size_t total = 0;
  uint32_t lastByteMs = millis();

  while (total < IDLE_IMAGE_BYTES) {
    size_t pending = min(sizeof(buffer), IDLE_IMAGE_BYTES - total);
    size_t received = stream->readBytes(buffer, pending);
    if (received > 0) {
      imageFile.write(buffer, received);
      total += received;
      lastByteMs = millis();
    } else if (millis() - lastByteMs > 10000 || !http.connected()) {
      break;
    } else {
      delay(1);
    }
  }

  imageFile.close();
  http.end();

  if (total != IDLE_IMAGE_BYTES) {
    Serial.printf("⚠️ Descarga incompleta de la imagen (%u/%u bytes).\n", (unsigned)total, (unsigned)IDLE_IMAGE_BYTES);
    LittleFS.remove(IDLE_IMAGE_PATH);
    return false;
  }
  return true;
}

void applyImage(JsonVariantConst image, const String& baseUrl) {
  if (image.isNull()) {
    if (settings.imageChecksum.length() > 0) {
      Serial.println("🖼️ Sin imagen seleccionada: vuelve la cara animada.");
    }
    clearIdleImage();
    LittleFS.remove(IDLE_IMAGE_PATH);
    settings.imageChecksum = "";
    return;
  }

  String checksum = image["checksum"] | "";
  if (checksum.length() > 0 && checksum == settings.imageChecksum && hasIdleImage()) return;

  String imageUrl = baseUrl + (image["url"] | "/device/image");
  Serial.println("⬇️ Descargando imagen de reposo: " + imageUrl);
  if (!downloadIdleImage(imageUrl) || !loadIdleImage(IDLE_IMAGE_PATH)) {
    Serial.println("⚠️ No pude aplicar la imagen; sigo con la cara animada.");
    clearIdleImage();
    settings.imageChecksum = "";
    return;
  }
  settings.imageChecksum = checksum;
}

}  // namespace

void initDeviceSettings() {
  if (restoreSettings()) {
    Serial.printf("📂 Configuración restaurada (revisión %u).\n", (unsigned)settings.revision);
    applySettings();
    if (settings.imageChecksum.length() > 0 && LittleFS.exists(IDLE_IMAGE_PATH)) {
      if (!loadIdleImage(IDLE_IMAGE_PATH)) settings.imageChecksum = "";
    }
  } else {
    applySettings();
  }
}

bool refreshDeviceSettings(bool force) {
  backendReachable = false;
  if (WiFi.status() != WL_CONNECTED) return false;

  String baseUrl = backendBaseUrl();
  HTTPClient http;
  http.setTimeout(4000);
  if (!beginBackendRequest(http, baseUrl + "/device/config")) return false;

  int status = http.GET();
  if (status != HTTP_CODE_OK) {
    http.end();
    return false;
  }
  backendReachable = true;

  JsonDocument document;
  DeserializationError error = deserializeJson(document, http.getStream());
  http.end();
  if (error) {
    Serial.printf("⚠️ Configuración remota ilegible (%s).\n", error.c_str());
    return false;
  }

  uint32_t revision = document["revision"] | 0u;
  if (!force && revision == settings.revision) return false;

  settings.revision = revision;
  settings.rgbEnabled = document["rgb"]["enabled"] | true;
  settings.red = document["rgb"]["r"] | 255;
  settings.green = document["rgb"]["g"] | 42;
  settings.blue = document["rgb"]["b"] | 0;
  settings.brightness = document["rgb"]["brightness"] | 70;
  settings.filamentEnabled = document["filament"]["enabled"] | true;
  settings.displayEnabled = document["display"]["enabled"] | true;

  Serial.printf("⚙️ Configuración aplicada (revisión %u).\n", (unsigned)revision);
  applySettings();
  applyImage(document["image"], baseUrl);
  persistSettings();
  return true;
}

void updateDeviceSettings() {
  uint32_t now = millis();
  if (now - lastPollMs < pollIntervalMs) return;
  lastPollMs = now;

  refreshDeviceSettings(false);
  if (!backendReachable) {
    pollIntervalMs = SETTINGS_RETRY_INTERVAL_MS;
  } else {
    pollIntervalMs = backendUsesTls() ? SETTINGS_REMOTE_POLL_INTERVAL_MS
                                      : SETTINGS_POLL_INTERVAL_MS;
  }
}
