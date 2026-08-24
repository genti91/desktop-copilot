#include <Arduino.h>
#include <WiFi.h>
#include <LittleFS.h>
#include "audio.h"
#include "backend.h"
#include "commands.h"
#include "device_config.h"
#include "display.h"
#include "network.h"

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

  String host = backendHost();
  uint16_t port = backendPort();
  String path = backendPath();

  // Plano en la LAN, TLS cuando la URL es el https:// de Tailscale Funnel.
  WiFiClient& client = backendTransport();
  Serial.printf("📡 Conectando a %s:%u (%s)...\n", host.c_str(), port,
                backendUsesTls() ? "TLS" : "sin cifrar");
  if (!client.connect(host.c_str(), port)) {
    Serial.println("❌ Error al conectar con FastAPI.");
    setFaceMode(FACE_IDLE);
    return;
  }

  String boundary = "----ESP32Boundary987654321";
  String bodyStart = "--" + boundary + "\r\n";
  bodyStart += "Content-Disposition: form-data; name=\"session_id\"\r\n\r\n";
  bodyStart += "esp32_session\r\n";
  bodyStart += "--" + boundary + "\r\n";
  bodyStart += "Content-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n";
  bodyStart += "Content-Type: audio/wav\r\n\r\n";
  String bodyEnd = "\r\n--" + boundary + "--\r\n";

  WavHeader header;
  header.subchunk2Size = recordedPcmBytes;
  header.chunkSize = 36 + recordedPcmBytes;
  size_t contentLength = bodyStart.length() + sizeof(WavHeader) + recordedPcmBytes + bodyEnd.length();

  // El puerto sólo va en Host si no es el que corresponde al esquema.
  bool defaultPort = port == (backendUsesTls() ? 443 : 80);
  String hostHeader = defaultPort ? host : host + ":" + String(port);

  client.printf("POST %s HTTP/1.1\r\n", path.c_str());
  client.printf("Host: %s\r\n", hostHeader.c_str());
  client.println("User-Agent: ESP32S3");
  client.println("Connection: close");
  // Fuera de la LAN el backend exige este token para atender al dispositivo.
  writeDeviceTokenHeader(client);
  client.printf("Content-Type: multipart/form-data; boundary=%s\r\n", boundary.c_str());
  client.printf("Content-Length: %d\r\n\r\n", contentLength);
  client.print(bodyStart);
  client.write((uint8_t*)&header, sizeof(WavHeader));

  constexpr size_t uploadChunkSize = 4096;
  for (size_t offset = 0; offset < recordedPcmBytes; offset += uploadChunkSize) {
    size_t length = min(uploadChunkSize, recordedPcmBytes - offset);
    client.write(pcm_buffer + offset, length);
  }

  client.print(bodyEnd);
  Serial.println("📡 Audio enviado a FastAPI. Esperando respuesta...");

  unsigned long timeout = millis();
  while (client.connected() && !client.available()) {
    if (millis() - timeout > 15000) {
      Serial.println("❌ Timeout esperando la respuesta del servidor.");
      client.stop();
      setFaceMode(FACE_IDLE);
      return;
    }
    delay(10);
  }

  bool isBody = false;
  File responseFile = LittleFS.open("/response.mp3", "w");
  while (client.connected() || client.available()) {
    if (!isBody) {
      String line = client.readStringUntil('\n');
      line.trim();
      if (line.startsWith("x-action: ")) executeDeviceCommand(line.substring(10));
      if (line.length() == 0) isBody = true;
    } else {
      uint8_t buffer[512];
      size_t length = client.read(buffer, sizeof(buffer));
      if (length > 0 && responseFile) responseFile.write(buffer, length);
    }
  }

  if (responseFile) responseFile.close();
  client.stop();
  Serial.println("🔊 Respuesta guardada en flash (/response.mp3). Reproduciendo...");
  setFaceMode(FACE_SPEAKING);
  file = new AudioFileSourceLittleFS("/response.mp3");
  mp3->begin(file, out);
}
