// WiFi.h primero: ver la nota en backend.h sobre el orden de los includes.
#include <WiFi.h>

#include <Arduino.h>
#include <HTTPClient.h>
#include "audio.h"
#include "backend.h"
#include "commands.h"
#include "device_config.h"
#include "display.h"
#include "voice.h"
#include "tailnet.h"

namespace {

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

// La respuesta ya no pasa por LittleFS. Escribirla a flash congelaba la
// animación de la cara mientras bajaba: cada escritura deshabilita la caché de
// flash en los DOS núcleos, y el código de la cara vive en flash. Reproducir
// desde PSRAM saca la flash del camino, tanto al bajar como al reproducir.
//
// Las respuestas medidas van de 20 a 155 KB; 256 KB dejan aire de sobra y en
// PSRAM no compiten con nada.
constexpr size_t RESPONSE_CAPACITY = 256 * 1024;
uint8_t* responseBuffer = NULL;
size_t responseCapacity = 0;
size_t responseSize = 0;

// Destino en memoria para HTTPClient::writeToStream, que es quien sabe deshacer
// el chunked. Descarta lo que no entra en vez de escribir fuera del buffer, y
// deja constancia para que un desborde no pase por un audio cortado a secas.
class ResponseSink : public Stream {
 public:
  ResponseSink(uint8_t* destino, size_t capacidad)
      : destino_(destino), capacidad_(capacidad) {}

  size_t write(uint8_t byte) override { return write(&byte, 1); }

  size_t write(const uint8_t* datos, size_t largo) override {
    recibidos_ += largo;
    const size_t entra = guardados_ + largo <= capacidad_ ? largo : capacidad_ - guardados_;
    if (entra > 0) {
      memcpy(destino_ + guardados_, datos, entra);
      guardados_ += entra;
    }
    return largo;  // se le miente al emisor: cortar aca dejaria el socket a medias
  }

  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }

  size_t guardados() const { return guardados_; }
  size_t recibidos() const { return recibidos_; }
  bool desbordo() const { return recibidos_ > guardados_; }

 private:
  uint8_t* destino_;
  size_t capacidad_;
  size_t guardados_ = 0;
  size_t recibidos_ = 0;
};

bool ensureResponseBuffer(size_t needed) {
  if (responseBuffer != NULL && responseCapacity >= needed) return true;
  if (responseBuffer != NULL) free(responseBuffer);

  responseBuffer = static_cast<uint8_t*>(ps_malloc(needed));
  responseCapacity = responseBuffer != NULL ? needed : 0;

  if (responseBuffer == NULL) Serial.println("❌ Sin memoria para la respuesta de audio.");
  return responseBuffer != NULL;
}

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
void dumpAt(const uint8_t* datos, const char* label, size_t offset, size_t largo) {
  uint8_t bytes[16] = {0};
  size_t read = offset < largo ? min(sizeof(bytes), largo - offset) : 0;
  if (read) memcpy(bytes, datos + offset, read);

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
void walkMp3Frames(const uint8_t* datos, size_t fileSize) {
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
    const uint8_t* header = datos + offset;

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

void inspectSavedResponse(const uint8_t* datos, size_t fileSize) {
  Serial.printf("⬇️ %u bytes | heap libre %u | mayor bloque %u\n",
                (unsigned)fileSize, (unsigned)ESP.getFreeHeap(),
                (unsigned)ESP.getMaxAllocHeap());

  dumpAt(datos, "inicio", 0, fileSize);
  if (fileSize > 64) dumpAt(datos, "medio", fileSize / 2, fileSize);
  if (fileSize > 32) dumpAt(datos, "final", fileSize - 16, fileSize);

  walkMp3Frames(datos, fileSize);
}

void startPlayback(size_t fileSize) {
  responseSize = fileSize;
  setFaceMode(FACE_SPEAKING);
  file = new AudioFileSourcePROGMEM(responseBuffer, fileSize);
  bool started = mp3->begin(file, out);
  playbackStartedMs = millis();
  playbackRetries = 0;
  Serial.printf("🔊 Reproduciendo %u bytes (begin=%s, fuente=%s, ve %u bytes).\n",
                (unsigned)fileSize, started ? "ok" : "FALLÓ",
                file->isOpen() ? "abierta" : "CERRADA", (unsigned)file->getSize());
  if (!started) setFaceMode(FACE_IDLE);
}

}  // namespace

bool sendAudioAndPlayResponse(size_t recordedPcmBytes) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("❌ Sin conexión Wi-Fi.");
    setFaceMode(FACE_IDLE);
    return false;
  }

  setFaceMode(FACE_WAITING);
  // El decodificador ahora lo alimenta su propia tarea en prioridad 8: pararlo y
  // liberar la fuente desde aca sin el candado seria arrancarle el archivo de las
  // manos a mitad de una lectura.
  audioLock(1000);
  if (mp3->isRunning()) mp3->stop();
  if (file) {
    delete file;
    file = NULL;
  }
  audioUnlock();

  size_t bodyLength = buildMultipartBody(recordedPcmBytes);
  if (bodyLength == 0) {
    setFaceMode(FACE_IDLE);
    return false;
  }

  const uint32_t antesLockMs = millis();
  if (!backendLock(NETWORK_LOCK_WAIT_MS)) {
    Serial.println("❌ La red quedó ocupada demasiado tiempo.");
    setFaceMode(FACE_IDLE);
    return false;
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
    return false;
  }

  http.addHeader("Content-Type", String("multipart/form-data; boundary=") + BOUNDARY);
  const char* collected[] = {"X-Action"};
  http.collectHeaders(collected, 1);

  const uint32_t antesPedidoMs = millis();
  int status = http.sendRequest("POST", requestBuffer, bodyLength);
  const uint32_t cabeceraMs = millis();
  // 204: el backend escuchó la grabación y decidió que no le hablaban a él
  // —ruido, o una de las muletillas que Whisper inventa sobre el silencio—. No
  // es un error: es el equipo quedándose callado, que es lo que corresponde.
  if (status == HTTP_CODE_NO_CONTENT) {
    Serial.println("🔇 El backend no encontró nada que contestar.");
    http.end();
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return false;
  }
  if (status != HTTP_CODE_OK) {
    Serial.printf("❌ El backend respondió %d.\n", status);
    http.end();
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return false;
  }

  if (!ensureResponseBuffer(RESPONSE_CAPACITY)) {
    http.end();
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return false;
  }

  // writeToStream decodifica el chunked si lo hubiera; el destino es memoria.
  ResponseSink sink(responseBuffer, RESPONSE_CAPACITY);
  int written = http.writeToStream(&sink);
  const uint32_t descargaMs = millis();

  String action = http.header("X-Action");
  http.end();

  if (written <= 0) {
    Serial.printf("❌ No llegó cuerpo en la respuesta (%d).\n", written);
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return false;
  }
  if (sink.desbordo()) {
    Serial.printf("⚠️ La respuesta no entró en el buffer (%u bytes de %u); se corta.\n",
                  (unsigned)sink.recibidos(), (unsigned)RESPONSE_CAPACITY);
  }

  const size_t fileSize = sink.guardados();
  if (fileSize == 0) {
    Serial.println("❌ La respuesta llegó vacía.");
    backendUnlock();
    setFaceMode(FACE_IDLE);
    return false;
  }
  inspectSavedResponse(responseBuffer, fileSize);

  const uint32_t inspeccionMs = millis();
  if (action.length() > 0) executeDeviceCommand(action);
  audioLock(1000);
  startPlayback(fileSize);
  audioUnlock();
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
  return true;
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

  file = new AudioFileSourcePROGMEM(responseBuffer, responseSize);
  if (!mp3->begin(file, out)) {
    Serial.println("   el reintento tampoco pudo arrancar");
    return false;
  }
  playbackStartedMs = millis();
  return true;
}
