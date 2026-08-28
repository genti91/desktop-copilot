#include <Arduino.h>

#include "esp_afe_config.h"
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "model_path.h"

#include "audio.h"
#include "commands.h"
#include "device_config.h"
#include "display.h"
#include "wakeword.h"

namespace {

// Un solo micrófono, sin canal de referencia: no hay cancelación de eco, así que
// mientras el asistente habla la escucha se apaga para no oírse a sí mismo.
constexpr const char* FORMATO_ENTRADA = "M";

// Nombre de la partición de partitions.csv donde vive srmodels.bin.
constexpr const char* PARTICION_MODELOS = "model";

// Cuánto silencio hace falta para dar por terminada la frase. Corto de más
// parte las pausas naturales al pensar; largo de más deja al usuario esperando
// después de haber terminado de hablar.
constexpr uint32_t SILENCIO_PARA_CORTAR_MS = 900;

// Techo duro. PCM_BUFFER_LEN son 10 s, y quedarse sin buffer a mitad de frase
// deja una grabación cortada sin que nadie se entere.
constexpr uint32_t MAXIMO_DE_GRABACION_MS = 8000;

// Por debajo de esto no se manda nada: es un disparo en falso o un ruido corto,
// y despertar al backend para eso son diez segundos de espera al pedo.
constexpr uint32_t MINIMO_DE_VOZ_MS = 400;

// Cuanto se espera a que la persona EMPIECE a hablar. Es distinto del silencio
// que corta la frase: despues de decir "Jarvis" es normal tomarse un segundo
// para pensar que se va a pedir, y con el umbral de corte se cancelaba solo.
// Tambien es la ventana de seguimiento: al terminar la respuesta se queda
// escuchando este rato para poder contestarle sin repetir la palabra.
constexpr uint32_t ESPERA_A_QUE_HABLE_MS = 5000;

constexpr size_t BYTES_POR_MS = (SAMPLE_RATE * 2) / 1000;

enum class Estado {
  ESCUCHANDO,  // esperando la palabra de activación
  GRABANDO,    // ya se activó, juntando lo que dice el usuario
  LISTA,       // hay una grabación esperando a que loop() la despache
  PRESTADO,    // el micrófono lo tiene la grabación por sensor táctil
};

const esp_afe_sr_iface_t* afeHandle = NULL;
esp_afe_sr_data_t* afeData = NULL;
volatile Estado estado = Estado::ESCUCHANDO;
volatile size_t bytesGrabados = 0;
volatile bool tareasVivas = false;

// El AFE entrega audio ya filtrado en trozos de tamaño fijo. Estos contadores
// van en milisegundos para que los umbrales de arriba se lean como lo que son.
uint32_t msGrabados = 0;
uint32_t msDeSilencio = 0;
uint32_t msDeVoz = 0;

bool escuchaSuspendida() {
  // Mientras suena la respuesta el micrófono capta el parlante. Sin cancelación
  // de eco, dejarlo escuchando es pedirle que se active con su propia voz.
  return estado == Estado::PRESTADO || estado == Estado::LISTA ||
         (mp3 != NULL && mp3->isRunning());
}

void alimentarTask(void* parametro) {
  (void)parametro;
  const int muestrasPorTrozo = afeHandle->get_feed_chunksize(afeData);
  const size_t bytesPorTrozo = static_cast<size_t>(muestrasPorTrozo) * sizeof(int16_t);

  int16_t* trozo = static_cast<int16_t*>(heap_caps_malloc(bytesPorTrozo, MALLOC_CAP_DEFAULT));
  if (trozo == NULL) {
    Serial.println("❌ Wake word: sin memoria para el buffer de entrada.");
    vTaskDelete(NULL);
    return;
  }

  for (;;) {
    if (escuchaSuspendida()) {
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    size_t leidos = 0;
    if (!micRead(trozo, bytesPorTrozo, &leidos, 200) || leidos < bytesPorTrozo) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    afeHandle->feed(afeData, trozo);
  }
}

void escucharTask(void* parametro) {
  (void)parametro;
  bool veniaSuspendida = false;

  for (;;) {
    if (escuchaSuspendida()) {
      veniaSuspendida = true;
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    // La cara de grabar se pone al volver de la suspension, no antes. La ventana
    // de seguimiento se abre mientras todavia suena la respuesta, y ponerla ahi
    // pisaba la cara de hablar durante toda la reproduccion.
    if (veniaSuspendida) {
      veniaSuspendida = false;
      if (estado == Estado::GRABANDO) setFaceMode(FACE_RECORDING);
    }

    afe_fetch_result_t* resultado = afeHandle->fetch(afeData);
    if (resultado == NULL || resultado->ret_value == ESP_FAIL) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }

    const uint32_t msDelTrozo = resultado->data_size / BYTES_POR_MS;

    if (estado == Estado::ESCUCHANDO) {
      if (resultado->wakeup_state == WAKENET_DETECTED) {
        Serial.println("🗣️ \"Jarvis\" detectado, escuchando...");
        wakeDeviceOutputs();
        setFaceMode(FACE_RECORDING);
        bytesGrabados = 0;
        msGrabados = 0;
        msDeSilencio = 0;
        msDeVoz = 0;
        estado = Estado::GRABANDO;
      }
      continue;
    }

    if (estado != Estado::GRABANDO) continue;

    // La palabra de activación queda afuera de lo que se manda: Whisper no
    // necesita transcribir "Jarvis" y el modelo no necesita leerlo.
    if (pcm_buffer != NULL && bytesGrabados + resultado->data_size <= PCM_BUFFER_LEN) {
      memcpy(pcm_buffer + bytesGrabados, resultado->data, resultado->data_size);
      bytesGrabados += resultado->data_size;
    }
    msGrabados += msDelTrozo;

    if (resultado->vad_state == VAD_SPEECH) {
      msDeVoz += msDelTrozo;
      msDeSilencio = 0;
    } else {
      msDeSilencio += msDelTrozo;
    }

    const bool termino_de_hablar =
        msDeSilencio >= SILENCIO_PARA_CORTAR_MS && msDeVoz >= MINIMO_DE_VOZ_MS;
    const bool se_paso_de_largo = msGrabados >= MAXIMO_DE_GRABACION_MS;
    // Se mide contra el total transcurrido, no contra el silencio acumulado: lo
    // que interesa es cuanto lleva la ventana abierta sin que nadie hable.
    const bool no_dijo_nada =
        msGrabados >= ESPERA_A_QUE_HABLE_MS && msDeVoz < MINIMO_DE_VOZ_MS;

    if (no_dijo_nada) {
      Serial.println("🤷 Se activó pero no se dijo nada; vuelvo a escuchar.");
      setFaceMode(FACE_IDLE);
      bytesGrabados = 0;
      estado = Estado::ESCUCHANDO;
      continue;
    }

    if (termino_de_hablar || se_paso_de_largo) {
      Serial.printf("🛑 Fin de la frase (%lu ms, %s).\n", (unsigned long)msGrabados,
                    se_paso_de_largo ? "tope de tiempo" : "silencio");
      estado = Estado::LISTA;
    }
  }
}

}  // namespace

// Cada paso deja rastro antes de ejecutarse, con la memoria interna libre del
// momento. Si el arranque se muere aca adentro, el USB se cae con el y no hay
// consola que leer despues: lo unico que queda es lo que se alcanzo a escribir.
static void paso(const char* que) {
  Serial.printf("   [ww] %s (interna libre: %u, mayor bloque: %u)\n", que,
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
  Serial.flush();
}

bool startWakeWord() {
  paso("arrancando");
  srmodel_list_t* modelos = esp_srmodel_init(PARTICION_MODELOS);
  if (modelos == NULL || modelos->num == 0) {
    Serial.println("❌ Wake word: la partición \"model\" no tiene modelos.");
    return false;
  }
  Serial.printf("🧠 Wake word: %d modelo(s) en flash.\n", modelos->num);

  paso("modelos leidos, armando configuracion");
  afe_config_t* configuracion =
      afe_config_init(FORMATO_ENTRADA, modelos, AFE_TYPE_SR, AFE_MODE_LOW_COST);
  if (configuracion == NULL) {
    Serial.println("❌ Wake word: no pude armar la configuración del AFE.");
    return false;
  }

  paso("configuracion lista, creando el AFE");
  afeHandle = esp_afe_handle_from_config(configuracion);
  afeData = afeHandle != NULL ? afeHandle->create_from_config(configuracion) : NULL;
  if (afeData == NULL) {
    Serial.println("❌ Wake word: no pude crear el AFE.");
    return false;
  }

  // Las dos en el núcleo 1, con loopTask: el 0 ya tiene la animación de la cara,
  // el Wi-Fi y MicroLink, y su tarea IDLE es la única vigilada por el watchdog.
  //
  // Y en la MISMA prioridad que loopTask, no por encima. En prioridad 5 estas dos
  // expropiaban a loopTask, que es quien alimenta al decodificador de audio: el
  // buffer de salida se vaciaba y la respuesta se escuchaba entrecortada. Igualadas,
  // FreeRTOS las alterna por tick en vez de dejar a una comiéndose el turno de la
  // otra. Que la detección llegue unos milisegundos más tarde no se nota; que la
  // respuesta se trabe, sí.
  paso("AFE creado, levantando las tareas");
  xTaskCreatePinnedToCore(alimentarTask, "wwFeed", 8192, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(escucharTask, "wwFetch", 12288, NULL, 1, NULL, 1);
  tareasVivas = true;
  Serial.println("👂 Escuchando \"Jarvis\".");
  return true;
}

bool wakeWordReady() {
  return tareasVivas;
}

size_t wakeWordCapturedBytes() {
  return estado == Estado::LISTA ? bytesGrabados : 0;
}

void wakeWordResume() {
  // Despues de contestar no vuelve a esperar la palabra: abre una ventana de
  // seguimiento para poder responderle de una. Si en ESPERA_A_QUE_HABLE_MS no
  // se dice nada, la logica de "no dijo nada" la devuelve sola a escuchar.
  //
  // Las tareas siguen suspendidas mientras suena la respuesta, asi que la
  // ventana empieza a contar recien cuando el audio termina, que es cuando la
  // persona puede hablar.
  bytesGrabados = 0;
  msGrabados = 0;
  msDeSilencio = 0;
  msDeVoz = 0;
  estado = Estado::GRABANDO;
}

void pauseWakeWord() {
  if (!tareasVivas) return;
  estado = Estado::PRESTADO;
  // Las dos tareas miran el estado cada 20 ms; con esto ya soltaron el micrófono.
  vTaskDelay(pdMS_TO_TICKS(40));
}

void resumeWakeWord() {
  if (!tareasVivas) return;
  bytesGrabados = 0;
  estado = Estado::ESCUCHANDO;
}
