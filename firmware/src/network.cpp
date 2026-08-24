#include <Arduino.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <WiFi.h>
#include "audio.h"
#include "backend.h"
#include "commands.h"
#include "device_config.h"
#include "display.h"
#include "network.h"

namespace {

constexpr const char* RESPONSE_PATH = "/response.mp3";
constexpr const char* BOUNDARY = "----ESP32Boundary987654321";
constexpr uint32_t REQUEST_TIMEOUT_MS = 20000;
// Espera máxima por el lock si la tarea de mantenimiento está usando la red.
constexpr uint32_t NETWORK_LOCK_WAIT_MS = 8000;

// El cuerpo multipart se arma entero en PSRAM y la respuesta la lee HTTPClient.
// Antes ambas cosas se hacían a mano sobre el socket: funcionaba hablándole
// directo a uvicorn, pero deja al firmware a merced de cualquier detalle de
// framing (chunked, keep-alive, particularidades de leer sobre TLS) que meta un
// proxy en el medio. Con HTTPClient eso lo resuelve la librería.
uint8_t* requestBuffer = NULL;
size_t requestCapacity = 0;

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

void startPlayback(size_t fileSize) {
  setFaceMode(FACE_SPEAKING);
  file = new AudioFileSourceLittleFS(RESPONSE_PATH);
  bool started = mp3->begin(file, out);
  playbackStartedMs = millis();
  Serial.printf("🔊 Reproduciendo %u bytes (begin=%s).\n",
                (unsigned)fileSize, started ? "ok" : "FALLÓ");
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

  if (!backendLock(NETWORK_LOCK_WAIT_MS)) {
    Serial.println("❌ La red quedó ocupada demasiado tiempo.");
    setFaceMode(FACE_IDLE);
    return;
  }

  HTTPClient http;
  http.setTimeout(REQUEST_TIMEOUT_MS);
  http.setReuse(false);

  Serial.printf("📡 Enviando %u bytes a %s (%s)...\n", (unsigned)bodyLength,
                backendHost().c_str(), backendUsesTls() ? "TLS" : "sin cifrar");

  if (!beginBackendRequest(http, String(server_url))) {
    Serial.println("❌ Error al preparar el pedido.");
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return;
  }

  http.addHeader("Content-Type", String("multipart/form-data; boundary=") + BOUNDARY);
  const char* collected[] = {"X-Action"};
  http.collectHeaders(collected, 1);

  int status = http.sendRequest("POST", requestBuffer, bodyLength);
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
  if (savedResponse) savedResponse.close();

  if (fileSize == 0) {
    Serial.println("❌ El MP3 quedó vacío en flash.");
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return;
  }

  if (action.length() > 0) executeDeviceCommand(action);
  startPlayback(fileSize);

  // El lock se suelta recién acá. Si se soltaba antes, la tarea de mantenimiento
  // veía mp3->isRunning() todavía en false, arrancaba un handshake TLS —CPU pura
  // en el mismo núcleo que loop()— y le robaba el procesador al decodificador
  // justo en los primeros segundos de la reproducción.
  backendUnlock();
}
