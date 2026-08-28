#include <LittleFS.h>
#include <driver/gpio.h>
#include "display.h"
#include "device_config.h"

namespace {
constexpr uint16_t FACE_COLOR = TFT_WHITE;
volatile FaceMode currentFaceMode = FACE_IDLE;
volatile bool displayPowered = true;
uint16_t* idleImage = nullptr;
volatile bool faceTaskRunning = false;
volatile bool facePaused = false;
volatile bool faceParked = false;
volatile bool idleImageReady = false;
volatile bool idleImageDrawn = false;
uint32_t ultimoCuadroMs = 0;
uint32_t peorHuecoMs = 0;
uint32_t cuadros = 0;
uint32_t ventanaMs = 0;

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

  if (mode == FACE_IDLE && idleImageReady && idleImage) {
    if (!idleImageDrawn) {
      tft.setSwapBytes(true);
      tft.pushImage(0, 0, SCREEN_W, SCREEN_H, idleImage);
      idleImageDrawn = true;
    }
    return;
  }
  idleImageDrawn = false;

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
  // Sirve para los dos casos: el Wi-Fi que no conecta y el portal abierto a mano
  // desde el sensor tactil.
  tft.drawCentreString("Configuracion", SCREEN_W / 2, 40, 1);
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

// La luz de fondo va por D6, que en el XIAO es GPIO43 = U0TXD. El gestor de
// periféricos de Arduino 3.x no entrega un pin que sigue anotado como UART, así
// que pinMode() lo rechaza y digitalWrite() imprime "IO 43 is not set as GPIO"
// sin hacer nada. Arduino 2.x no tenía ese gestor y el digitalWrite pasaba
// directo: por eso el encendido y apagado de la pantalla dejó de funcionar en la
// migración. Se va por el driver de IDF, que no consulta esa tabla. La consola
// sale por USB, así que UART0 no se usa para nada.
void setDisplayPower(bool enabled) {
  static bool backlightReady = false;
  const gpio_num_t backlight = static_cast<gpio_num_t>(PIN_TFT_BL);
  if (!backlightReady) {
    gpio_reset_pin(backlight);
    gpio_set_direction(backlight, GPIO_MODE_OUTPUT);
    backlightReady = true;
  }
  if (enabled && !displayPowered) idleImageDrawn = false;
  displayPowered = enabled;
  gpio_set_level(backlight, enabled ? 1 : 0);
}

bool loadIdleImage(const char* path) {
  File imageFile = LittleFS.open(path, "r");
  if (!imageFile) {
    Serial.printf("⚠️ No pude abrir la imagen de reposo (%s).\n", path);
    return false;
  }
  if (imageFile.size() != IDLE_IMAGE_BYTES) {
    Serial.printf("⚠️ Imagen de reposo con tamaño inesperado (%u bytes).\n", (unsigned)imageFile.size());
    imageFile.close();
    return false;
  }

  if (!idleImage) {
    idleImage = static_cast<uint16_t*>(ps_malloc(IDLE_IMAGE_BYTES));
    if (!idleImage) idleImage = static_cast<uint16_t*>(malloc(IDLE_IMAGE_BYTES));
    if (!idleImage) {
      Serial.println("❌ Sin memoria para la imagen de reposo.");
      imageFile.close();
      return false;
    }
  }

  // La tarea de la cara corre en el otro núcleo: la dormimos un par de cuadros
  // antes de reescribir el buffer para no mostrar una imagen a medio cargar.
  idleImageReady = false;
  vTaskDelay(pdMS_TO_TICKS(FACE_TASK_DELAY_MS * 3));

  size_t bytesRead = imageFile.read(reinterpret_cast<uint8_t*>(idleImage), IDLE_IMAGE_BYTES);
  imageFile.close();
  if (bytesRead != IDLE_IMAGE_BYTES) {
    Serial.println("⚠️ Lectura incompleta de la imagen de reposo.");
    return false;
  }

  idleImageDrawn = false;
  idleImageReady = true;
  Serial.println("🖼️ Imagen de reposo cargada.");
  return true;
}

void clearIdleImage() {
  idleImageReady = false;
  idleImageDrawn = false;
}

bool hasIdleImage() {
  return idleImageReady;
}

void pauseFaceAnimation() {
  if (!faceTaskRunning) return;
  facePaused = true;
  for (int attempt = 0; attempt < 50 && !faceParked; attempt++) delay(10);
}

void resumeFaceAnimation() {
  idleImageDrawn = false;
  facePaused = false;
}

void faceAnimationTask(void* parameter) {
  (void)parameter;
  faceTaskRunning = true;
  for (;;) {
    if (facePaused) {
      faceParked = true;
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }
    faceParked = false;

    // La cara es la única señal de que el aparato registró el toque. Si la tarea
    // se queda sin procesador —vive en el núcleo 0, junto al Wi-Fi y a
    // MicroLink— el usuario ve la reacción tarde aunque el firmware haya
    // reaccionado a tiempo. Esto mide el peor hueco entre dos cuadros.
    const uint32_t ahora = millis();
    if (ultimoCuadroMs != 0) {
      const uint32_t hueco = ahora - ultimoCuadroMs;
      if (hueco > peorHuecoMs) peorHuecoMs = hueco;
    }
    ultimoCuadroMs = ahora;
    cuadros++;
    if (ahora - ventanaMs >= 5000) {
      Serial.printf("[cara] %lu cuadros en 5 s  peor_hueco=%lums\n",
                    (unsigned long)cuadros, (unsigned long)peorHuecoMs);
      cuadros = 0;
      peorHuecoMs = 0;
      ventanaMs = ahora;
    }

    updateFace();
    vTaskDelay(pdMS_TO_TICKS(FACE_TASK_DELAY_MS));
  }
}
