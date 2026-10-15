// WiFi.h y FS.h primero, y TFT_eSPI sólo a través de display.h: ver las notas de
// backend.h y display.h sobre el orden. Incluir <TFT_eSPI.h> a mano acá rompe
// WebServer.h (entra por WiFiManager) con "FS was not declared".
#include <WiFi.h>
#include <FS.h>

#include <Arduino.h>

#include "esp_camera.h"
#include "img_converters.h"

#include "audio.h"
#include "backend.h"
#include "commands.h"
#include "device_config.h"
#include "display.h"
#include "tailnet.h"
#include "videocall.h"
#include "wakeword.h"

namespace {

// El relay del backend escucha en este puerto, aparte del 8000 de la API.
constexpr uint16_t CALL_RELAY_PORT = 8001;

constexpr uint32_t TX_INTERVAL_MS = 90;              // ~11 fps de subida
// Por el tailnet cada paquete pasa por WireGuard en el propio ESP, así que el
// enlace da bastante menos que la LAN: a 11 fps las colas se llenan y lo que se
// nota no es que falten cuadros sino el retardo. Menos cuadros y más compresión
// (un q20 ronda 4 KB contra los 8 del q12) lo bajan mucho más que subir el fps.
constexpr uint32_t TX_INTERVAL_TAILNET_MS = 200;     // ~5 fps
constexpr int JPEG_QUALITY_LAN = 12;
constexpr int JPEG_QUALITY_TAILNET = 20;
// Si al terminar de leer un cuadro ya hay este tanto de bytes esperando, vamos
// atrasados: ese cuadro se descarta sin decodificar y se pasa al siguiente. La
// ventana de recepción de lwIP son 5744 bytes, así que 4 KB encolados es tenerla
// casi llena: el que manda viene bastante adelante. Más abajo el umbral empieza
// a tirar cuadros buenos en LAN, donde alcanzan a entrar unos KB del siguiente
// mientras se decodifica el actual.
constexpr int RX_BACKLOG_DROP_BYTES = 4096;
// Cada cuánto se refresca la vista de la cámara propia (llamando / sonando).
// No hace falta más: es sólo para encuadrar, y decodificar cuesta.
constexpr uint32_t PREVIEW_INTERVAL_MS = 150;
// Franja negra abajo donde va el texto cuando atrás se ve la cámara propia.
constexpr int16_t OVERLAY_H = 46;
constexpr size_t MAX_JPEG_BYTES = 60 * 1024;         // un 240x240 q12 ronda 8 KB
// Cuánto se muestra "Llamando a ..." esperando que el otro inicie su llamada.
// Menos que el _PAIR_WAIT_TIMEOUT_S del relay (240 s) para colgar limpio primero.
constexpr uint32_t WAIT_PEER_MS = 180000;
constexpr uint32_t MAX_CALL_MS = 10UL * 60 * 1000;
constexpr uint32_t RX_STALL_MS = 8000;               // sin frames -> se cortó
constexpr uint32_t RING_MS = 35000;                  // cuánto suena la entrante
// Tras atender o rechazar, se ignora al mismo llamante este rato: el aviso del
// backend sigue vivo unos segundos y el poll lo volvería a traer.
constexpr uint32_t RING_COOLDOWN_MS = 50000;

// Pines de la cámara del XIAO ESP32-S3 Sense. Son fijos: van por el conector B2B
// de la placa de expansión, no por el header, así que no chocan con el display
// (7/8/9/44), el sensor táctil (4), los LED (5/6) ni el micrófono (41/42).
camera_config_t cameraConfig() {
  camera_config_t config = {};
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.pin_xclk = 10;
  config.pin_sccb_sda = 40;
  config.pin_sccb_scl = 39;
  config.pin_d7 = 48;
  config.pin_d6 = 11;
  config.pin_d5 = 12;
  config.pin_d4 = 14;
  config.pin_d3 = 16;
  config.pin_d2 = 18;
  config.pin_d1 = 17;
  config.pin_d0 = 15;
  config.pin_vsync = 38;
  config.pin_href = 47;
  config.pin_pclk = 13;
  config.xclk_freq_hz = 20000000;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_240X240;
  config.jpeg_quality = tailnetEnabled() ? JPEG_QUALITY_TAILNET : JPEG_QUALITY_LAN;
  config.fb_count = 2;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;
  return config;
}

volatile bool pendingCall = false;
char pendingTarget[DEVICE_NAME_SIZE] = {0};
volatile bool pendingIncoming = false;
char pendingFrom[DEVICE_NAME_SIZE] = {0};
char lastRingFrom[DEVICE_NAME_SIZE] = {0};
uint32_t lastRingMs = 0;
bool cameraUp = false;

String buildRoom(const String& target) {
  String me = String(device_name);
  me.trim();
  me.toLowerCase();
  String other = target;
  other.trim();
  other.toLowerCase();
  if (me.length() == 0 || other.length() == 0) return "";
  return (me < other) ? me + "+" + other : other + "+" + me;
}

void showMessage(const char* line1, const char* line2, uint16_t color) {
  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(color, TFT_BLACK);
  tft.setTextSize(2);
  tft.drawCentreString(line1, SCREEN_W / 2, SCREEN_H / 2 - 20, 1);
  if (line2 != nullptr) tft.drawCentreString(line2, SCREEN_W / 2, SCREEN_H / 2 + 8, 1);
}

// Mismo texto que showMessage() pero en una franja abajo, para que no tape la
// imagen de la cámara propia que quedó dibujada atrás.
void drawOverlay(const char* line1, const char* line2, uint16_t color) {
  tft.fillRect(0, SCREEN_H - OVERLAY_H, SCREEN_W, OVERLAY_H, TFT_BLACK);
  tft.setTextColor(color, TFT_BLACK);
  tft.setTextSize(2);
  tft.drawCentreString(line1, SCREEN_W / 2, SCREEN_H - OVERLAY_H + 5, 1);
  if (line2 != nullptr) tft.drawCentreString(line2, SCREEN_W / 2, SCREEN_H - OVERLAY_H + 24, 1);
}

// Dibuja lo que está viendo la cámara de este equipo, con el texto encima. Sirve
// para encuadrar antes de que el otro lado empiece a ver: mientras suena la
// entrante y mientras se espera que atiendan la saliente.
bool drawSelfPreview(uint8_t* rgb, const char* line1, const char* line2, uint16_t color) {
  camera_fb_t* frame = esp_camera_fb_get();
  if (frame == nullptr) return false;
  const bool ok = frame->len > 0 && jpg2rgb565(frame->buf, frame->len, rgb, JPG_SCALE_NONE);
  esp_camera_fb_return(frame);
  if (!ok) return false;
  tft.pushImage(0, 0, SCREEN_W, SCREEN_H, reinterpret_cast<uint16_t*>(rgb));
  drawOverlay(line1, line2, color);
  return true;
}

bool startCamera() {
  if (cameraUp) return true;
  camera_config_t config = cameraConfig();
  esp_err_t error = esp_camera_init(&config);
  if (error != ESP_OK) {
    Serial.printf("❌ Cámara: esp_camera_init falló (0x%x).\n", (unsigned)error);
    return false;
  }
  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor != nullptr) {
    Serial.printf("📷 Sensor PID 0x%x.\n", (unsigned)sensor->id.PID);
    // El módulo del Sense viene montado al revés respecto del display.
    sensor->set_vflip(sensor, 1);
    sensor->set_hmirror(sensor, 1);
  }
  cameraUp = true;
  return true;
}

void stopCamera() {
  if (!cameraUp) return;
  esp_camera_deinit();
  cameraUp = false;
}

// Decodifica un JPEG a RGB565 en un buffer ya reservado (SCREEN_W*SCREEN_H*2)
// con el conversor que trae esp32-camera. Los dos equipos capturan a 240x240,
// así que el frame entra justo.
bool decodeToScreen(const uint8_t* jpeg, size_t length, uint8_t* rgb565) {
  return jpg2rgb565(jpeg, length, rgb565, JPG_SCALE_NONE);
}

void writeFrame(WiFiClient& socket, const uint8_t* data, size_t length) {
  uint8_t header[4] = {
    static_cast<uint8_t>(length >> 24),
    static_cast<uint8_t>(length >> 16),
    static_cast<uint8_t>(length >> 8),
    static_cast<uint8_t>(length),
  };
  socket.write(header, 4);
  socket.write(data, length);
}

}  // namespace

void requestVideoCall(const String& persona) {
  String target = persona;
  target.trim();
  if (target.length() == 0) return;
  strlcpy(pendingTarget, target.c_str(), sizeof(pendingTarget));
  pendingCall = true;
  Serial.printf("📞 Videollamada pedida: %s\n", pendingTarget);
}

bool videoCallPending() {
  return pendingCall;
}

void requestIncomingCall(const String& from) {
  String who = from;
  who.trim();
  if (who.length() == 0) return;
  if (pendingCall || pendingIncoming) return;  // ya hay algo en curso
  if (who.equalsIgnoreCase(lastRingFrom) && millis() - lastRingMs < RING_COOLDOWN_MS) return;
  strlcpy(pendingFrom, who.c_str(), sizeof(pendingFrom));
  pendingIncoming = true;
  Serial.printf("📞 Llamada entrante de: %s\n", pendingFrom);
}

bool incomingCallPending() {
  return pendingIncoming;
}

void runIncomingCall() {
  pendingIncoming = false;
  const String from = String(pendingFrom);
  pendingFrom[0] = 0;
  strlcpy(lastRingFrom, from.c_str(), sizeof(lastRingFrom));
  lastRingMs = millis();

  if (from.length() == 0 || String(device_name).length() == 0) return;

  // Que termine cualquier respuesta de voz que esté sonando.
  const uint32_t waitStart = millis();
  while (mp3 != nullptr && mp3->isRunning() && millis() - waitStart < 6000) delay(50);

  pauseWakeWord();
  pauseFaceAnimation();
  showMessage(from.c_str(), "te esta llamando", TFT_CYAN);
  setNotificationLed(255, 0, 0);

  // La cámara se prende ya, antes de atender: así se ve el propio encuadre
  // mientras suena y se puede acomodar el equipo antes de que el otro lado
  // empiece a ver. Si atiende, queda prendida y runVideoCall() la reusa.
  uint8_t* rgb = nullptr;
  if (startCamera()) {
    rgb = static_cast<uint8_t*>(ps_malloc(static_cast<size_t>(SCREEN_W) * SCREEN_H * 2));
    if (rgb != nullptr) tft.setSwapBytes(true);
  }

  bool answered = false;
  uint32_t lastPreview = 0;
  const uint32_t ringStart = millis();
  while (millis() - ringStart < RING_MS) {
    if (digitalRead(TOUCH_PIN) == HIGH) {
      delay(40);
      if (digitalRead(TOUCH_PIN) == HIGH) {
        while (digitalRead(TOUCH_PIN) == HIGH) delay(10);
        answered = true;
        break;
      }
    }
    if (rgb != nullptr && millis() - lastPreview >= PREVIEW_INTERVAL_MS) {
      lastPreview = millis();
      drawSelfPreview(rgb, from.c_str(), "te esta llamando", TFT_CYAN);
    }
    // Parpadeo lento para que se lea como "está sonando".
    setNotificationLed(((millis() / 450) % 2) ? 255 : 25, 0, 0);
    delay(20);
  }

  free(rgb);
  clearNotificationLed();
  resumeFaceAnimation();
  resumeWakeWord();

  if (answered) {
    Serial.println("📞 Atendida.");
    requestVideoCall(from);  // loop() la levanta en la vuelta siguiente
  } else {
    Serial.println("📞 Llamada perdida.");
    stopCamera();
    setFaceMode(FACE_IDLE);
  }
}

void runVideoCall() {
  pendingCall = false;
  const String target = String(pendingTarget);
  pendingTarget[0] = 0;
  const String room = buildRoom(target);

  // Que termine de sonar "llamando a ..." antes de tomar la pantalla.
  const uint32_t waitStart = millis();
  while (mp3 != nullptr && mp3->isRunning() && millis() - waitStart < 6000) delay(50);

  pauseWakeWord();
  pauseFaceAnimation();

  if (room.length() == 0) {
    showMessage("Falta el nombre", "de este equipo", TFT_RED);
    delay(2500);
    resumeFaceAnimation();
    resumeWakeWord();
    return;
  }

  if (!backendLock(8000)) {
    showMessage("Red ocupada", nullptr, TFT_RED);
    delay(2000);
    resumeFaceAnimation();
    resumeWakeWord();
    return;
  }

  WiFiClient socket;

  bool torn = false;
  auto teardown = [&]() {
    if (torn) return;
    torn = true;
    socket.stop();
    stopCamera();
    backendDisconnect();
    backendUnlock();
    resumeFaceAnimation();
    resumeWakeWord();
    setFaceMode(FACE_IDLE);
  };

  showMessage("Llamando a", target.c_str(), TFT_CYAN);

  if (!startCamera()) {
    showMessage("Sin camara", nullptr, TFT_RED);
    delay(2500);
    teardown();
    return;
  }

  const String host = backendConnectHost();
  if (tailnetEnabled()) tailnetEnsurePeer(host, CALL_RELAY_PORT);

  socket.setTimeout(5000);
  if (!socket.connect(host.c_str(), CALL_RELAY_PORT)) {
    Serial.printf("❌ No pude conectar al relay %s:%u\n", host.c_str(), CALL_RELAY_PORT);
    showMessage("Sin conexion", "al relay", TFT_RED);
    delay(2500);
    teardown();
    return;
  }
  socket.setNoDelay(true);
  socket.print(room);
  socket.print("\n");

  uint8_t* jpegRx = static_cast<uint8_t*>(ps_malloc(MAX_JPEG_BYTES));
  uint8_t* rgb = static_cast<uint8_t*>(ps_malloc(static_cast<size_t>(SCREEN_W) * SCREEN_H * 2));
  if (jpegRx == nullptr || rgb == nullptr) {
    free(jpegRx);
    free(rgb);
    showMessage("Sin memoria", nullptr, TFT_RED);
    delay(2000);
    teardown();
    return;
  }

  Serial.printf("📹 En sala \"%s\"\n", room.c_str());
  tft.setSwapBytes(true);

  const uint32_t callStart = millis();
  const uint32_t txInterval = tailnetEnabled() ? TX_INTERVAL_TAILNET_MS : TX_INTERVAL_MS;
  uint32_t lastTx = 0;
  uint32_t lastPreview = 0;
  uint32_t lastRxFrame = millis();
  bool gotFirstFrame = false;

  enum { WANT_LEN, WANT_DATA } rxState = WANT_LEN;
  uint32_t need = 0;
  uint32_t got = 0;

  for (;;) {
    // --- razones para colgar ---
    if (digitalRead(TOUCH_PIN) == HIGH) {
      delay(40);
      if (digitalRead(TOUCH_PIN) == HIGH) {
        Serial.println("📴 Llamada cortada con el sensor.");
        while (digitalRead(TOUCH_PIN) == HIGH) delay(10);
        break;
      }
    }
    if (!socket.connected() && socket.available() == 0) {
      Serial.println("📴 El otro colgó.");
      break;
    }
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("📴 Wi-Fi caído.");
      break;
    }
    if (millis() - callStart > MAX_CALL_MS) {
      Serial.println("📴 Tope de duración de la llamada.");
      break;
    }
    if (!gotFirstFrame && millis() - callStart > WAIT_PEER_MS) {
      Serial.println("📴 Nadie atendió.");
      break;
    }
    if (gotFirstFrame && millis() - lastRxFrame > RX_STALL_MS) {
      Serial.println("📴 Se cortó el video.");
      break;
    }

    // --- recibir y dibujar ---
    if (rxState == WANT_LEN && socket.available() >= 4) {
      uint8_t header[4];
      socket.readBytes(header, 4);
      need = (static_cast<uint32_t>(header[0]) << 24) |
             (static_cast<uint32_t>(header[1]) << 16) |
             (static_cast<uint32_t>(header[2]) << 8) |
             static_cast<uint32_t>(header[3]);
      got = 0;
      if (need == 0 || need > MAX_JPEG_BYTES) {
        Serial.printf("⚠️ Frame de %lu bytes fuera de rango; corto.\n", (unsigned long)need);
        break;
      }
      rxState = WANT_DATA;
    }
    if (rxState == WANT_DATA) {
      while (got < need && socket.available() > 0) {
        const size_t chunk = min(static_cast<size_t>(socket.available()), static_cast<size_t>(need - got));
        const int n = socket.read(jpegRx + got, chunk);
        if (n <= 0) break;
        got += n;
      }
      if (got == need) {
        rxState = WANT_LEN;
        lastRxFrame = millis();
        gotFirstFrame = true;
        // Con cola atrás este cuadro ya es viejo: descartarlo sale gratis y
        // dibujar el siguiente deja la imagen al día en vez de ir arrastrando
        // el retardo. Sin esto, un enlace lento se ve "en diferido".
        if (socket.available() < RX_BACKLOG_DROP_BYTES && decodeToScreen(jpegRx, need, rgb)) {
          tft.pushImage(0, 0, SCREEN_W, SCREEN_H, reinterpret_cast<uint16_t*>(rgb));
        }
      }
    }

    // --- capturar y enviar ---
    // Si el otro lado va lento, TCP frena esta escritura y la cámara termina
    // mandando a su ritmo. El watchdog del núcleo 1 no está vigilado, así que un
    // frenón acá no reinicia; y si el socket muere, socket.connected() lo caza
    // en la vuelta siguiente.
    if (millis() - lastTx >= txInterval) {
      lastTx = millis();
      camera_fb_t* frame = esp_camera_fb_get();
      if (frame != nullptr) {
        if (frame->len > 0 && frame->len <= MAX_JPEG_BYTES && socket.connected()) {
          writeFrame(socket, frame->buf, frame->len);
        }
        // Mientras el otro no atendió, la pantalla muestra la cámara propia en
        // vez de un cartel pelado: se aprovecha el mismo cuadro que se acaba de
        // mandar, así que sólo cuesta la decodificación.
        if (!gotFirstFrame && frame->len > 0 && millis() - lastPreview >= PREVIEW_INTERVAL_MS) {
          lastPreview = millis();
          if (jpg2rgb565(frame->buf, frame->len, rgb, JPG_SCALE_NONE)) {
            tft.pushImage(0, 0, SCREEN_W, SCREEN_H, reinterpret_cast<uint16_t*>(rgb));
            drawOverlay("Llamando a", target.c_str(), TFT_CYAN);
          }
        }
        esp_camera_fb_return(frame);
      }
    }

    delay(2);
  }

  socket.stop();
  free(jpegRx);
  free(rgb);

  showMessage("Llamada", "terminada", TFT_WHITE);
  delay(1200);
  teardown();
}
