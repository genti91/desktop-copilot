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

// Cuanto se espera a que la persona EMPIECE a hablar despues de decir "Jarvis".
// Es distinto del silencio que corta la frase: es normal tomarse un segundo para
// pensar que se va a pedir, y con el umbral de corte se cancelaba solo.
constexpr uint32_t ESPERA_A_QUE_HABLE_MS = 5000;

// La ventana de seguimiento —la que se abre sola al terminar de contestar, para
// poder repreguntar sin decir "Jarvis" otra vez— es mucho mas corta y mas
// exigente que la de arriba, y por un motivo: ahi hubo una palabra de activacion
// de por medio, o sea alguien que decidio hablarle. Aca no hay ninguna senal de
// que le esten hablando, asi que cada segundo que queda abierta es un segundo en
// el que cualquier ruido del ambiente se puede llevar el turno.
constexpr uint32_t ESPERA_SEGUIMIENTO_MS = 2000;
constexpr uint32_t MINIMO_DE_VOZ_SEGUIMIENTO_MS = 700;

// El VAD dice "esto tiene forma de voz", no "esto te lo estan diciendo a vos":
// una conversacion de fondo, la tele o un golpe seco lo activan igual. El filtro
// que los separa es el nivel: quien le habla al equipo esta cerca del microfono
// y destaca sobre el ambiente; lo de fondo se queda pegado al piso de ruido.
//
// Estos dos umbrales se aplican SOLO en la ventana de seguimiento, y no despues
// de "Jarvis". El costo de equivocarse no es el mismo en los dos lados: en el
// seguimiento, rechazar de mas cuesta volver a decir la palabra, mientras que
// aceptar de mas es el equipo hablando solo. Despues de "Jarvis" es al reves
// —alguien acaba de pedirle algo explicitamente— y ahi un umbral mal calibrado
// dejaria el camino principal sin contestar.
//
// 9 dB son unas 3 veces la amplitud.
constexpr float MARGEN_SOBRE_EL_RUIDO_DB = 9.0f;

// Y un tope absoluto, porque el margen solo no alcanza: en una habitacion muy
// silenciosa el piso se va a -80 dBFS y cualquier crujido queda 20 dB por
// encima. -60 dBFS es el mismo valor que usa el AFE de fabrica para decidir si
// una trama tiene energia de voz (AFE_VAD_ENERGY_THRESHOLD_DEFAULT).
constexpr float NIVEL_MINIMO_DE_VOZ_DBFS = -60.0f;

// Al retomar el microfono, los primeros trozos que devuelve el AFE son los que
// quedaron en su buffer de antes de la pausa: la cola de la propia respuesta.
// Sin cancelacion de eco, tomarlos por voz es contestarse a si mismo.
constexpr uint32_t DESCARTE_AL_RETOMAR_MS = 300;

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
uint32_t msPorDescartar = 0;

// Si esta ventana la abrio la respuesta anterior en vez de la palabra de
// activacion. Lo escribe loop() al despachar, lo lee la tarea de escucha.
volatile bool enSeguimiento = false;

// Nivel del ambiente y de lo que se esta grabando, los dos en dBFS. El AFE los
// entrega ya calculados en cada trozo (data_volume), asi que salen gratis.
float pisoDeRuidoDb = 0.0f;
bool pisoMedido = false;
float picoDeVozDb = -120.0f;

// El piso baja rapido y sube despacio a proposito: si alguien enciende un
// ventilador, que el umbral lo absorba en un segundo; si alguien habla cerca,
// que su voz no se coma el piso y termine haciendo pasar al ruido siguiente.
void actualizarPisoDeRuido(float nivelDb) {
  if (!pisoMedido) {
    pisoDeRuidoDb = nivelDb;
    pisoMedido = true;
    return;
  }
  pisoDeRuidoDb += (nivelDb - pisoDeRuidoDb) * (nivelDb < pisoDeRuidoDb ? 0.25f : 0.02f);
}

// Lo grabado no vale: se tira y se vuelve a esperar "Jarvis". Cerrar la ventana
// es la mitad del arreglo —dejarla abierta despues de descartar es ofrecerle el
// turno al proximo ruido, que es justo lo que estabamos evitando—.
void volverAEscuchar() {
  setFaceMode(FACE_IDLE);
  bytesGrabados = 0;
  enSeguimiento = false;
  estado = Estado::ESCUCHANDO;
}

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
      msPorDescartar = DESCARTE_AL_RETOMAR_MS;
      if (estado == Estado::GRABANDO) setFaceMode(FACE_RECORDING);
    }

    afe_fetch_result_t* resultado = afeHandle->fetch(afeData);
    if (resultado == NULL || resultado->ret_value == ESP_FAIL) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }

    const uint32_t msDelTrozo = resultado->data_size / BYTES_POR_MS;

    // Los primeros trozos despues de una pausa son la cola de la respuesta que
    // quedo en el buffer del AFE. No cuentan ni como voz ni como ambiente.
    if (msPorDescartar > 0) {
      msPorDescartar = msDelTrozo >= msPorDescartar ? 0 : msPorDescartar - msDelTrozo;
      continue;
    }

    const float nivelDb = resultado->data_volume;
    const bool hayVoz = resultado->vad_state == VAD_SPEECH;
    if (!hayVoz) actualizarPisoDeRuido(nivelDb);

    if (estado == Estado::ESCUCHANDO) {
      if (resultado->wakeup_state == WAKENET_DETECTED) {
        Serial.println("🗣️ \"Jarvis\" detectado, escuchando...");
        wakeDeviceOutputs();
        setFaceMode(FACE_RECORDING);
        bytesGrabados = 0;
        msGrabados = 0;
        msDeSilencio = 0;
        msDeVoz = 0;
        picoDeVozDb = -120.0f;
        enSeguimiento = false;
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

    if (hayVoz) {
      msDeVoz += msDelTrozo;
      msDeSilencio = 0;
      if (nivelDb > picoDeVozDb) picoDeVozDb = nivelDb;
    } else {
      msDeSilencio += msDelTrozo;
    }

    // La ventana de seguimiento pide mas voz y espera menos: nadie dijo la
    // palabra de activacion, asi que la duda se resuelve a favor de callarse.
    const uint32_t minimoDeVoz =
        enSeguimiento ? MINIMO_DE_VOZ_SEGUIMIENTO_MS : MINIMO_DE_VOZ_MS;
    const uint32_t esperaAQueHable =
        enSeguimiento ? ESPERA_SEGUIMIENTO_MS : ESPERA_A_QUE_HABLE_MS;

    const bool termino_de_hablar =
        msDeSilencio >= SILENCIO_PARA_CORTAR_MS && msDeVoz >= minimoDeVoz;
    const bool se_paso_de_largo = msGrabados >= MAXIMO_DE_GRABACION_MS;
    // Se mide contra el total transcurrido, no contra el silencio acumulado: lo
    // que interesa es cuanto lleva la ventana abierta sin que nadie hable.
    const bool no_dijo_nada = msGrabados >= esperaAQueHable && msDeVoz < minimoDeVoz;

    if (no_dijo_nada) {
      Serial.printf("🤷 %lu ms de ventana sin voz suficiente (%lu ms); vuelvo a escuchar.\n",
                    (unsigned long)esperaAQueHable, (unsigned long)msDeVoz);
      volverAEscuchar();
      continue;
    }

    if (termino_de_hablar || se_paso_de_largo) {
      // El VAD ya dijo que suena a voz; esto decide si esa voz venia dirigida al
      // equipo o si es lo que hay en la habitacion. Un pico que apenas asoma
      // sobre el ambiente es exactamente lo que se escuchaba como "le contesto a
      // cualquier ruido", y en el seguimiento conviene equivocarse callandose.
      const float margenDb = picoDeVozDb - pisoDeRuidoDb;
      const bool destacaSobreElAmbiente = !pisoMedido || margenDb >= MARGEN_SOBRE_EL_RUIDO_DB;
      const bool hablaronCerca = picoDeVozDb >= NIVEL_MINIMO_DE_VOZ_DBFS;

      if (enSeguimiento && (!destacaSobreElAmbiente || !hablaronCerca)) {
        Serial.printf("🔇 Seguimiento descartado por ruido: pico %.0f dBFS, piso %.0f, margen %.0f dB.\n",
                      picoDeVozDb, pisoDeRuidoDb, margenDb);
        volverAEscuchar();
        continue;
      }

      // Los niveles se imprimen siempre, tambien cuando pasa: son los numeros
      // con los que se calibran los dos umbrales de arriba sin tener que
      // adivinar como suena esta habitacion.
      Serial.printf("🛑 Fin de la frase (%lu ms, %s, pico %.0f dBFS, piso %.0f, margen %.0f dB).\n",
                    (unsigned long)msGrabados,
                    se_paso_de_largo ? "tope de tiempo" : "silencio",
                    picoDeVozDb, pisoDeRuidoDb, margenDb);
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
  // Despues de contestar no vuelve a esperar la palabra de activacion: abre una
  // ventana de seguimiento para poder responderle de una. Dura
  // ESPERA_SEGUIMIENTO_MS y pide mas voz que la de despues de "Jarvis"; si no se
  // dice nada, la logica de "no dijo nada" la devuelve sola a escuchar.
  //
  // Las tareas siguen suspendidas mientras suena la respuesta, asi que la
  // ventana empieza a contar recien cuando el audio termina, que es cuando la
  // persona puede hablar.
  bytesGrabados = 0;
  msGrabados = 0;
  msDeSilencio = 0;
  msDeVoz = 0;
  picoDeVozDb = -120.0f;
  enSeguimiento = true;
  estado = Estado::GRABANDO;
}

void wakeWordListenAgain() {
  if (!tareasVivas) return;
  bytesGrabados = 0;
  enSeguimiento = false;
  estado = Estado::ESCUCHANDO;
}

void pauseWakeWord() {
  if (!tareasVivas) return;
  estado = Estado::PRESTADO;
  // Las dos tareas miran el estado cada 20 ms; con esto ya soltaron el micrófono.
  vTaskDelay(pdMS_TO_TICKS(40));
}

void resumeWakeWord() {
  wakeWordListenAgain();
}
