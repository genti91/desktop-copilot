#pragma once

#include <Arduino.h>

#define TOUCH_PIN 4
#define MIC_PDM_CLK 42
#define MIC_PDM_DATA 41
#define I2S_BCLK 2
#define I2S_LRC 1
#define I2S_DIN 3
#define PIN_RGB D4
#define PIN_LED D5
#define PIN_TFT_BL D6
// Pines del panel. Antes vivian en las build_flags de TFT_eSPI; con LovyanGFX
// la configuracion es codigo, asi que van con el resto del hardware.
#define PIN_TFT_SCLK 7
#define PIN_TFT_MOSI 9
#define PIN_TFT_DC 8
#define PIN_TFT_RST 44

constexpr uint16_t SCREEN_W = 240;
constexpr uint16_t SCREEN_H = 240;
#define NUMPIXELS 1

#define SAMPLE_RATE 16000
#define RECORD_TIME_S 10
#define PCM_BUFFER_LEN (SAMPLE_RATE * 2 * RECORD_TIME_S)

struct WavHeader {
  char riff[4] = {'R', 'I', 'F', 'F'};
  uint32_t chunkSize;
  char wave[4] = {'W', 'A', 'V', 'E'};
  char fmt[4] = {'f', 'm', 't', ' '};
  uint32_t subchunk1Size = 16;
  uint16_t audioFormat = 1;
  uint16_t numChannels = 1;
  uint32_t sampleRate = SAMPLE_RATE;
  uint32_t byteRate = SAMPLE_RATE * 1 * 2;
  uint16_t blockAlign = 1 * 2;
  uint16_t bitsPerSample = 16;
  char data[4] = {'d', 'a', 't', 'a'};
  uint32_t subchunk2Size;
};
