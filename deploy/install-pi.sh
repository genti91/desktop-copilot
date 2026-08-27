#!/usr/bin/env bash
#
# Instala el backend de Desktop Co-Pilot en una Raspberry Pi con OctoPi.
#
#   curl -fsSL https://raw.githubusercontent.com/genti91/desktop-copilot/main/deploy/install-pi.sh | bash
#
# o, con el repo ya clonado:
#
#   bash ~/desktop-copilot/deploy/install-pi.sh
#
# Es idempotente: se puede volver a correr para actualizar.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/genti91/desktop-copilot.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/desktop-copilot}"
SERVICE_NAME="desktop-copilot"
PORT="${PORT:-8000}"

RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$1"; }
die() { printf '\033[1;31m[x] %s\033[0m\n' "$1" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "No lo corras como root. Usá tu usuario normal; el script pide sudo cuando hace falta."
command -v systemctl >/dev/null || die "Esto espera un sistema con systemd (Raspberry Pi OS / OctoPi)."

# Si sudo pide password interactiva no podemos usarlo (por ejemplo corriendo por
# SSH sin TTY). En ese caso hacemos todo lo demás y dejamos anotado lo que falta.
HAVE_SUDO=1
sudo -n true 2>/dev/null || HAVE_SUDO=0

# --------------------------------------------------------------------------- #

MISSING_PACKAGES=()
for package in git python3-venv python3-dev build-essential; do
  dpkg -s "$package" >/dev/null 2>&1 || MISSING_PACKAGES+=("$package")
done

if [ ${#MISSING_PACKAGES[@]} -eq 0 ]; then
  say "Dependencias del sistema: ya estaban todas"
elif [ "$HAVE_SUDO" -eq 1 ]; then
  say "Dependencias del sistema"
  sudo apt-get update -qq
  sudo apt-get install -y "${MISSING_PACKAGES[@]}"
else
  die "Faltan paquetes y sudo pide password. Corré primero:
    sudo apt-get install -y ${MISSING_PACKAGES[*]}"
fi

# --------------------------------------------------------------------------- #

if [ -d "$INSTALL_DIR/.git" ]; then
  say "Actualizando el repo en $INSTALL_DIR (rama $BRANCH)"
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
else
  say "Clonando en $INSTALL_DIR (rama $BRANCH)"
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

BACKEND_DIR="$INSTALL_DIR/backend"
[ -d "$BACKEND_DIR" ] || die "No encontré $BACKEND_DIR; ¿el repo es el correcto?"

# --------------------------------------------------------------------------- #

say "Entorno virtual"
[ -d "$BACKEND_DIR/venv" ] || python3 -m venv "$BACKEND_DIR/venv"
"$BACKEND_DIR/venv/bin/pip" install --upgrade pip --quiet

say "Instalando dependencias de Python (unos minutos en ARM)"
"$BACKEND_DIR/venv/bin/pip" install -r "$BACKEND_DIR/requirements.txt"

# --------------------------------------------------------------------------- #

say "Configuración"
ENV_FILE="$BACKEND_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$BACKEND_DIR/.env.example" "$ENV_FILE"
  echo "Creé $ENV_FILE a partir del ejemplo."
fi

# El panel sin password queda abierto a cualquiera que alcance el backend,
# y eso incluye poder publicar firmware que el ESP32 después flashea.
if ! grep -qE '^PANEL_PASSWORD=.+' "$ENV_FILE"; then
  GENERATED="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  if grep -qE '^PANEL_PASSWORD=' "$ENV_FILE"; then
    sed -i "s|^PANEL_PASSWORD=.*|PANEL_PASSWORD=$GENERATED|" "$ENV_FILE"
  else
    printf '\nPANEL_PASSWORD=%s\n' "$GENERATED" >> "$ENV_FILE"
  fi
  say "Password del panel generada"
  printf '\n    \033[1;32m%s\033[0m\n\n' "$GENERATED"
  echo "Guardala ahora. Está en $ENV_FILE si la perdés."
fi

if ! grep -qE '^GEMINI_API_KEY=.+' "$ENV_FILE"; then
  warn "Falta GEMINI_API_KEY en $ENV_FILE."
  warn "El panel, la config del dispositivo y el OTA funcionan igual, pero el"
  warn "asistente de voz no va a responder hasta que la pongas y reinicies."
fi

# --------------------------------------------------------------------------- #

say "Servicio de systemd"
UNIT_TEMPLATE="$INSTALL_DIR/deploy/$SERVICE_NAME.service"
# Rodeo mientras el ESP32 no pueda iniciar el handshake de WireGuard.
# Ver docs/raspberry-pi.md, "El ESP32 desde cualquier red".
KEEPALIVE_NAME="$SERVICE_NAME-keepalive"
KEEPALIVE_UNIT="$INSTALL_DIR/deploy/$KEEPALIVE_NAME.service"
[ -f "$UNIT_TEMPLATE" ] || die "No encontré $UNIT_TEMPLATE"

# La plantilla viene con el usuario 'pi'; la ajustamos a quien esté instalando.
# Ruta estable en vez de mktemp: si sudo no esta disponible, el usuario tiene que
# poder copiarlo despues sin que /tmp se lo haya limpiado.
GENERATED_UNIT="$INSTALL_DIR/deploy/$SERVICE_NAME.generated.service"
sed -e "s|^User=.*|User=$RUN_USER|" \
    -e "s|^Group=.*|Group=$RUN_GROUP|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=$BACKEND_DIR|" \
    -e "s|^ExecStart=.*|ExecStart=$BACKEND_DIR/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT|" \
    "$UNIT_TEMPLATE" > "$GENERATED_UNIT"

if [ "$HAVE_SUDO" -eq 0 ]; then
  warn "sudo pide password, asi que no puedo instalar el servicio."
  echo "Ya quedo todo lo demas listo. Corré estos tres comandos para terminar:"
  cat <<PENDIENTE

  sudo cp $GENERATED_UNIT /etc/systemd/system/$SERVICE_NAME.service
  sudo cp $KEEPALIVE_UNIT /etc/systemd/system/$KEEPALIVE_NAME.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now $SERVICE_NAME $KEEPALIVE_NAME

PENDIENTE
  echo "Y verificá con:  curl -s localhost:$PORT/health"
  exit 0
fi

sudo cp "$GENERATED_UNIT" "/etc/systemd/system/$SERVICE_NAME.service"
rm -f "$GENERATED_UNIT"
sudo cp "$KEEPALIVE_UNIT" "/etc/systemd/system/$KEEPALIVE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" >/dev/null
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl enable "$KEEPALIVE_NAME" >/dev/null
sudo systemctl restart "$KEEPALIVE_NAME"

# --------------------------------------------------------------------------- #

say "Verificando"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if curl -fsS "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"status":"ok"'; then
  HEALTH="$(curl -fsS "http://127.0.0.1:$PORT/health")"
  echo "  /health -> $HEALTH"
  case "$HEALTH" in
    *'"auth":false'*) warn "El panel está SIN password. Poné PANEL_PASSWORD en $ENV_FILE y reiniciá." ;;
  esac
  case "$HEALTH" in
    *'"ai":false'*) warn "Sin GEMINI_API_KEY: el asistente de voz queda mudo." ;;
  esac
else
  warn "El servicio no respondió a tiempo. Mirá los logs:"
  echo "  journalctl -u $SERVICE_NAME -n 50 --no-pager"
  exit 1
fi

LAN_IP="$(hostname -I | awk '{print $1}')"

say "Listo"
cat <<RESUMEN
  Panel:    http://$LAN_IP:$PORT/notes
  ESP32:    http://$LAN_IP:$PORT/voice-assistant
            (ponelo en el portal de WiFiManager, y reservá esta IP en el router)

  Logs:     journalctl -u $SERVICE_NAME -f
  Reiniciar: sudo systemctl restart $SERVICE_NAME

  Para entrar desde afuera sin port forwarding:
    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up
RESUMEN
