#!/usr/bin/env bash
# install.sh — установка VPN-сервера Симбионта на чистый Ubuntu 22.04/24.04.
#
# Что делает (всё идемпотентно — можно запускать повторно):
#   1) ставит sing-box (официальный бинарник под архитектуру сервера);
#   2) генерирует самоподписанный TLS-серт для Hysteria2;
#   3) кладёт server config.json (из ./out/config.json рядом со скриптом ИЛИ /etc/symbiont/config.json);
#   4) поднимает systemd-сервис sing-box-symbiont и автозапуск;
#   5) открывает порты в firewall (ufw, если есть).
#
# ВАЖНО: сам config.json генерируется ОТДЕЛЬНО на твоей машине:
#   python3 gen_server.py --host <IP> --code NL --country Netherlands --out ./out
# затем копируешь папку server/ на сервер и запускаешь:
#   sudo bash install.sh
#
# Запуск только от root/sudo.
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "${GREEN}[symbiont]${NC} $*"; }
warn() { echo -e "${YELLOW}[symbiont]${NC} $*"; }
die()  { echo -e "${RED}[symbiont] ОШИБКА:${NC} $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "запусти от root:  sudo bash install.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETC=/etc/symbiont
mkdir -p "$ETC"

# ── 1) config.json: ищем рядом (out/config.json) или уже в /etc/symbiont ──
SRC_CFG=""
if [ -f "$SCRIPT_DIR/out/config.json" ]; then SRC_CFG="$SCRIPT_DIR/out/config.json"
elif [ -f "$SCRIPT_DIR/config.json" ]; then SRC_CFG="$SCRIPT_DIR/config.json"
elif [ -f "$ETC/config.json" ]; then SRC_CFG="$ETC/config.json"
fi
[ -n "$SRC_CFG" ] || die "не найден config.json. Сначала сгенерируй его:
  python3 gen_server.py --host <IP> --code NL --country Netherlands --out ./out
и положи папку out рядом с install.sh"
if [ "$SRC_CFG" != "$ETC/config.json" ]; then
  cp "$SRC_CFG" "$ETC/config.json"
  say "config.json установлен в $ETC/config.json"
fi

# ── 2) ставим sing-box (официальный бинарник) ──
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) SB_ARCH="amd64" ;;
  aarch64|arm64) SB_ARCH="arm64" ;;
  *) die "архитектура $ARCH не поддерживается этим скриптом" ;;
esac

if ! command -v sing-box >/dev/null 2>&1; then
  say "ставлю sing-box ($SB_ARCH)…"
  apt-get update -qq && apt-get install -y -qq curl tar openssl jq >/dev/null
  # узнаём последнюю версию
  SB_VER="$(curl -fsSL https://api.github.com/repos/SagerNet/sing-box/releases/latest | jq -r .tag_name | sed 's/^v//')"
  [ -n "$SB_VER" ] && [ "$SB_VER" != "null" ] || die "не удалось узнать версию sing-box (нет сети/лимит GitHub)"
  TMP="$(mktemp -d)"
  URL="https://github.com/SagerNet/sing-box/releases/download/v${SB_VER}/sing-box-${SB_VER}-linux-${SB_ARCH}.tar.gz"
  say "скачиваю $URL"
  curl -fsSL "$URL" -o "$TMP/sb.tar.gz" || die "не удалось скачать sing-box"
  tar -xzf "$TMP/sb.tar.gz" -C "$TMP"
  install -m 0755 "$TMP/sing-box-${SB_VER}-linux-${SB_ARCH}/sing-box" /usr/local/bin/sing-box
  rm -rf "$TMP"
  say "sing-box установлен: $(sing-box version | head -1)"
else
  say "sing-box уже установлен: $(sing-box version | head -1)"
fi

# ── 3) самоподписанный серт для Hysteria2 (если ещё нет) ──
if [ ! -f "$ETC/cert.pem" ] || [ ! -f "$ETC/key.pem" ]; then
  say "генерирую самоподписанный TLS-серт для Hysteria2…"
  openssl ecparam -genkey -name prime256v1 -out "$ETC/key.pem" 2>/dev/null
  openssl req -new -x509 -days 3650 -key "$ETC/key.pem" -out "$ETC/cert.pem" \
    -subj "/CN=www.bing.com" 2>/dev/null
  chmod 600 "$ETC/key.pem"
  say "серт создан ($ETC/cert.pem)"
else
  say "TLS-серт уже есть"
fi

# ── 4) проверяем конфиг и поднимаем systemd ──
say "проверяю конфиг (sing-box check)…"
sing-box check -c "$ETC/config.json" || die "конфиг невалиден — проверь $ETC/config.json"
say "конфиг валиден ✔"

cat > /etc/systemd/system/sing-box-symbiont.service <<'UNIT'
[Unit]
Description=Symbiont sing-box server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/sing-box run -c /etc/symbiont/config.json
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576
# базовая изоляция
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/etc/symbiont
ProtectHome=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable sing-box-symbiont >/dev/null 2>&1 || true
systemctl restart sing-box-symbiont
sleep 2
if systemctl is-active --quiet sing-box-symbiont; then
  say "сервис запущен ✔  (sing-box-symbiont)"
else
  warn "сервис не активен — смотри журнал:  journalctl -u sing-box-symbiont -n 50 --no-pager"
fi

# ── 5) firewall (ufw) ──
if command -v ufw >/dev/null 2>&1; then
  RP="$(jq -r '.inbounds[] | select(.tag=="vless-reality") | .listen_port' "$ETC/config.json")"
  HP="$(jq -r '.inbounds[] | select(.tag=="hysteria2") | .listen_port' "$ETC/config.json")"
  SP="$(jq -r '.inbounds[] | select(.tag=="ss2022") | .listen_port' "$ETC/config.json")"
  say "открываю порты в ufw: $RP/tcp, $HP/udp, $SP/tcp (+22/tcp ssh)…"
  ufw allow 22/tcp >/dev/null 2>&1 || true
  ufw allow "${RP}/tcp" >/dev/null 2>&1 || true
  ufw allow "${HP}/udp" >/dev/null 2>&1 || true
  ufw allow "${SP}/tcp" >/dev/null 2>&1 || true
  warn "если ufw был выключен — включи вручную:  ufw enable  (не потеряй SSH!)"
else
  warn "ufw не найден — открой порты в firewall провайдера вручную (Reality TCP, Hysteria2 UDP, SS TCP)."
fi

echo
say "ГОТОВО. Полезные команды:"
echo "  статус:   systemctl status sing-box-symbiont"
echo "  журнал:   journalctl -u sing-box-symbiont -f"
echo "  рестарт:  systemctl restart sing-box-symbiont"
echo
say "Теперь зарегистрируй узел в манифесте бэкенда (manifest_node.json) — см. server/README.md"
