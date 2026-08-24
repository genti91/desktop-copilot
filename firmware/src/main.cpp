#include <Arduino.h>
#include <LittleFS.h>
#include <WiFi.h>
#include <WiFiManager.h>
#include "audio.h"
#include "backend.h"
#include "commands.h"
#include "device_config.h"
#include "display.h"
#include "ota.h"
#include "settings.h"
#include "version.h"

// server_url y device_token viven en backend.cpp.
bool shouldSaveConfig = false;
TaskHandle_t faceTaskHandle = NULL;

void saveConfigCallback() {
  shouldSaveConfig = true;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\n🚀 Firmware %s (build %d)\n", FIRMWARE_VERSION, FIRMWARE_BUILD);

  initDeviceOutputs();

  tft.init();
  setDisplayPower(true);
  tft.setRotation(3);
  tft.fillScreen(TFT_RED);
  delay(200);
  tft.fillScreen(TFT_BLACK);
  delay(100);

  faceCanvas.setColorDepth(16);
  faceCanvas.createSprite(SCREEN_W, SCREEN_H);
  setFaceMode(FACE_IDLE);
  pinMode(TOUCH_PIN, INPUT);

  if (!LittleFS.begin(true)) {
    Serial.println("❌ Error al iniciar LittleFS");
  }

  loadBackendConfig();

  WiFiManager wifiManager;
  wifiManager.setSaveConfigCallback(saveConfigCallback);
  wifiManager.setAPCallback(configModeCallback);

  WiFiManagerParameter customServerUrl("server", "URL del backend", server_url, SERVER_URL_SIZE);
  // Hace falta sólo cuando el ESP32 sale de la LAN, por ejemplo vía Tailscale Funnel.
  WiFiManagerParameter customDeviceToken("token", "Token del dispositivo (opcional)", device_token, DEVICE_TOKEN_SIZE);
  wifiManager.addParameter(&customServerUrl);
  wifiManager.addParameter(&customDeviceToken);

  Serial.println("📡 Conectando a Wi-Fi o abriendo portal cautivo...");
  if (!wifiManager.autoConnect("ESP32_Asistente")) {
    Serial.println("❌ Falló la conexión WiFi o timeout. Reiniciando...");
    delay(3000);
    ESP.restart();
  }

  Serial.println("\n🌐 Wi-Fi conectado con éxito.");
  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_GREEN, TFT_BLACK);
  tft.setTextSize(2);
  tft.drawCentreString("Conectado!", SCREEN_W / 2, SCREEN_H / 2 - 10, 1);
  delay(1500);

  if (shouldSaveConfig) {
    strlcpy(server_url, customServerUrl.getValue(), sizeof(server_url));
    strlcpy(device_token, customDeviceToken.getValue(), sizeof(device_token));
    saveBackendConfig();
  }

  // Antes de arrancar nada más: si el backend publicó un build mayor, el ESP32
  // se actualiza y reinicia desde acá.
  checkForFirmwareUpdate();

  initAudio();
  initDeviceSettings();

  xTaskCreatePinnedToCore(
    faceAnimationTask,
    "faceTask",
    4096,
    NULL,
    1,
    &faceTaskHandle,
    FACE_TASK_CORE
  );

  // Con la tarea de la cara ya viva, traemos la configuración actual del backend
  // (colores, encendidos e imagen de reposo).
  refreshDeviceSettings(true);

  Serial.println("\n✅ SISTEMA LISTO. Mantén presionado el sensor Touch para hablar.");
}

void loop() {
  updateAudioPlayback();
  if (!mp3->isRunning()) {
    updateDeviceSettings();
    updateFirmwareCheck();
  }

  if (digitalRead(TOUCH_PIN) == HIGH) {
    delay(50);
    if (digitalRead(TOUCH_PIN) == HIGH) {
      wakeDeviceOutputs();
      recordWhileTouched();
      while (digitalRead(TOUCH_PIN) == HIGH) delay(10);
    }
  }
}
