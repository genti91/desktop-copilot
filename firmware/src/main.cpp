#include <Arduino.h>
#include <LittleFS.h>
#include <WiFi.h>
#include <WiFiManager.h>
#include "audio.h"
#include "backend.h"
#include "commands.h"
#include "device_config.h"
#include "display.h"
#include "maintenance.h"
#include "ota.h"
#include "settings.h"
#include "tailnet.h"
#include "version.h"
#include "videocall.h"
#include "voice.h"
#include "wakeword.h"

// server_url y device_token viven en backend.cpp, la auth key en tailnet.cpp.
bool shouldSaveConfig = false;

// El primer arranque incluye registrar el dispositivo contra el control plane de
// Tailscale y bajar la lista de peers; los siguientes reusan las claves de NVS.
constexpr uint32_t TAILNET_STARTUP_TIMEOUT_MS = 45000;

// Si el sensor se leyo alto por ruido, el portal no puede dejar la placa colgada
// para siempre: al vencer reinicia y el arranque siguiente es normal.
constexpr uint32_t CONFIG_PORTAL_TIMEOUT_S = 300;

// Cuanto se espera un toque antes de seguir con el arranque normal.
constexpr uint32_t CONFIG_PORTAL_WINDOW_MS = 3000;
TaskHandle_t faceTaskHandle = NULL;

void saveConfigCallback() {
  shouldSaveConfig = true;
}

// Un toque en esta ventana abre el portal aunque el Wi-Fi conecte bien. Sin esto
// la unica forma de entrar era que fallara la conexion, y un dispositivo ya
// configurado se quedaba sin manera de cambiar la URL, el token o la auth key.
//
// La ventana va DESPUES del arranque y no durante: el sensor capacitivo se
// autocalibra al energizarse, asi que un dedo apoyado mientras bootea pasa a ser
// su linea de base y la lectura queda invertida —justo al reves de lo que hace
// falta—. Hay que arrancar sin tocar y tocar cuando lo pide.
bool configPortalRequested() {
  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_CYAN, TFT_BLACK);
  tft.setTextSize(2);
  tft.drawCentreString("Toca para", SCREEN_W / 2, SCREEN_H / 2 - 30, 1);
  tft.drawCentreString("configurar", SCREEN_W / 2, SCREEN_H / 2 + 2, 1);
  Serial.printf("🔧 Toca el sensor en los proximos %lu ms para abrir el portal.\n",
                (unsigned long)CONFIG_PORTAL_WINDOW_MS);

  const uint32_t startedMs = millis();
  while (millis() - startedMs < CONFIG_PORTAL_WINDOW_MS) {
    if (digitalRead(TOUCH_PIN) == HIGH) {
      delay(50);  // el mismo antirrebote que usa loop()
      if (digitalRead(TOUCH_PIN) == HIGH) return true;
    }
    delay(10);
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\n🚀 Firmware %s (build %d)\n", FIRMWARE_VERSION, FIRMWARE_BUILD);

  initBackend();
  initDeviceOutputs();

  tft.init();

  setDisplayPower(true);
  tft.setRotation(3);
  tft.fillScreen(TFT_RED);
  delay(200);
  tft.fillScreen(TFT_BLACK);
  delay(100);

  faceCanvas.setColorDepth(16);
  faceCanvas.createSprite(SCREEN_W, SCREEN_H);
  setFaceMode(FACE_IDLE);
  pinMode(TOUCH_PIN, INPUT);

  if (!LittleFS.begin(true)) {
    Serial.println("❌ Error al iniciar LittleFS");
  }

  loadBackendConfig();

  // Antes de tocar la red: es el gesto que decide si esto es un arranque normal
  // o una sesion de configuracion.
  const bool portalRequested = configPortalRequested();

  WiFiManager wifiManager;
  wifiManager.setSaveConfigCallback(saveConfigCallback);
  wifiManager.setAPCallback(configModeCallback);

  WiFiManagerParameter customServerUrl("server", "URL del backend", server_url, SERVER_URL_SIZE);
  // Hace falta sólo cuando el ESP32 sale de la LAN y el backend queda expuesto.
  WiFiManagerParameter customDeviceToken("token", "Token del dispositivo (opcional)", device_token, DEVICE_TOKEN_SIZE);
  // Con esto el ESP32 entra al tailnet como un nodo mas y le habla al backend
  // directo por su IP 100.x. Se usa una sola vez: despues las claves quedan en
  // NVS y el dispositivo se reconecta solo.
  WiFiManagerParameter customAuthKey("authkey", "Auth key de Tailscale (opcional)", tailnet_auth_key, TAILNET_AUTH_KEY_SIZE);
  // Nombre de este equipo: se usa para armar la sala de la videollamada.
  WiFiManagerParameter customDeviceName("name", "Nombre de este equipo (videollamadas)", device_name, DEVICE_NAME_SIZE);
  wifiManager.addParameter(&customServerUrl);
  wifiManager.addParameter(&customDeviceToken);
  wifiManager.addParameter(&customAuthKey);
  wifiManager.addParameter(&customDeviceName);

  if (portalRequested) {
    Serial.println("🔧 Sensor tocado: abro el portal de configuracion.");
    wifiManager.setConfigPortalTimeout(CONFIG_PORTAL_TIMEOUT_S);
  }

  Serial.println("📡 Conectando a Wi-Fi o abriendo portal cautivo...");
  const bool connected = portalRequested ? wifiManager.startConfigPortal("ESP32_Asistente")
                                         : wifiManager.autoConnect("ESP32_Asistente");
  if (!connected) {
    Serial.println("❌ Falló la conexión WiFi o timeout. Reiniciando...");
    delay(3000);
    ESP.restart();
  }

  Serial.println("\n🌐 Wi-Fi conectado con éxito.");
  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_GREEN, TFT_BLACK);
  tft.setTextSize(2);
  tft.drawCentreString("Conectado!", SCREEN_W / 2, SCREEN_H / 2 - 10, 1);
  delay(1500);

  if (shouldSaveConfig) {
    strlcpy(server_url, customServerUrl.getValue(), sizeof(server_url));
    strlcpy(device_token, customDeviceToken.getValue(), sizeof(device_token));
    strlcpy(tailnet_auth_key, customAuthKey.getValue(), sizeof(tailnet_auth_key));
    strlcpy(device_name, customDeviceName.getValue(), sizeof(device_name));
    saveBackendConfig();
  }

  // El tailnet va antes que el OTA porque el backend puede estar solamente ahí
  // adentro. Si no levanta, los pedidos salen igual contra la URL configurada.
  if (tailnetEnabled()) {
    tft.fillScreen(TFT_BLACK);
    tft.setTextColor(TFT_CYAN, TFT_BLACK);
    tft.setTextSize(2);
    tft.drawCentreString("Tailnet...", SCREEN_W / 2, SCREEN_H / 2 - 10, 1);

    if (startTailnet(TAILNET_STARTUP_TIMEOUT_MS)) {
      tft.fillScreen(TFT_BLACK);
      tft.setTextColor(TFT_GREEN, TFT_BLACK);
      tft.drawCentreString(tailnetVpnIp(), SCREEN_W / 2, SCREEN_H / 2 - 10, 1);
      delay(1200);
    }
  }

  // Antes de arrancar nada más: si el backend publicó un build mayor, el ESP32
  // se actualiza y reinicia desde acá.
  checkForFirmwareUpdate();

  initAudio();
  startAudioTask();
  initDeviceSettings();

  xTaskCreatePinnedToCore(
    faceAnimationTask,
    "faceTask",
    4096,
    NULL,
    1,
    &faceTaskHandle,
    FACE_TASK_CORE
  );

  // Con la tarea de la cara ya viva, traemos la configuración actual del backend
  // (colores, encendidos e imagen de reposo).
  refreshDeviceSettings(true);

  // La escucha continua NO arranca aca. Un intento anterior murio dentro del
  // arranque del AFE y se llevo puesto el USB con el: la placa quedo en ciclo de
  // reinicio, sin consola para leer y sin puerto para reflashear. Arrancarla
  // desde loop(), unos segundos despues, garantiza que el log del arranque ya
  // salio y que el puerto quedo establecido antes de tocar la parte riesgosa.
  // Ver WAKEWORD_ARRANQUE_MS en loop().

  // El sondeo y el OTA pasan a su propia tarea: en loop() bloqueaban la
  // lectura del sensor tactil y la reproduccion del audio.
  startMaintenanceTask();


  Serial.println("\n✅ SISTEMA LISTO. Mantén presionado el sensor Touch para hablar.");
}

// Cuanto espera loop() antes de levantar la escucha continua. Lo suficiente para
// que el arranque haya terminado de imprimirse y el USB este firme.
constexpr uint32_t WAKEWORD_ARRANQUE_MS = 8000;

void loop() {

  static bool wakeWordIntentada = false;
  if (!wakeWordIntentada && millis() > WAKEWORD_ARRANQUE_MS) {
    wakeWordIntentada = true;
    startWakeWord();
  }

  // La escucha continua deja la grabacion lista en pcm_buffer y avisa por aca.
  // El envio va en loop() y no en su tarea: bloquea varios segundos y comparte
  // el candado de red con el mantenimiento.
  const size_t porWakeWord = wakeWordCapturedBytes();
  if (porWakeWord > 0) {
    normalizeRecording(porWakeWord);
    // La ventana de seguimiento se abre solo si el equipo llego a contestar. Si
    // no hubo respuesta —el backend descarto la grabacion, o fallo la red—
    // abrirla igual es dejar el microfono servido para el proximo ruido, sin
    // que nadie haya empezado una conversacion.
    if (sendAudioAndPlayResponse(porWakeWord)) wakeWordResume();
    else wakeWordListenAgain();
  }

  // Llamada entrante detectada en el último sondeo de /device/config.
  if (incomingCallPending()) {
    runIncomingCall();
  }

  // Una respuesta pudo traer "CALL:<persona>": la llamada bloquea, así que va
  // acá y no en executeDeviceCommand(), que corre mientras se despacha el audio.
  // Atender una entrante también deja una llamada pendiente por acá.
  if (videoCallPending()) {
    runVideoCall();
  }

  if (digitalRead(TOUCH_PIN) == HIGH) {
    // El reloj arranca en el primer HIGH, antes del antirrebote: es el instante
    // en que el usuario tocó, que es contra el que hay que medir todo lo demás.
    interactionStartedMs = millis();
    delay(50);
    if (digitalRead(TOUCH_PIN) == HIGH) {
      wakeDeviceOutputs();
      recordWhileTouched();
      while (digitalRead(TOUCH_PIN) == HIGH) delay(10);
    }
  }
}
