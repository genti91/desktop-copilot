#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include "commands.h"
#include "device_config.h"
#include "display.h"
#include "videocall.h"

namespace {
uint8_t currentRed = 255;
uint8_t currentGreen = 42;
uint8_t currentBlue = 0;
uint8_t currentBrightness = 70;
bool rgbEnabled = true;
bool filamentEnabled = true;
bool displayEnabled = true;
// ALL_OFF es un apagado temporal: no pisa la configuración guardada, sólo deja
// todo en silencio hasta el próximo toque del sensor.
bool sleeping = false;
Adafruit_NeoPixel rgbLed(NUMPIXELS, PIN_RGB, NEO_GRB + NEO_KHZ800);

void applyLedOutputs() {
  digitalWrite(PIN_LED, (filamentEnabled && !sleeping) ? HIGH : LOW);

  rgbLed.setBrightness(currentBrightness);
  rgbLed.setPixelColor(
    0,
    (rgbEnabled && !sleeping) ? rgbLed.Color(currentRed, currentGreen, currentBlue) : 0
  );
  rgbLed.show();
}
}

void applyDeviceOutputs() {
  applyLedOutputs();
  setDisplayPower(displayEnabled && !sleeping);
}

void initDeviceOutputs() {
  pinMode(PIN_LED, OUTPUT);
  rgbLed.begin();
  // El backlight lo enciende setup() recién después de tft.init().
  applyLedOutputs();
}

void wakeDeviceOutputs() {
  sleeping = false;
  applyDeviceOutputs();
}

void setRgbEnabled(bool enabled) {
  rgbEnabled = enabled;
}

void setRgbColor(uint8_t red, uint8_t green, uint8_t blue) {
  currentRed = red;
  currentGreen = green;
  currentBlue = blue;
}

void setRgbBrightness(uint8_t brightness) {
  currentBrightness = brightness;
}

void setFilamentEnabled(bool enabled) {
  filamentEnabled = enabled;
}

void setDisplayEnabled(bool enabled) {
  displayEnabled = enabled;
}

void executeDeviceCommand(String command) {
  command.trim();
  if (command == "NONE") return;

  Serial.println("💡 Comando(s) de hardware recibido(s): " + command);
  bool outputsNeedUpdate = false;
  int startIndex = 0;

  while (startIndex < command.length()) {
    int pipeIndex = command.indexOf('|', startIndex);
    if (pipeIndex == -1) pipeIndex = command.length();

    String singleCommand = command.substring(startIndex, pipeIndex);
    if (singleCommand.startsWith("LED_RGB:")) {
      String rgbValues = singleCommand.substring(8);
      int firstComma = rgbValues.indexOf(',');
      int secondComma = rgbValues.indexOf(',', firstComma + 1);

      if (firstComma > 0 && secondComma > 0) {
        currentRed = rgbValues.substring(0, firstComma).toInt();
        currentGreen = rgbValues.substring(firstComma + 1, secondComma).toInt();
        currentBlue = rgbValues.substring(secondComma + 1).toInt();
        // Pedir un color implica querer verlo, aunque el RGB estuviera apagado.
        rgbEnabled = true;
        sleeping = false;
        outputsNeedUpdate = true;
        Serial.printf("🎨 Color actualizado -> R: %d, G: %d, B: %d\n", currentRed, currentGreen, currentBlue);
      }
    } else if (singleCommand.startsWith("LED_BRIGHTNESS:")) {
      int brightness = singleCommand.substring(15).toInt();
      currentBrightness = constrain(brightness, 0, 255);
      rgbEnabled = true;
      sleeping = false;
      outputsNeedUpdate = true;
      Serial.printf("☀️ Brillo actualizado a: %d/255\n", currentBrightness);
    } else if (singleCommand == "FILAMENT_ON") {
      filamentEnabled = true;
      sleeping = false;
      outputsNeedUpdate = true;
      Serial.println("💡 Filamento ENCENDIDO");
    } else if (singleCommand == "FILAMENT_OFF") {
      filamentEnabled = false;
      outputsNeedUpdate = true;
      Serial.println("💡 Filamento APAGADO");
    } else if (singleCommand == "ALL_OFF" || singleCommand == "DISPLAY_OFF" || singleCommand == "POWER_OFF") {
      sleeping = true;
      outputsNeedUpdate = true;
      Serial.println("🌙 LEDs y display APAGADOS");
    } else if (singleCommand.startsWith("CALL:")) {
      // La llamada la levanta loop() cuando termina de sonar la respuesta: acá
      // sólo se anota a quién.
      requestVideoCall(singleCommand.substring(5));
    }

    startIndex = pipeIndex + 1;
  }

  if (outputsNeedUpdate) applyDeviceOutputs();
}
