#include <Arduino.h>
#include <driver/i2s_pdm.h>
#include "audio.h"
#include "device_config.h"
#include "display.h"
#include "voice.h"

uint8_t* pcm_buffer = NULL;
AudioGeneratorMP3* mp3 = NULL;
AudioFileSourceLittleFS* file = NULL;
AudioOutputI2S* out = NULL;
uint32_t playbackStartedMs = 0;

namespace {

// Una espera infinita aca convierte cualquier problema del microfono en un
// cuelgue permanente de loop(): el bucle deja de leer el sensor y de alimentar
// al decodificador, y la placa parece muerta aunque la red siga andando. Con un
// tope, una falla se ve y se sigue.
constexpr uint32_t MIC_READ_TIMEOUT_MS = 200;

// El microfono usa el driver I2S nuevo de IDF 5.x. El viejo (driver/i2s.h) sigue
// compilando pero con PDM devuelve cero muestras. ESP8266Audio, que se queda con
// el driver viejo para la salida, no molesta: vive en otra unidad de compilacion
// y en el puerto 1, y el bloqueo entre drivers de IDF es por puerto.
i2s_chan_handle_t micChannel = NULL;

bool micStep(const char* what, esp_err_t result) {
  if (result == ESP_OK) return true;
  Serial.printf("❌ Microfono: %s fallo (%s).\n", what, esp_err_to_name(result));
  return false;
}

void initMicrophone() {
  i2s_chan_config_t channelConfig = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  channelConfig.dma_desc_num = 8;
  channelConfig.dma_frame_num = 512;

  if (!micStep("i2s_new_channel", i2s_new_channel(&channelConfig, NULL, &micChannel))) {
    micChannel = NULL;
    return;
  }

  i2s_pdm_rx_config_t pdmConfig = {
    .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
    .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
    .gpio_cfg = {
      .clk = (gpio_num_t)MIC_PDM_CLK,
      .din = (gpio_num_t)MIC_PDM_DATA,
      .invert_flags = {.clk_inv = false},
    },
  };

  if (!micStep("init_pdm_rx_mode", i2s_channel_init_pdm_rx_mode(micChannel, &pdmConfig)) ||
      !micStep("channel_enable", i2s_channel_enable(micChannel))) {
    i2s_del_channel(micChannel);
    micChannel = NULL;
  }
}

// Una lectura corta al arrancar dice si el microfono entrega muestras, sin tener
// que esperar a que alguien toque el sensor.
void probeMicrophone() {
  if (pcm_buffer == NULL) return;

  if (micChannel == NULL) return;

  size_t bytesRead = 0;
  esp_err_t result =
      i2s_channel_read(micChannel, pcm_buffer, 1024, &bytesRead, MIC_READ_TIMEOUT_MS);
  Serial.printf("🎤 Microfono: %s, %u bytes en la primera lectura.\n",
                esp_err_to_name(result), (unsigned)bytesRead);
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

  // Por defecto la libreria reserva 8 x 128 frames: a 24 kHz son 43 ms de audio
  // en el DMA. La tarea de WireGuard corre en prioridad 7 sobre el mismo nucleo
  // que loopTask —que es quien alimenta al decodificador, en prioridad 1— y su
  // barrido periodico llega a tardar mas que eso, asi que la salida se queda
  // sin datos.
  //
  // Y quedarse sin datos no suena a hueco: el DMA de I2S arranca con
  // auto_clear en false, o sea que repite el buffer viejo. Se escucha como una
  // palabra dicha dos veces. ESP8266Audio no expone ese campo, asi que la
  // defensa es que el buffer no llegue a vaciarse.
  //
  // 24 x 256 son ~256 ms de colchon por 24 KB de RAM interna, que sobran desde
  // que los malloc grandes salen de PSRAM.
  out->SetBuffers(24, 256 * 4);

  out->SetPinout(I2S_BCLK, I2S_LRC, I2S_DIN);
  out->SetGain(0.5);
  mp3 = new AudioGeneratorMP3();
  initMicrophone();
  probeMicrophone();
}

void recordWhileTouched() {
  if (!pcm_buffer) return;

  Serial.println("🎙️ Sensor Tocado: Grabando...");
  setFaceMode(FACE_RECORDING);

  size_t bytesRead = 0;
  size_t totalBytes = 0;
  esp_err_t primed = micChannel != NULL
                         ? i2s_channel_read(micChannel, pcm_buffer, 1024, &bytesRead,
                                            MIC_READ_TIMEOUT_MS)
                         : ESP_ERR_INVALID_STATE;
  if (primed != ESP_OK || bytesRead == 0) {
    Serial.printf("❌ El microfono no entrega muestras (%s).\n", esp_err_to_name(primed));
    setFaceMode(FACE_IDLE);
    return;
  }

  while (digitalRead(TOUCH_PIN) == HIGH && totalBytes < PCM_BUFFER_LEN) {
    if (i2s_channel_read(micChannel, pcm_buffer + totalBytes, 1024, &bytesRead,
                         MIC_READ_TIMEOUT_MS) != ESP_OK) break;
    if (bytesRead == 0) break;  // el microfono se quedo mudo a mitad de la grabacion
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
    uint32_t elapsed = millis() - playbackStartedMs;
    uint32_t position = file != NULL ? file->getPos() : 0;

    // Cortarse en los primeros instantes no es un final: es una falla.
    if (elapsed < 200) {
      Serial.printf("⚠️ Se cortó a los %lu ms, tras leer %lu bytes del MP3.\n",
                    (unsigned long)elapsed, (unsigned long)position);
      if (retryPlayback()) {
        Serial.println("   reintentando con el mismo archivo...");
        return;
      }
    }

    mp3->stop();
    if (file) {
      delete file;
      file = NULL;
    }
    Serial.printf("✅ Reproducción finalizada tras %lu ms (leyó %lu bytes).\n",
                  (unsigned long)elapsed, (unsigned long)position);
    setFaceMode(FACE_IDLE);
  }
}
