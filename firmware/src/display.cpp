#include "display.h"
#include "device_config.h"

namespace {
constexpr uint16_t FACE_COLOR = TFT_WHITE;
volatile FaceMode currentFaceMode = FACE_IDLE;
volatile bool displayPowered = true;

void drawEye(int centerX, int centerY, int width, int height) {
  if (height < 4) height = 4;
  int radius = min(width, height) / 2;
  faceCanvas.fillRoundRect(centerX - width / 2, centerY - height / 2, width, height, radius, FACE_COLOR);
}

void drawSmileMouth(int centerX, int centerY, int width, int height, int thickness) {
  if (height < 4) height = 4;
  faceCanvas.fillRoundRect(centerX - width / 2, centerY - height / 2, width, height, height / 2, FACE_COLOR);
  faceCanvas.fillRoundRect(centerX - width / 2 - 2, centerY - height / 2 - 2, width + 4, height - thickness, (height - thickness) / 2, TFT_BLACK);
}

void drawIdleFace(uint32_t now) {
  uint32_t cycle = now % 4000;
  int eyeHeight = 85;

  if (cycle < 80) {
    eyeHeight = map(cycle, 0, 80, 85, 4);
  } else if (cycle < 160) {
    eyeHeight = map(cycle, 80, 160, 4, 85);
  }

  int breath = sin(now / 400.0) * 2.0;
  drawEye(65, 110, 48 + breath, eyeHeight + breath);
  drawEye(175, 110, 48 + breath, eyeHeight + breath);
  drawSmileMouth(120, 175, 36, 18, 7);
}

void drawRecordingFace(uint32_t now) {
  int pulse = sin(now / 150.0) * 6.0;

  drawEye(65, 110, 48 + pulse, 85 + pulse);
  drawEye(175, 110, 48 + pulse, 85 + pulse);
  drawSmileMouth(120, 175, 20, 10, 5);

  if (sin(now / 150.0) > 0) {
    faceCanvas.fillCircle(120, 30, 6, TFT_RED);
  }
}

void drawWaitingFace(uint32_t now) {
  float offsetX = sin(now / 400.0) * 20.0;
  uint32_t cycle = now % 2500;
  int eyeHeight = 85;

  if (cycle < 100) eyeHeight = map(cycle, 0, 100, 85, 4);
  else if (cycle < 200) eyeHeight = map(cycle, 100, 200, 4, 85);

  drawEye(65 + static_cast<int>(offsetX), 110, 48, eyeHeight);
  drawEye(175 + static_cast<int>(offsetX), 110, 48, eyeHeight);
  drawSmileMouth(120 + static_cast<int>(offsetX * 0.3), 175, 26, 12, 6);
}

void drawSpeakingFace(uint32_t now) {
  uint32_t cycle = now % 2000;
  int eyeHeight = 85;

  if (cycle < 80) {
    eyeHeight = map(cycle, 0, 80, 85, 4);
  } else if (cycle < 160) {
    eyeHeight = map(cycle, 80, 160, 4, 85);
  }

  drawEye(65, 110, 48, eyeHeight);
  drawEye(175, 110, 48, eyeHeight);

  float wave1 = sin(now / 100.0);
  float wave2 = cos(now / 160.0);
  float combined = fabs(wave1 + wave2) / 2.0;
  int mouthWidth = 24 + (combined * 12.0);
  int mouthHeight = 10 + (combined * 14.0);

  faceCanvas.fillRoundRect(120 - mouthWidth / 2, 175 - mouthHeight / 2, mouthWidth, mouthHeight, min(mouthWidth, mouthHeight) / 2, FACE_COLOR);
}

void renderFace(FaceMode mode, uint32_t now) {
  if (!displayPowered) return;

  faceCanvas.fillSprite(TFT_BLACK);

  switch (mode) {
    case FACE_RECORDING: drawRecordingFace(now); break;
    case FACE_WAITING: drawWaitingFace(now); break;
    case FACE_SPEAKING: drawSpeakingFace(now); break;
    case FACE_IDLE:
    default: drawIdleFace(now); break;
  }

  faceCanvas.pushSprite(0, 0);
}

void updateFace() {
  renderFace(currentFaceMode, millis());
}
}

TFT_eSPI tft = TFT_eSPI();
TFT_eSprite faceCanvas = TFT_eSprite(&tft);

void configModeCallback(WiFiManager* wifiManager) {
  Serial.println("Entrando a Modo Configuracion WiFi");

  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(2);
  tft.drawCentreString("Sin conexion", SCREEN_W / 2, 40, 1);
  tft.drawCentreString("Conectate al WiFi:", SCREEN_W / 2, 80, 1);

  tft.setTextColor(TFT_YELLOW, TFT_BLACK);
  tft.drawCentreString(wifiManager->getConfigPortalSSID(), SCREEN_W / 2, 115, 1);

  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawCentreString("y abre tu navegador en:", SCREEN_W / 2, 160, 1);

  tft.setTextColor(TFT_CYAN, TFT_BLACK);
  tft.drawCentreString(WiFi.softAPIP().toString(), SCREEN_W / 2, 195, 1);
}

void setFaceMode(FaceMode mode) {
  currentFaceMode = mode;
}

void setDisplayPower(bool enabled) {
  pinMode(PIN_TFT_BL, OUTPUT);
  displayPowered = enabled;
  digitalWrite(PIN_TFT_BL, enabled ? HIGH : LOW);
}

void faceAnimationTask(void* parameter) {
  (void)parameter;
  for (;;) {
    updateFace();
    vTaskDelay(pdMS_TO_TICKS(FACE_TASK_DELAY_MS));
  }
}
