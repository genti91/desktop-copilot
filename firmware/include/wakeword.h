#pragma once

#include <Arduino.h>

// Escucha continua de la palabra de activación ("Jarvis") con esp-sr.
//
// El micrófono pasa a estar siempre ocupado por la tarea que alimenta al AFE, así
// que el camino del sensor táctil tiene que pedirlo prestado: pauseWakeWord()
// antes de grabar a mano y resumeWakeWord() al terminar.

// Levanta el modelo y las tareas. Devuelve false si la partición "model" está
// vacía o si no hay memoria: en ese caso el sensor táctil sigue funcionando.
bool startWakeWord();

// true si el modelo cargó y las tareas están corriendo.
bool wakeWordReady();

// Bytes de una grabación terminada esperando a que loop() la mande, o 0 si no
// hay ninguna. La grabación vive en pcm_buffer, igual que la del sensor.
size_t wakeWordCapturedBytes();

// Vuelve a escuchar después de que loop() despachó la grabación.
void wakeWordResume();

// Suelta y retoma el micrófono para que lo use la grabación por sensor táctil.
void pauseWakeWord();
void resumeWakeWord();
