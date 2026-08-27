// WiFi.h primero: ver la nota en backend.h sobre el orden de los includes.
#include <WiFi.h>

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Update.h>
#include <mbedtls/sha256.h>
#include <mbedtls/version.h>
#include "backend.h"
#include "display.h"
#include "ota.h"
#include "version.h"

// mbedtls 2.x expone las variantes *_ret; en 3.x esos alias desaparecieron.
#if MBEDTLS_VERSION_NUMBER >= 0x03000000
#define SHA256_STARTS mbedtls_sha256_starts
#define SHA256_UPDATE mbedtls_sha256_update
#define SHA256_FINISH mbedtls_sha256_finish
#else
#define SHA256_STARTS mbedtls_sha256_starts_ret
#define SHA256_UPDATE mbedtls_sha256_update_ret
#define SHA256_FINISH mbedtls_sha256_finish_ret
#endif

namespace {

constexpr uint32_t OTA_STALL_TIMEOUT_MS = 15000;

void showOtaScreen(const String& title, const String& detail, uint16_t color) {
  tft.fillScreen(TFT_BLACK);
  tft.setTextSize(2);
  tft.setTextColor(color, TFT_BLACK);
  tft.drawCentreString(title, SCREEN_W / 2, 85, 1);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawCentreString(detail, SCREEN_W / 2, 125, 1);
}

void showOtaProgress(uint8_t percent) {
  constexpr int16_t barX = 30;
  constexpr int16_t barY = 155;
  constexpr int16_t barW = SCREEN_W - 60;
  constexpr int16_t barH = 16;

  tft.drawRect(barX, barY, barW, barH, TFT_WHITE);
  tft.fillRect(barX + 2, barY + 2, ((barW - 4) * percent) / 100, barH - 4, TFT_GREEN);
}

String toHex(const uint8_t* digest, size_t length) {
  String hex;
  hex.reserve(length * 2);
  for (size_t index = 0; index < length; index++) {
    char pair[3];
    snprintf(pair, sizeof(pair), "%02x", digest[index]);
    hex += pair;
  }
  return hex;
}

bool downloadAndFlash(const String& binaryUrl, const String& expectedSha256) {
  HTTPClient http;
  http.setTimeout(15000);
  if (!beginBackendRequest(http, binaryUrl)) return false;

  int status = http.GET();
  if (status != HTTP_CODE_OK) {
    Serial.printf("❌ OTA: el backend respondió %d.\n", status);
    http.end();
    return false;
  }

  int contentLength = http.getSize();
  if (contentLength <= 0) {
    Serial.println("❌ OTA: el backend no informó el tamaño del binario.");
    http.end();
    return false;
  }

  if (!Update.begin(contentLength)) {
    Serial.printf("❌ OTA: no entra en la partición (%s).\n", Update.errorString());
    http.end();
    return false;
  }

  mbedtls_sha256_context shaContext;
  mbedtls_sha256_init(&shaContext);
  SHA256_STARTS(&shaContext, 0);

  WiFiClient* stream = http.getStreamPtr();
  uint8_t buffer[1024];
  size_t written = 0;
  uint8_t lastPercent = 255;
  uint32_t lastByteMs = millis();

  while (written < static_cast<size_t>(contentLength)) {
    size_t pending = min(sizeof(buffer), static_cast<size_t>(contentLength) - written);
    size_t received = stream->readBytes(buffer, pending);

    if (received == 0) {
      if (millis() - lastByteMs > OTA_STALL_TIMEOUT_MS || !http.connected()) break;
      delay(1);
      continue;
    }

    if (Update.write(buffer, received) != received) {
      Serial.printf("❌ OTA: error escribiendo flash (%s).\n", Update.errorString());
      break;
    }
    SHA256_UPDATE(&shaContext, buffer, received);
    written += received;
    lastByteMs = millis();

    uint8_t percent = (written * 100) / contentLength;
    if (percent != lastPercent) {
      showOtaProgress(percent);
      lastPercent = percent;
    }
  }

  uint8_t digest[32];
  SHA256_FINISH(&shaContext, digest);
  mbedtls_sha256_free(&shaContext);
  http.end();

  if (written != static_cast<size_t>(contentLength)) {
    Serial.printf("❌ OTA: descarga incompleta (%u/%d bytes).\n", (unsigned)written, contentLength);
    Update.abort();
    return false;
  }

  String actualSha256 = toHex(digest, sizeof(digest));
  if (expectedSha256.length() == 64 && !expectedSha256.equalsIgnoreCase(actualSha256)) {
    Serial.println("❌ OTA: el SHA-256 no coincide. Se descarta la actualización.");
    Serial.println("   esperado: " + expectedSha256);
    Serial.println("   recibido: " + actualSha256);
    Update.abort();
    return false;
  }

  if (!Update.end(true)) {
    Serial.printf("❌ OTA: no se pudo finalizar (%s).\n", Update.errorString());
    return false;
  }
  return true;
}

uint32_t lastCheckMs = 0;

}  // namespace

void checkForFirmwareUpdate() {
  lastCheckMs = millis();
  if (WiFi.status() != WL_CONNECTED) return;

  String baseUrl = backendBaseUrl();
  HTTPClient http;
  http.setTimeout(5000);
  if (!beginBackendRequest(http, baseUrl + "/ota/manifest")) return;

  int status = http.GET();
  if (status != HTTP_CODE_OK) {
    Serial.printf("ℹ️ OTA: sin manifest disponible (HTTP %d).\n", status);
    http.end();
    return;
  }

  JsonDocument manifest;
  DeserializationError error = deserializeJson(manifest, http.getStream());
  http.end();
  if (error) {
    Serial.printf("⚠️ OTA: manifest ilegible (%s).\n", error.c_str());
    return;
  }

  bool available = manifest["available"] | false;
  int remoteBuild = manifest["build"] | 0;
  String remoteVersion = manifest["version"] | "0.0.0";

  Serial.printf("🧩 Firmware local %s (build %d) | remoto %s (build %d)\n",
                FIRMWARE_VERSION, FIRMWARE_BUILD, remoteVersion.c_str(), remoteBuild);

  if (!available || remoteBuild <= FIRMWARE_BUILD) {
    Serial.println("✅ Firmware al día.");
    return;
  }

  String path = manifest["url"] | "/ota/download";
  String expectedSha256 = manifest["sha256"] | "";

  Serial.println("⬇️ OTA: descargando " + remoteVersion + "...");

  // Si la cara ya está animando (chequeo periódico), hay que sacarla del TFT
  // antes de dibujar: corre en el otro núcleo.
  pauseFaceAnimation();
  showOtaScreen("Actualizando", "v" + remoteVersion, TFT_CYAN);

  if (downloadAndFlash(baseUrl + path, expectedSha256)) {
    Serial.println("✅ OTA aplicada. Reiniciando...");
    showOtaScreen("Listo!", "Reiniciando", TFT_GREEN);
    delay(1200);
    ESP.restart();
  }

  showOtaScreen("Fallo OTA", "Sigo con v" FIRMWARE_VERSION, TFT_RED);
  delay(2000);
  resumeFaceAnimation();
}

void updateFirmwareCheck() {
  if (millis() - lastCheckMs < OTA_CHECK_INTERVAL_MS) return;
  checkForFirmwareUpdate();
}
