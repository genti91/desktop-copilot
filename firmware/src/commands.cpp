#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include "commands.h"
#include "device_config.h"
#include "display.h"

namespace {
uint8_t currentRed = 255;
uint8_t currentGreen = 42;
uint8_t currentBlue = 0;
Adafruit_NeoPixel rgbLed(NUMPIXELS, PIN_RGB, NEO_GRB + NEO_KHZ800);
}

void initDeviceOutputs() {
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, HIGH);

  rgbLed.begin();
  rgbLed.setBrightness(70);
  rgbLed.setPixelColor(0, rgbLed.Color(255, 42, 0));
  rgbLed.show();
}

void wakeDeviceOutputs() {
  digitalWrite(PIN_LED, HIGH);
  setDisplayPower(true);
  rgbLed.setPixelColor(0, rgbLed.Color(currentRed, currentGreen, currentBlue));
  rgbLed.show();
}

void executeDeviceCommand(String command) {
  command.trim();
  if (command == "NONE") return;

  Serial.println("💡 Comando(s) de hardware recibido(s): " + command);
  bool ledNeedsUpdate = false;
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
        ledNeedsUpdate = true;
        Serial.printf("🎨 Color actualizado -> R: %d, G: %d, B: %d\n", currentRed, currentGreen, currentBlue);
      }
    } else if (singleCommand.startsWith("LED_BRIGHTNESS:")) {
      int brightness = singleCommand.substring(15).toInt();
      brightness = constrain(brightness, 0, 255);
      rgbLed.setBrightness(brightness);
      ledNeedsUpdate = true;
      Serial.printf("☀️ Brillo actualizado a: %d/255\n", brightness);
    } else if (singleCommand == "FILAMENT_ON") {
      digitalWrite(PIN_LED, HIGH);
      Serial.println("💡 Filamento ENCENDIDO");
    } else if (singleCommand == "FILAMENT_OFF") {
      digitalWrite(PIN_LED, LOW);
      Serial.println("💡 Filamento APAGADO");
    } else if (singleCommand == "ALL_OFF" || singleCommand == "DISPLAY_OFF" || singleCommand == "POWER_OFF") {
      digitalWrite(PIN_LED, LOW);
      rgbLed.setPixelColor(0, 0);
      rgbLed.show();
      setDisplayPower(false);
      Serial.println("🌙 LEDs y display APAGADOS");
    }

    startIndex = pipeIndex + 1;
  }

  if (ledNeedsUpdate) {
    rgbLed.setPixelColor(0, rgbLed.Color(currentRed, currentGreen, currentBlue));
    rgbLed.show();
  }
}
