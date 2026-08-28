#pragma once

// El orden de estos includes no es alfabetico a proposito:
//  - WiFi.h primero, por lo que explica la nota de backend.h.
//  - FS.h antes que TFT_eSPI.h: TFT_eSPI define FS_NO_GLOBALS, que deja a
//    fs::FS fuera del scope global, y WebServer.h —que entra por WiFiManager—
//    lo usa sin calificar. Incluirlo antes deja los alias puestos.
#include <WiFi.h>
#include <FS.h>

#include <Arduino.h>
#include <TFT_eSPI.h>
#include <WiFiManager.h>

#include "device_config.h"

constexpr BaseType_t FACE_TASK_CORE = 0;
constexpr uint32_t FACE_TASK_DELAY_MS = 15;
constexpr size_t IDLE_IMAGE_BYTES = static_cast<size_t>(SCREEN_W) * SCREEN_H * 2;

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

// Congela la animación y espera a que la tarea suelte el TFT, para poder
// dibujar encima desde otro núcleo (por ejemplo, la pantalla de OTA).
void pauseFaceAnimation();
void resumeFaceAnimation();

// Imagen de reposo: se dibuja sólo en FACE_IDLE. En cuanto la cara pasa a
// grabar/esperar/hablar vuelve la animación, y al volver a FACE_IDLE reaparece.
bool loadIdleImage(const char* path);
void clearIdleImage();
bool hasIdleImage();
