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
constexpr BaseType_t MAINTENANCE_CORE = 0;

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
