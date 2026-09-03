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

// Abre la ventana de seguimiento después de que loop() despachó la grabación y
// el equipo contestó: se puede repreguntar sin volver a decir "Jarvis".
void wakeWordResume();

// Cierra la ventana de seguimiento y vuelve a esperar la palabra de activación.
// La usa loop() cuando el pedido NO terminó en una respuesta hablada: si no
// hubo nada que contestar, dejar el micrófono abierto es lo que hace que el
// próximo ruido de la habitación se lleve el turno.
void wakeWordListenAgain();

// Suelta y retoma el micrófono para que lo use la grabación por sensor táctil.
void pauseWakeWord();
void resumeWakeWord();
