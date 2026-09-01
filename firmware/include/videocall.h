#pragma once

#include <Arduino.h>

// Videollamada ESP↔ESP sin audio: la cámara de este equipo viaja al display del
// otro y viceversa, por el relay TCP del backend (app/call.py).
//
// El disparo llega como comando "CALL:<persona>" en la cabecera X-Action de una
// respuesta de voz. commands.cpp lo pasa a requestVideoCall(); loop() lo levanta
// con runVideoCall() una vez que terminó de sonar el "llamando a ...".
//
// La sala se arma como sorted(nombre_propio, persona) unida por "+": los dos
// equipos tienen que iniciar la llamada para caer en la misma sala. El nombre
// propio sale del campo "Nombre de este equipo" del portal (device_name).

// Anota a quién llamar. No arranca la llamada todavía.
void requestVideoCall(const String& persona);

// true si hay una llamada pedida esperando a que loop() la levante.
bool videoCallPending();

// Entra en modo llamada y no vuelve hasta que se corta (toque en el sensor, el
// otro cuelga, nadie atiende, o se cae el Wi-Fi). Bloquea: corre en loop().
void runVideoCall();

// Llamada entrante: el backend avisa por /device/config que "from" está
// llamando. settings.cpp lo pasa acá; loop() lo levanta con runIncomingCall().
void requestIncomingCall(const String& from);
bool incomingCallPending();

// Muestra "<from> te está llamando", pone el LED rojo y espera un toque para
// atender (hasta ~35 s). Si se atiende, encadena la videollamada. Bloquea.
void runIncomingCall();
