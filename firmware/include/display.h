#pragma once

// WiFi.h va primero por lo que explica la nota de backend.h.
#include <WiFi.h>

#include <Arduino.h>
#include <WiFiManager.h>

#include "device_config.h"
#include "panel.h"

constexpr BaseType_t FACE_TASK_CORE = 0;
constexpr uint32_t FACE_TASK_DELAY_MS = 15;
constexpr size_t IDLE_IMAGE_BYTES = static_cast<size_t>(SCREEN_W) * SCREEN_H * 2;

extern Panel240 tft;
extern LGFX_Sprite faceCanvas;

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

// Congela la animación y espera a que la tarea suelte el TFT, para poder
// dibujar encima desde otro núcleo (por ejemplo, la pantalla de OTA).
void pauseFaceAnimation();
void resumeFaceAnimation();

// Imagen de reposo: se dibuja sólo en FACE_IDLE. En cuanto la cara pasa a
// grabar/esperar/hablar vuelve la animación, y al volver a FACE_IDLE reaparece.
bool loadIdleImage(const char* path);
void clearIdleImage();
bool hasIdleImage();
