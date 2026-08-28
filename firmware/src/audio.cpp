#include <Arduino.h>
#include <driver/i2s_pdm.h>
#include "audio.h"
#include "device_config.h"
#include "display.h"
#include "voice.h"
#include "wakeword.h"

uint8_t* pcm_buffer = NULL;
AudioGeneratorMP3* mp3 = NULL;
AudioFileSource* file = NULL;
AudioOutputI2S* out = NULL;
uint32_t playbackStartedMs = 0;
uint32_t interactionStartedMs = 0;

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

  // El micrófono entrega poco nivel: medido a la distancia normal de uso, la voz
  // pica entre 560 y 977 sobre 32767, menos del 3% de la escala. Se probaron dos
  // arreglos del lado del hardware y ninguno movió la aguja:
  //
  //  - amplify_num, el multiplicador del PDM, no existe en el S3 (define
  //    SOC_I2S_SUPPORTS_PDM_RX pero no ..._HP_FILTER, así que el campo ni se
  //    compila).
  //  - I2S_PDM_DSR_16S, para sacar al micrófono de un posible modo de bajo
  //    consumo por reloj lento, dejó el pico igual: 747, 686, 672.
  //
  // Así que el nivel se corrige por software, en cleanAndAmplifyAudio.
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

// Pico de la grabación antes de amplificar, y ganancia aplicada. Un pico bajo
// con la ganancia pegada al tope significa que al micrófono le llegó poca señal,
// que es lo que después se escucha como "no me entiende".
int16_t lastPeak = 0;
float lastGain = 1.0;
bool firstLoopReported = false;
uint32_t ultimoDecodeMs = 0;
uint32_t peorHuecoDecodeMs = 0;

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

  // El tope de 6x se quedaba corto por mucho: con un pico de 800 hacían falta
  // 32x para llegar al objetivo, así que la normalización nunca normalizaba y a
  // Whisper le llegaba una grabación al 15% de la escala. Amplificar no mejora
  // la relación señal/ruido —el ruido sube con la señal— pero sí pone el nivel
  // donde el reconocedor lo espera, y con eso alcanzó.
  float gain = 26000.0 / maxValue;
  if (gain > 30.0) gain = 30.0;
  if (gain < 1.0) gain = 1.0;
  lastPeak = maxValue;
  lastGain = gain;

  for (size_t index = 0; index < sampleCount; index++) {
    int32_t value = (int32_t)(samples[index] * gain);
    if (value > 32767) value = 32767;
    if (value < -32768) value = -32768;
    samples[index] = (int16_t)value;
  }
}
}

SemaphoreHandle_t audioMutex = NULL;

bool audioLock(uint32_t timeoutMs) {
  if (audioMutex == NULL) return true;  // todavía no arrancó la tarea
  return xSemaphoreTake(audioMutex, pdMS_TO_TICKS(timeoutMs)) == pdTRUE;
}

void audioUnlock() {
  if (audioMutex != NULL) xSemaphoreGive(audioMutex);
}

namespace {

// Prioridad 8: por encima de ml_wg_mgr, que corre en 7 sobre este mismo núcleo y
// es quien dejaba sin comer al decodificador. Y con vTaskDelay en cada vuelta,
// que es lo que hace que sea seguro estar tan arriba: cede el procesador siempre,
// asi que no puede ahogar al tunel ni a nada mas. Subirle la prioridad a loop()
// en vez de esto no servia: la escritura de I2S es no bloqueante, asi que con el
// buffer lleno loop() giraria en vacio reteniendo el nucleo.
void audioTask(void* parametro) {
  (void)parametro;
  for (;;) {
    updateAudioPlayback();
    vTaskDelay(1);  // 1 ms; un cuadro de MP3 son 48 ms de audio, sobra de lejos
  }
}

}  // namespace

void startAudioTask() {
  if (audioMutex == NULL) audioMutex = xSemaphoreCreateMutex();
  xTaskCreatePinnedToCore(audioTask, "audio", 4096, NULL, 8, NULL, 1);
}

bool micRead(void* destino, size_t bytes, size_t* leidos, uint32_t timeoutMs) {
  if (micChannel == NULL) return false;
  return i2s_channel_read(micChannel, destino, bytes, leidos, timeoutMs) == ESP_OK;
}

void normalizeRecording(size_t totalBytes) {
  if (pcm_buffer == NULL || totalBytes == 0) return;
  cleanAndAmplifyAudio(pcm_buffer, totalBytes);
  Serial.printf("[etapas] pico=%d  ganancia=%.1fx  (%.1fs de audio)\n",
                (int)lastPeak, lastGain, totalBytes / 32000.0f);
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

  const uint32_t entradaMs = millis();
  // La escucha continua tiene el micrófono ocupado; hay que pedírselo prestado.
  pauseWakeWord();
  Serial.println("🎙️ Sensor Tocado: Grabando...");
  setFaceMode(FACE_RECORDING);

  size_t bytesRead = 0;
  size_t totalBytes = 0;
  esp_err_t primed = micChannel != NULL
                         ? i2s_channel_read(micChannel, pcm_buffer, 1024, &bytesRead,
                                            MIC_READ_TIMEOUT_MS)
                         : ESP_ERR_INVALID_STATE;
  const uint32_t primeraMuestraMs = millis();
  if (primed != ESP_OK || bytesRead == 0) {
    Serial.printf("❌ El microfono no entrega muestras (%s).\n", esp_err_to_name(primed));
    setFaceMode(FACE_IDLE);
    resumeWakeWord();
    return;
  }

  while (digitalRead(TOUCH_PIN) == HIGH && totalBytes < PCM_BUFFER_LEN) {
    if (i2s_channel_read(micChannel, pcm_buffer + totalBytes, 1024, &bytesRead,
                         MIC_READ_TIMEOUT_MS) != ESP_OK) break;
    if (bytesRead == 0) break;  // el microfono se quedo mudo a mitad de la grabacion
    totalBytes += bytesRead;
  }

  Serial.printf("🛑 TTP223 Liberado: Grabación finalizada (%d bytes).\n", totalBytes);
  const uint32_t finGrabacionMs = millis();
  if (totalBytes > 0) {
    cleanAndAmplifyAudio(pcm_buffer, totalBytes);
    Serial.printf(
        "[etapas] toque->grabando=%lums  primera_muestra=%lums  grabacion=%lums (%.1fs de audio)"
        "  pico=%d  ganancia=%.1fx\n",
        (unsigned long)(entradaMs - interactionStartedMs),
        (unsigned long)(primeraMuestraMs - entradaMs),
        (unsigned long)(finGrabacionMs - entradaMs),
        totalBytes / 32000.0f, (int)lastPeak, lastGain);
    sendAudioAndPlayResponse(totalBytes);
  } else {
    setFaceMode(FACE_IDLE);
  }
  resumeWakeWord();
}

void updateAudioPlayback() {
  if (!mp3->isRunning()) {
    firstLoopReported = false;
    return;
  }

  // Sin espera larga: si loop() esta manipulando el decodificador, esta vuelta se
  // saltea y se reintenta en 1 ms. Bloquear aca seria bloquear la prioridad 8.
  if (!audioLock(2)) return;
  if (!mp3->isRunning()) {
    audioUnlock();
    return;
  }

  // Cuánto tarda loop() en volver a alimentar al decodificador después de que
  // arrancó la reproducción. Si esto es grande, la cara de hablar ya cambió pero
  // todavía no salió audio, y el desfasaje es CPU robada a loopTask.
  if (!firstLoopReported) {
    firstLoopReported = true;
    ultimoDecodeMs = millis();
    peorHuecoDecodeMs = 0;
    Serial.printf("[etapas] arranque->primer_decode=%lums  desde_el_toque=%lums\n",
                  (unsigned long)(millis() - playbackStartedMs),
                  (unsigned long)(millis() - interactionStartedMs));
  }

  // El DMA de salida tiene 256 ms de colchón. Si loop() tarda más que eso en
  // volver a alimentar al decodificador, el buffer se vacía —y como arranca con
  // auto_clear en false, repite lo viejo en vez de callarse: eso es lo que se
  // escucha como un trabón o una palabra dicha dos veces. Este número dice si
  // pasó y por cuánto, en vez de dejarlo a la percepción.
  const uint32_t ahoraDecode = millis();
  const uint32_t huecoDecode = ahoraDecode - ultimoDecodeMs;
  if (huecoDecode > peorHuecoDecodeMs) peorHuecoDecodeMs = huecoDecode;
  ultimoDecodeMs = ahoraDecode;

  if (!mp3->loop()) {
    uint32_t elapsed = millis() - playbackStartedMs;
    uint32_t position = file != NULL ? file->getPos() : 0;

    // Cortarse en los primeros instantes no es un final: es una falla.
    if (elapsed < 200) {
      Serial.printf("⚠️ Se cortó a los %lu ms, tras leer %lu bytes del MP3.\n",
                    (unsigned long)elapsed, (unsigned long)position);
      if (retryPlayback()) {
        Serial.println("   reintentando con el mismo archivo...");
        audioUnlock();
        return;
      }
    }

    mp3->stop();
    if (file) {
      delete file;
      file = NULL;
    }
    Serial.printf("✅ Reproducción finalizada tras %lu ms (leyó %lu bytes).  peor_hueco=%lums\n",
                  (unsigned long)elapsed, (unsigned long)position,
                  (unsigned long)peorHuecoDecodeMs);
    setFaceMode(FACE_IDLE);
  }
  audioUnlock();
}
