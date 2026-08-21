#pragma once

// El backend decide si hay que actualizar comparando FIRMWARE_BUILD contra el
// campo "build" del manifest OTA, así que este número debe subir en cada release.
#define FIRMWARE_VERSION "1.1.0"
#define FIRMWARE_BUILD 2
