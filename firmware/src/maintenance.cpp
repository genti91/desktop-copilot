#include <Arduino.h>
#include "audio.h"
#include "backend.h"
#include "device_config.h"
#include "maintenance.h"
#include "ota.h"
#include "settings.h"

namespace {

constexpr uint32_t MAINTENANCE_TICK_MS = 500;
// El handshake TLS de mbedtls necesita bastante pila, y por acá tambien pasa el
// OTA, que escribe flash mientras calcula el SHA-256.
constexpr uint32_t MAINTENANCE_STACK_BYTES = 16384;

// Va en el mismo núcleo que loop(), no en el de la animación.
//
// El núcleo 0 ya tiene faceTask redibujando 240x240 cada 15 ms, y sólo el IDLE
// del núcleo 0 está vigilado por el task watchdog (5 s, con panic). Un handshake
// TLS es CPU pura y no cede: puesto ahí, entre las dos tareas dejaban al IDLE0
// sin correr y el watchdog reiniciaba la placa.
//
// En el núcleo 1 comparte tiempo con loopTask, las dos en prioridad 1, así que
// FreeRTOS las alterna por tick y el sensor táctil se sigue leyendo.
#ifdef ARDUINO_RUNNING_CORE
constexpr BaseType_t MAINTENANCE_CORE = ARDUINO_RUNNING_CORE;
#else
constexpr BaseType_t MAINTENANCE_CORE = 1;
#endif

bool interactionInProgress() {
  // Mientras el usuario habla o el asistente contesta, la red queda para eso.
  return digitalRead(TOUCH_PIN) == HIGH || (mp3 != NULL && mp3->isRunning());
}

void maintenanceTask(void* parameter) {
  (void)parameter;
  for (;;) {
    // backendLock(0) no espera: si la interacción esta usando la red, este
    // ciclo se saltea y vuelve a intentar en el próximo tick.
    if (!interactionInProgress() && backendLock(0)) {
      updateDeviceSettings();
      updateFirmwareCheck();
      backendUnlock();
    }
    vTaskDelay(pdMS_TO_TICKS(MAINTENANCE_TICK_MS));
  }
}

}  // namespace

void startMaintenanceTask() {
  xTaskCreatePinnedToCore(
    maintenanceTask,
    "maintenance",
    MAINTENANCE_STACK_BYTES,
    NULL,
    1,
    NULL,
    MAINTENANCE_CORE
  );
}
