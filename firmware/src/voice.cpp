// WiFi.h primero: ver la nota en backend.h sobre el orden de los includes.
#include <WiFi.h>

#include <Arduino.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include "audio.h"
#include "backend.h"
#include "commands.h"
#include "device_config.h"
#include "display.h"
#include "voice.h"
#include "tailnet.h"

namespace {

constexpr const char* RESPONSE_PATH = "/response.mp3";
constexpr const char* BOUNDARY = "----ESP32Boundary987654321";
// El backend encadena cuatro APIs remotas (STT, embeddings, el modelo y el TTS).
// Medido sobre la Pi: entre 5 y 11 s lo habitual, pero con cola larga —Gemini
// solo pasa de 1.6 a 8.3 s entre pedidos consecutivos, y un pedido medido tardo
// 30 s—. Con 20 s se perdian respuestas que el backend igual estaba generando.
constexpr uint32_t REQUEST_TIMEOUT_MS = 45000;
// Espera máxima por el lock si la tarea de mantenimiento está usando la red.
constexpr uint32_t NETWORK_LOCK_WAIT_MS = 8000;

// El cuerpo multipart se arma entero en PSRAM y la respuesta la lee HTTPClient.
// Antes ambas cosas se hacían a mano sobre el socket: funcionaba hablándole
// directo a uvicorn, pero deja al firmware a merced de cualquier detalle de
// framing (chunked, keep-alive, particularidades de leer sobre TLS) que meta un
// proxy en el medio. Con HTTPClient eso lo resuelve la librería.
uint8_t* requestBuffer = NULL;
size_t requestCapacity = 0;
uint8_t playbackRetries = 0;

bool ensureRequestBuffer(size_t needed) {
  if (requestBuffer != NULL && requestCapacity >= needed) return true;
  if (requestBuffer != NULL) free(requestBuffer);

  requestBuffer = static_cast<uint8_t*>(ps_malloc(needed));
  if (requestBuffer == NULL) requestBuffer = static_cast<uint8_t*>(malloc(needed));
  requestCapacity = requestBuffer != NULL ? needed : 0;

  if (requestBuffer == NULL) Serial.println("❌ Sin memoria para el cuerpo del pedido.");
  return requestBuffer != NULL;
}

size_t buildMultipartBody(size_t recordedPcmBytes) {
  String head = String("--") + BOUNDARY + "\r\n";
  head += "Content-Disposition: form-data; name=\"session_id\"\r\n\r\n";
  head += "esp32_session\r\n";
  head += String("--") + BOUNDARY + "\r\n";
  head += "Content-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n";
  head += "Content-Type: audio/wav\r\n\r\n";
  String tail = String("\r\n--") + BOUNDARY + "--\r\n";

  WavHeader header;
  header.subchunk2Size = recordedPcmBytes;
  header.chunkSize = 36 + recordedPcmBytes;

  size_t total = head.length() + sizeof(WavHeader) + recordedPcmBytes + tail.length();
  if (!ensureRequestBuffer(total)) return 0;

  size_t offset = 0;
  memcpy(requestBuffer + offset, head.c_str(), head.length());
  offset += head.length();
  memcpy(requestBuffer + offset, &header, sizeof(WavHeader));
  offset += sizeof(WavHeader);
  memcpy(requestBuffer + offset, pcm_buffer, recordedPcmBytes);
  offset += recordedPcmBytes;
  memcpy(requestBuffer + offset, tail.c_str(), tail.length());
  offset += tail.length();

  return offset;
}

// Un MP3 de edge-tts empieza en un frame sync (0xFF 0xFB/0xF3) o en un tag ID3.
// Si lo que quedó en flash arranca con otra cosa, el decodificador aborta sin
// emitir nada y este volcado dice exactamente con qué.
void dumpAt(File& saved, const char* label, size_t offset) {
  uint8_t bytes[16] = {0};
  saved.seek(offset);
  size_t read = saved.read(bytes, sizeof(bytes));

  String hex;
  size_t zeros = 0;
  for (size_t index = 0; index < read; index++) {
    char pair[4];
    snprintf(pair, sizeof(pair), "%02x ", bytes[index]);
    hex += pair;
    if (bytes[index] == 0) zeros++;
  }
  Serial.printf("   %-6s @%-7u %s%s\n", label, (unsigned)offset, hex.c_str(),
                zeros == read ? " (todo ceros)" : "");
}

// Recorre el MP3 frame por frame. Mirar tres muestras sueltas no alcanza para
// saber si el archivo esta intacto: esto camina la cadena entera y dice hasta
// donde llega, que es exactamente lo que hace el decodificador antes de rendirse.
void walkMp3Frames(File& saved, size_t fileSize) {
  static const uint16_t BITRATE_MPEG1[16] =
    {0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0};
  static const uint16_t BITRATE_MPEG2[16] =
    {0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0};
  static const uint32_t RATE_MPEG1[4] = {44100, 48000, 32000, 0};
  static const uint32_t RATE_MPEG2[4] = {22050, 24000, 16000, 0};
  static const uint32_t RATE_MPEG25[4] = {11025, 12000, 8000, 0};

  size_t offset = 0;
  uint32_t frames = 0;
  uint32_t totalMs = 0;
  uint32_t firstRate = 0;
  uint16_t firstBitrate = 0;

  while (offset + 4 <= fileSize) {
    uint8_t header[4];
    saved.seek(offset);
    if (saved.read(header, 4) != 4) break;

    if (header[0] != 0xFF || (header[1] & 0xE0) != 0xE0) break;

    uint8_t versionBits = (header[1] >> 3) & 0x03;
    uint8_t layerBits = (header[1] >> 1) & 0x03;
    if (versionBits == 1 || layerBits != 1) break;  // reservado, o no es Layer III

    bool isMpeg1 = versionBits == 3;
    uint8_t bitrateIndex = (header[2] >> 4) & 0x0F;
    uint8_t rateIndex = (header[2] >> 2) & 0x03;
    uint8_t padding = (header[2] >> 1) & 0x01;

    uint16_t bitrate = isMpeg1 ? BITRATE_MPEG1[bitrateIndex] : BITRATE_MPEG2[bitrateIndex];
    uint32_t rate = isMpeg1 ? RATE_MPEG1[rateIndex]
                            : (versionBits == 2 ? RATE_MPEG2[rateIndex] : RATE_MPEG25[rateIndex]);
    if (bitrate == 0 || rate == 0) break;

    // Layer III: 1152 muestras por frame en MPEG1, 576 en MPEG2/2.5.
    uint32_t samples = isMpeg1 ? 1152 : 576;
    size_t frameSize = (samples / 8) * bitrate * 1000 / rate + padding;
    if (frameSize < 4) break;

    if (frames == 0) {
      firstRate = rate;
      firstBitrate = bitrate;
    }
    frames++;
    totalMs += samples * 1000 / rate;
    offset += frameSize;
  }

  Serial.printf("   %lu frames, %lu kbps, %lu Hz, %lu ms de audio\n",
                (unsigned long)frames, (unsigned long)firstBitrate,
                (unsigned long)firstRate, (unsigned long)totalMs);

  if (offset >= fileSize) {
    Serial.println("   cadena de frames completa: el MP3 esta intacto");
  } else {
    Serial.printf("   ⚠️ la cadena se corta en el offset %u de %u (%.1f%%)\n",
                  (unsigned)offset, (unsigned)fileSize,
                  fileSize ? (100.0 * offset / fileSize) : 0.0);
  }
}

void inspectSavedResponse(File& saved, size_t fileSize) {
  Serial.printf("⬇️ %u bytes | heap libre %u | mayor bloque %u\n",
                (unsigned)fileSize, (unsigned)ESP.getFreeHeap(),
                (unsigned)ESP.getMaxAllocHeap());

  dumpAt(saved, "inicio", 0);
  if (fileSize > 64) dumpAt(saved, "medio", fileSize / 2);
  if (fileSize > 32) dumpAt(saved, "final", fileSize - 16);
  saved.seek(0);

  walkMp3Frames(saved, fileSize);
  saved.seek(0);
}

void startPlayback(size_t fileSize) {
  setFaceMode(FACE_SPEAKING);
  file = new AudioFileSourceLittleFS(RESPONSE_PATH);
  bool started = mp3->begin(file, out);
  playbackStartedMs = millis();
  playbackRetries = 0;
  Serial.printf("🔊 Reproduciendo %u bytes (begin=%s, fuente=%s, ve %u bytes).\n",
                (unsigned)fileSize, started ? "ok" : "FALLÓ",
                file->isOpen() ? "abierta" : "CERRADA", (unsigned)file->getSize());
  if (!started) setFaceMode(FACE_IDLE);
}

}  // namespace

void sendAudioAndPlayResponse(size_t recordedPcmBytes) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("❌ Sin conexión Wi-Fi.");
    setFaceMode(FACE_IDLE);
    return;
  }

  setFaceMode(FACE_WAITING);
  if (mp3->isRunning()) mp3->stop();
  if (file) {
    delete file;
    file = NULL;
  }

  size_t bodyLength = buildMultipartBody(recordedPcmBytes);
  if (bodyLength == 0) {
    setFaceMode(FACE_IDLE);
    return;
  }

  const uint32_t antesLockMs = millis();
  if (!backendLock(NETWORK_LOCK_WAIT_MS)) {
    Serial.println("❌ La red quedó ocupada demasiado tiempo.");
    setFaceMode(FACE_IDLE);
    return;
  }

  HTTPClient http;
  http.setTimeout(REQUEST_TIMEOUT_MS);
  http.setReuse(false);

  Serial.printf("📡 Enviando %u bytes a %s (%s)...\n", (unsigned)bodyLength,
                backendHost().c_str(), tailnetEnabled() ? "tailnet" : "LAN");

  // La URL sale de backendVoiceUrl() y no de server_url porque dentro del
  // tailnet el host viaja ya resuelto a su IP 100.x.
  if (!beginBackendRequest(http, backendVoiceUrl())) {
    Serial.println("❌ Error al preparar el pedido.");
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return;
  }

  http.addHeader("Content-Type", String("multipart/form-data; boundary=") + BOUNDARY);
  const char* collected[] = {"X-Action"};
  http.collectHeaders(collected, 1);

  const uint32_t antesPedidoMs = millis();
  int status = http.sendRequest("POST", requestBuffer, bodyLength);
  const uint32_t cabeceraMs = millis();
  if (status != HTTP_CODE_OK) {
    Serial.printf("❌ El backend respondió %d.\n", status);
    http.end();
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return;
  }

  File responseFile = LittleFS.open(RESPONSE_PATH, "w");
  if (!responseFile) {
    Serial.println("❌ No pude abrir /response.mp3 para escribir.");
    http.end();
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return;
  }

  // writeToStream decodifica el chunked si lo hubiera.
  int written = http.writeToStream(&responseFile);
  const uint32_t descargaMs = millis();
  responseFile.flush();
  responseFile.close();

  String action = http.header("X-Action");
  http.end();

  if (written <= 0) {
    Serial.printf("❌ No llegó cuerpo en la respuesta (%d).\n", written);
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return;
  }

  // El tamaño hay que leerlo con el archivo ya cerrado: en modo escritura,
  // File::size() devuelve el que tenía al abrirlo, o sea cero.
  File savedResponse = LittleFS.open(RESPONSE_PATH, "r");
  size_t fileSize = savedResponse ? savedResponse.size() : 0;
  if (savedResponse) {
    inspectSavedResponse(savedResponse, fileSize);
    savedResponse.close();
  }

  if (fileSize == 0) {
    Serial.println("❌ El MP3 quedó vacío en flash.");
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return;
  }

  const uint32_t inspeccionMs = millis();
  if (action.length() > 0) executeDeviceCommand(action);
  startPlayback(fileSize);
  Serial.printf(
      "[etapas] espera_lock=%lums  pedido=%lums  descarga=%lums  revision=%lums  arranque=%lums\n",
      (unsigned long)(antesPedidoMs - antesLockMs),
      (unsigned long)(cabeceraMs - antesPedidoMs),
      (unsigned long)(descargaMs - cabeceraMs),
      (unsigned long)(inspeccionMs - descargaMs),
      (unsigned long)(millis() - inspeccionMs));

  // Cerrar el socket va despues de que la reproduccion abrio su archivo. Al
  // reves, el descriptor recien abierto quedaba invalido: getPos() devolvia -1
  // en el primer loop() del decodificador, y solo reabrirlo lo arreglaba.
  backendDisconnect();

  // El lock se suelta recién acá. Si se soltaba antes, la tarea de mantenimiento
  // veía mp3->isRunning() todavía en false, salía a la red en el mismo núcleo
  // que loop() y le robaba el procesador al decodificador justo en los primeros
  // segundos de la reproducción.
  backendUnlock();
}

bool retryPlayback() {
  // El archivo ya se verifico frame por frame, asi que si falla al instante el
  // problema no es el MP3 sino el estado del decodificador o de la salida justo
  // despues del pedido. Reintentar desde cero lo distingue: si a la segunda
  // suena, es estado transitorio; si vuelve a fallar, no.
  if (playbackRetries >= 1) return false;
  playbackRetries++;

  mp3->stop();
  if (file) {
    delete file;
    file = NULL;
  }

  file = new AudioFileSourceLittleFS(RESPONSE_PATH);
  if (!mp3->begin(file, out)) {
    Serial.println("   el reintento tampoco pudo arrancar");
    return false;
  }
  playbackStartedMs = millis();
  return true;
}
