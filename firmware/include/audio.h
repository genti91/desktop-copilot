#pragma once

#include <Arduino.h>
#include "AudioGeneratorMP3.h"
#include "AudioOutputI2S.h"
#include "AudioFileSourcePROGMEM.h"

extern uint8_t* pcm_buffer;
extern AudioGeneratorMP3* mp3;
// La respuesta se reproduce desde PSRAM, no desde LittleFS. Escribirla a flash
// congelaba la animacion de la cara: cada escritura deshabilita la cache de
// flash en LOS DOS nucleos, y el codigo de la cara vive en flash.
extern AudioFileSource* file;
extern AudioOutputI2S* out;
extern uint32_t playbackStartedMs;

void initAudio();
// Momento en que loop() confirmo el toque. Sirve para medir cuanto tarda
// cada etapa de la interaccion desde el punto de vista del usuario.
extern uint32_t interactionStartedMs;
// Lectura cruda del microfono. La usa la escucha de la wake word, que lo tiene
// ocupado de forma continua mientras nadie este grabando a mano.
bool micRead(void* destino, size_t bytes, size_t* leidos, uint32_t timeoutMs);

// Quita el continuo y lleva la grabacion al nivel que espera el reconocedor.
// Vale para las dos grabaciones: la del sensor y la de la wake word.
void normalizeRecording(size_t totalBytes);

void recordWhileTouched();
void updateAudioPlayback();

// La reproduccion vive en su propia tarea, no en loop(). Medido: WireGuard
// corre en prioridad 7 sobre el mismo nucleo y expropiaba a loop(), que iba en
// prioridad 1, dejando huecos de hasta 787 ms contra un colchon de salida de
// 256 ms. Eso es lo que se escucha como un trabon o una palabra repetida.
void startAudioTask();

// El decodificador lo tocan dos hilos: la tarea de audio que lo alimenta y
// loop(), que lo arranca y lo para al despachar una respuesta.
bool audioLock(uint32_t timeoutMs);
void audioUnlock();
