#pragma once

#include <Arduino.h>
#include <TFT_eSPI.h>
#include <WiFiManager.h>

constexpr uint16_t SCREEN_W = 240;
constexpr uint16_t SCREEN_H = 240;
constexpr BaseType_t FACE_TASK_CORE = 0;
constexpr uint32_t FACE_TASK_DELAY_MS = 15;

extern TFT_eSPI tft;
extern TFT_eSprite faceCanvas;

enum FaceMode {
  FACE_IDLE,
  FACE_RECORDING,
  FACE_WAITING,
  FACE_SPEAKING
};

void configModeCallback(WiFiManager* wifiManager);
void setDisplayPower(bool enabled);
void setFaceMode(FaceMode mode);
void faceAnimationTask(void* parameter);
