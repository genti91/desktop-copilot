#include <Arduino.h>
#include <driver/i2s.h>
#include "audio.h"
#include "device_config.h"
#include "display.h"
#include "network.h"

uint8_t* pcm_buffer = NULL;
AudioGeneratorMP3* mp3 = NULL;
AudioFileSourceLittleFS* file = NULL;
AudioOutputI2S* out = NULL;
uint32_t playbackStartedMs = 0;

namespace {
void initMicrophone() {
  i2s_config_t i2sConfig = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 512,
    .use_apll = false
  };

  i2s_pin_config_t pinConfig = {
    .bck_io_num = I2S_PIN_NO_CHANGE,
    .ws_io_num = MIC_PDM_CLK,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = MIC_PDM_DATA
  };

  i2s_driver_install(I2S_NUM_0, &i2sConfig, 0, NULL);
  i2s_set_pin(I2S_NUM_0, &pinConfig);
}

void cleanAndAmplifyAudio(uint8_t* buffer, size_t totalBytes) {
  int16_t* samples = (int16_t*)buffer;
  size_t sampleCount = totalBytes / 2;
  if (sampleCount == 0) return;

  int64_t sum = 0;
  for (size_t index = 0; index < sampleCount; index++) sum += samples[index];
  int16_t dcOffset = sum / sampleCount;

  int16_t maxValue = 1;
  for (size_t index = 0; index < sampleCount; index++) {
    samples[index] -= dcOffset;
    int16_t absoluteValue = abs(samples[index]);
    if (absoluteValue > maxValue) maxValue = absoluteValue;
  }

  float gain = 26000.0 / maxValue;
  if (gain > 6.0) gain = 6.0;
  if (gain < 1.0) gain = 1.0;

  for (size_t index = 0; index < sampleCount; index++) {
    int32_t value = (int32_t)(samples[index] * gain);
    if (value > 32767) value = 32767;
    if (value < -32768) value = -32768;
    samples[index] = (int16_t)value;
  }
}
}

void initAudio() {
  pcm_buffer = (uint8_t*)ps_malloc(PCM_BUFFER_LEN);
  if (pcm_buffer) Serial.println("✅ PSRAM inicializada.");
  else Serial.println("❌ Error asignando PSRAM.");

  out = new AudioOutputI2S(1);
  out->SetPinout(I2S_BCLK, I2S_LRC, I2S_DIN);
  out->SetGain(0.5);
  mp3 = new AudioGeneratorMP3();
  initMicrophone();
}

void recordWhileTouched() {
  if (!pcm_buffer) return;

  Serial.println("🎙️ Sensor Tocado: Grabando...");
  setFaceMode(FACE_RECORDING);

  size_t bytesRead = 0;
  size_t totalBytes = 0;
  i2s_read(I2S_NUM_0, pcm_buffer, 1024, &bytesRead, portMAX_DELAY);

  while (digitalRead(TOUCH_PIN) == HIGH && totalBytes < PCM_BUFFER_LEN) {
    i2s_read(I2S_NUM_0, pcm_buffer + totalBytes, 1024, &bytesRead, portMAX_DELAY);
    totalBytes += bytesRead;
  }

  Serial.printf("🛑 TTP223 Liberado: Grabación finalizada (%d bytes).\n", totalBytes);
  if (totalBytes > 0) {
    cleanAndAmplifyAudio(pcm_buffer, totalBytes);
    sendAudioAndPlayResponse(totalBytes);
  } else {
    setFaceMode(FACE_IDLE);
  }
}

void updateAudioPlayback() {
  if (!mp3->isRunning()) return;

  if (!mp3->loop()) {
    mp3->stop();
    if (file) {
      delete file;
      file = NULL;
    }
    Serial.printf("✅ Reproducción finalizada tras %lu ms.\n",
                  (unsigned long)(millis() - playbackStartedMs));
    setFaceMode(FACE_IDLE);
  }
}
