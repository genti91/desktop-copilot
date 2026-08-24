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

ESP32 (misma casa)
  -> IP local de la Pi :8000     <- NO pasa por el tunel
```

El ESP32 se queda hablando HTTP plano contra la IP local. No lo mandes por el
tunel: el firmware no tiene TLS (`network.cpp` se conecta con un `WiFiClient`
crudo), asi que una URL `https://` no le va a funcionar.

## Antes de empezar

- Raspberry Pi 4 con 2 GB o mas. En una Pi 3 o Zero 2W (1 GB) el backend compite
  con OctoPrint por memoria y ChromaDB no deja mucho margen.
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

La instalacion de dependencias tarda bastante en ARM: ChromaDB compila algunas
cosas. Si se queda sin memoria, agranda el swap temporalmente
(`sudo dphys-swapfile swapoff`, editar `CONF_SWAPSIZE=2048` en
`/etc/dphys-swapfile`, `sudo dphys-swapfile setup && sudo dphys-swapfile swapon`).

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

**Memoria.** Con OctoPrint imprimiendo, el backend y ChromaDB arriba, una Pi de
2 GB va justa. Si ves el OOM killer en `dmesg`, agrega `MemoryMax=700M` al
service (con el riesgo de que mate requests) o mueve ChromaDB a otra maquina.

**CPU durante impresiones.** El service ya viene con `Nice=5` para que OctoPrint
tenga prioridad y no se arruine un print por un pico del backend.

**Python.** El codigo de `app/` es compatible con Python 3.9, asi que funciona en
imagenes viejas de OctoPi (Bullseye). Los tests usan sintaxis de 3.10+, asi que
si queres correrlos en la Pi necesitas Bookworm o mas nuevo.

**El sync de firmware es saliente.** La Pi consulta la API de GitHub cada 5
minutos; no necesita nada abierto hacia adentro, funciona detras de CGNAT.

## Opcional: acceso publico

Tailscale cubre tus dispositivos. Si necesitas un link que abra en cualquier
navegador sin instalar nada:

```bash
sudo tailscale funnel 8000
```

Te da una URL `https://<maquina>.<tailnet>.ts.net` publica. Funciona solo para
HTTP/HTTPS, no para puertos arbitrarios.

Con eso el backend queda expuesto a internet, asi que `PANEL_PASSWORD` deja de
ser opcional. La alternativa mas robusta es Cloudflare Tunnel con Cloudflare
Access adelante (login con Google/GitHub), usando un dominio propio; el GitHub
Student Pack regala un `.me` en Namecheap por un ano.

Si terminas un tunel en la Pi, ojo con `TRUSTED_NETWORKS`: loopback esta fuera de
la lista a proposito, para que el trafico del tunel no saltee el login.
