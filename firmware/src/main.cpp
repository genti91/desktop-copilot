#include <Arduino.h>
#include <LittleFS.h>
#include <WiFi.h>
#include <WiFiManager.h>
#include "audio.h"
#include "commands.h"
#include "device_config.h"
#include "display.h"
#include "ota.h"
#include "settings.h"
#include "version.h"

char server_url[128] = "http://192.168.100.99:8000/voice-assistant";
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

  if (LittleFS.exists("/config.txt")) {
    File configFile = LittleFS.open("/config.txt", "r");
    if (configFile) {
      String loadedUrl = configFile.readStringUntil('\n');
      loadedUrl.trim();
      if (loadedUrl.length() > 0) {
        strlcpy(server_url, loadedUrl.c_str(), sizeof(server_url));
        Serial.printf("📂 URL cargada desde flash: %s\n", server_url);
      }
      configFile.close();
    }
  }

  WiFiManager wifiManager;
  wifiManager.setSaveConfigCallback(saveConfigCallback);
  wifiManager.setAPCallback(configModeCallback);

  WiFiManagerParameter customServerUrl("server", "Server URL", server_url, 128);
  wifiManager.addParameter(&customServerUrl);

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
    File configFile = LittleFS.open("/config.txt", "w");
    if (configFile) {
      configFile.println(server_url);
      configFile.close();
      Serial.println("💾 Nueva URL del servidor guardada en LittleFS.");
    }
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
