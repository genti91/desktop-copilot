# Deploy en una Raspberry Pi con OctoPi

Guia para dejar el backend corriendo 24/7 en la misma Pi que ya usa OctoPrint, y
poder entrar desde cualquier lado sin port forwarding.

## Que queda armado

```text
tu celular / laptop (en cualquier lado)
  -> Tailscale (WireGuard, atraviesa CGNAT)
     -> Raspberry Pi
        |- OctoPrint  :80
        \- backend    :8000  (protegido con password)

ESP32 (en cualquier lado)
  -> Tailscale (MicroLink, cliente de tailnet corriendo en el ESP32)
     -> Raspberry Pi :8000     <- WireGuard punto a punto, sin pasar por relay
```

El ESP32 entra al tailnet como un nodo mas y le habla a la Pi directo por su IP
`100.x`. Si estan en la misma LAN, WireGuard descubre el camino local y el
trafico ni siquiera sale a internet. Ver "El ESP32 desde cualquier red".

## Antes de empezar

- Raspberry Pi 3 o superior. Funciona igual en imagenes de 32 bits (armhf): el
  backend no usa ChromaDB ni onnxruntime, que no tienen wheels para ARM de 32 bits.
- OctoPi ya funcionando y con acceso por SSH.
- El repo con las claves de IA que vas a usar (Gemini obligatoria).

## 1. Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Te da una URL para autenticarte. Despues instala Tailscale en tu celular y tu
laptop con la misma cuenta, y la Pi queda alcanzable desde cualquier red.

Dos opcionales que valen la pena:

```bash
# Que la Pi no pida re-autenticacion cada 6 meses
sudo tailscale up --ssh

# Alcanzar TODA tu red de casa desde afuera, no solo la Pi
sudo tailscale up --advertise-routes=192.168.1.0/24
```

Si activas rutas, hay que aprobarlas una vez desde el admin panel de Tailscale.

Con MagicDNS activado entras como `http://octopi:8000` desde cualquier
dispositivo del tailnet.

## 2. El backend

### Camino corto

```bash
curl -fsSL https://raw.githubusercontent.com/genti91/desktop-copilot/main/deploy/install-pi.sh | bash
```

Instala dependencias, clona el repo, arma el venv, genera una `PANEL_PASSWORD` si
no hay, deja el service de systemd andando y verifica `/health`. Es idempotente:
volves a correrlo para actualizar. Despues solo falta poner `GEMINI_API_KEY` en
`backend/.env` y reiniciar.

Si preferis ver que hace cada paso, el resto de esta seccion es lo mismo a mano.

### A mano

```bash
sudo apt update
sudo apt install -y git python3-venv python3-dev build-essential

git clone https://github.com/genti91/desktop-copilot.git ~/desktop-copilot
cd ~/desktop-copilot/backend

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

La instalacion tarda unos minutos en ARM. Las dependencias pesadas (numpy,
pillow) tienen wheels en piwheels, que Raspberry Pi OS ya trae configurado.

Configura el entorno:

```bash
cp .env.example .env
nano .env
```

Como minimo `GEMINI_API_KEY` y **`PANEL_PASSWORD`**. Para generar una:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

Sin `PANEL_PASSWORD` el backend queda abierto a cualquiera que lo alcance, y eso
incluye `POST /ota/firmware`, que acepta un binario que el ESP32 despues flashea.

## 3. systemd

```bash
sudo cp ~/desktop-copilot/deploy/desktop-copilot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now desktop-copilot
```

Verificar:

```bash
systemctl status desktop-copilot
journalctl -u desktop-copilot -f
curl -s localhost:8000/health
```

`/health` responde `{"status":"ok","auth":true}` si la password quedo bien puesta.

Si tu imagen de OctoPi usa otro usuario, cambia `User=`, `Group=` y las rutas del
service.

## 4. Apuntar el ESP32

1. Reservale IP fija a la Pi en tu router (DHCP reservation). Si la IP cambia, el
   dispositivo queda huerfano.
2. Manten presionado el boton de reset del ESP32 hasta que abra el portal
   `ESP32_Asistente`, o borra la red guardada.
3. En el portal, poner la URL del backend:
   `http://192.168.1.XX:8000/voice-assistant`.

El ESP32 alcanza `/device/config`, `/device/image`, `/ota/manifest` y
`/ota/download` sin password porque viene de una red confiable
(`TRUSTED_NETWORKS`, por defecto los rangos privados). El panel y todo lo que
escribe siguen pidiendo sesion, incluso desde la LAN.

## 5. Actualizar el backend

```bash
cd ~/desktop-copilot
git pull
./backend/venv/bin/pip install -r backend/requirements.txt
sudo systemctl restart desktop-copilot
```

El firmware **no** se actualiza asi: eso lo maneja GitHub Actions + el sync
automatico. Ver la seccion de OTA en el README principal.

## Cosas para tener en cuenta

**Los puertos 80 y 443 estan ocupados.** OctoPi corre haproxy adelante de
OctoPrint. Por eso el backend usa el 8000; no intentes moverlo al 80.

**Memoria.** El backend arranca en ~120 MB. Si ves el OOM killer en `dmesg`
mientras imprimis, agrega `MemoryMax=400M` al service.

**32 bits.** OctoPi usa una imagen armhf. Por eso la memoria consultable corre
sobre SQLite y no sobre ChromaDB: este ultimo depende de `onnxruntime`, que nunca
publico wheels para armv7l.

**CPU durante impresiones.** El service ya viene con `Nice=5` para que OctoPrint
tenga prioridad y no se arruine un print por un pico del backend.

**Python.** El codigo de `app/` es compatible con Python 3.9, asi que funciona en
imagenes viejas de OctoPi (Bullseye). Los tests usan sintaxis de 3.10+, asi que
si queres correrlos en la Pi necesitas Bookworm o mas nuevo.

**El sync de firmware es saliente.** La Pi consulta la API de GitHub cada 5
minutos; no necesita nada abierto hacia adentro, funciona detras de CGNAT.

## Opcional: acceso publico

Tailscale cubre tus dispositivos, incluido el ESP32. Si necesitas un link que
abra en cualquier navegador sin instalar nada:

```bash
sudo tailscale funnel 8000
```

Te da una URL `https://<maquina>.<tailnet>.ts.net` publica. Funciona solo para
HTTP/HTTPS, no para puertos arbitrarios. Sirve para entrar al panel desde un
navegador prestado; el ESP32 ya no la usa, porque entra al tailnet por su cuenta
y el firmware no habla TLS.

Con eso el backend queda expuesto a internet, asi que `PANEL_PASSWORD` deja de
ser opcional. La alternativa mas robusta es Cloudflare Tunnel con Cloudflare
Access adelante (login con Google/GitHub), usando un dominio propio; el GitHub
Student Pack regala un `.me` en Namecheap por un ano.

Si terminas un tunel en la Pi, ojo con `TRUSTED_NETWORKS`: loopback esta fuera de
la lista a proposito, para que el trafico del tunel no saltee el login. Funnel
proxea a `localhost`, asi que todo lo que entra por el tunel llega como
`127.0.0.1` y necesita credenciales si o si.

## El ESP32 desde cualquier red

El ESP32 corre su propio cliente de Tailscale: [MicroLink](https://github.com/CamM2325/microlink),
un componente de ESP-IDF que implementa el protocolo ts2021 completo (registro
contra el control plane, WireGuard, DISCO y STUN para atravesar NAT, y DERP
como relay de ultimo recurso).

La diferencia con exponer el backend por Funnel es el camino que hace el
trafico. Con Funnel el audio sale de tu casa hacia los servidores de Tailscale
y vuelve a entrar; con el tailnet, DISCO abre un agujero UDP entre el ESP32 y
la Pi y los paquetes van directo. Si ademas estan en la misma red, el camino
que gana es el local. De paso el backend deja de estar publicado en internet.

### 1. Generar una auth key

En [login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys),
"Generate auth key". Conviene marcarla como reusable si vas a flashear varios
dispositivos.

La key se usa una sola vez, para registrar el nodo: despues las claves quedan en
la NVS del ESP32 y el dispositivo se reconecta solo en cada arranque.

### 2. Poner el token del dispositivo

`TRUSTED_NETWORKS` no cubre el rango del tailnet, asi que el ESP32 llega como un
cliente cualquiera y necesita `DEVICE_TOKEN` en `backend/.env`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

Y reiniciar: `sudo systemctl restart desktop-copilot`.

### 3. Configurar el ESP32

Dale reset **sin tocar nada**. Apenas arranca, la pantalla pide "Toca para
configurar" y espera 3 segundos: tocá el sensor ahi y abre el portal
`ESP32_Asistente` aunque el Wi-Fi conecte bien. Conectate a esa red y carga:

- **URL del backend**: `http://octopi:8000/voice-assistant`
- **Token del dispositivo**: el que generaste arriba
- **Auth key de Tailscale**: la del paso 1

Los tres quedan en `/config.txt` de LittleFS, una por linea, y sobreviven a los
flasheos. Ese mismo gesto sirve despues para cambiar cualquiera de los tres: sin
el, el portal solo aparece cuando el Wi-Fi no conecta, y un dispositivo ya
configurado se queda sin forma de editarlos.

La ventana va despues del arranque y no durante a proposito. El sensor tactil se
autocalibra al energizarse: un dedo apoyado mientras bootea pasa a ser su linea
de base, y a partir de ahi la lectura queda invertida.

El portal se cierra solo a los 5 minutos y reinicia, para que una lectura falsa
del sensor no deje la placa colgada esperando.

`octopi` es el nombre del nodo en el tailnet, no un nombre DNS: el ESP32 no
consulta MagicDNS, lo resuelve contra la lista de peers que le llega del control
plane. Tambien podes poner la IP `100.x` directamente.

En la pantalla, despues de "Conectado!", aparece la IP `100.x` que le toco. Si
no aparece, el registro fallo y el firmware sigue hablando contra la URL tal
cual quedo configurada.

### Como lo resuelve el firmware

`tailnet.cpp` arranca MicroLink despues del Wi-Fi y antes del chequeo de OTA,
porque el backend puede estar solamente adentro del tailnet.

Todo pedido pasa por `beginBackendRequest()`, que hace dos cosas antes de abrir
el socket: resuelve el host contra la tabla de peers (una vez, cacheado) y se
asegura de que haya sesion de WireGuard viva contra la Pi. Ese segundo paso hace
falta porque lwIP rutea `100.64.0.0/10` por el netif del tunel: sin handshake
previo, `connect()` vuelve con `EHOSTUNREACH`. Se saltea solo si el tunel se
uso en los ultimos 60 segundos.

De ahi para arriba no cambia nada: el mismo `HTTPClient` sobre sockets BSD
comunes, con `X-Action` y `X-Device-Token`.

El firmware ya no habla TLS. Antes lo necesitaba para llegar a la URL publica de
Funnel; adentro del tailnet el cifrado lo pone WireGuard, asi que se fueron
`WiFiClientSecure`, el root CA embebido y el handshake de mbedtls, que era lo
mas caro de cada sondeo. Un `https://` cargado a mano en el portal se normaliza
a `http://`.

### El keepalive: por que hace falta

Hay un rodeo instalado en la Pi, `desktop-copilot-keepalive.service`, que hace un
`ping` al ESP32 cada 30 segundos.

El motivo es una limitacion de MicroLink: **el ESP32 no logra iniciar el handshake
de WireGuard contra `tailscaled`**. Sus initiations se descartan en silencio, tanto
por el camino directo como por DERP, aunque DISCO funcione en las dos direcciones
por esa misma IP y puerto, y aunque la clave publica del peer sea la correcta. Al
reves anda perfecto: cuando la Pi inicia, el tunel levanta y el ESP32 conecta al
backend en ~16 ms.

Como WireGuard descarta una sesion recien a los ~180 segundos de inactividad, un
ping cada 30 alcanza para que nunca caduque y el dispositivo siempre encuentre el
tunel armado.

Se instala solo con `install-pi.sh`. A mano:

```bash
sudo cp deploy/desktop-copilot-keepalive.service /etc/systemd/system/
sudo systemctl enable --now desktop-copilot-keepalive
```

Cuando se arregle upstream esto se saca entero:

```bash
sudo systemctl disable --now desktop-copilot-keepalive
```

### Si algo falla

Volves a entrar al portal cautivo (que es local, no depende del tunel) y pones
la IP de la LAN de la Pi, sin auth key. Por eso conviene probar el OTA en la red
local antes de mover el dispositivo.

Los logs de MicroLink salen por el mismo monitor serie que el resto del firmware
(`pio device monitor`), con el prefijo `ml_`.
