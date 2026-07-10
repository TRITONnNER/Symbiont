#!/usr/bin/env bash
# bootstrap_vps.sh — ОДНА КОМАНДА превращает чистый Ubuntu 22.04/24.04 VPS в полностью
# работающий сервис «Симбионт»: и VPN-узел (sing-box, 3 протокола), и управляющий
# бэкенд (FastAPI: аккаунты/ключи/подписанный манифест), уже связанные между собой.
#
# Что делает (всё идемпотентно — можно гонять повторно):
#   1) определяет публичный IP сервера сам;
#   2) генерирует конфиг узла + секреты + запись манифеста (gen_server.py);
#   3) ставит sing-box и поднимает узел (через install.sh) — systemd, автозапуск;
#   4) разворачивает бэкенд: venv, зависимости, ключ подписи, systemd на 0.0.0.0:8000;
#   5) СВЯЗЫВАЕТ узел с бэкендом (publish_nodes.py → nodes.json) — манифест отдаёт
#      реальный узел, подписанный Ed25519;
#   6) открывает порты в firewall;
#   7) печатает адрес бэкенда + ПРИБИТЫЙ ключ + готовую команду сборки клиента.
#
# Использование (после покупки VPS):
#   scp -r symbiont_build/ root@<IP>:/root/
#   ssh root@<IP>
#   cd /root/symbiont_build/server
#   sudo bash bootstrap_vps.sh --code NL --country Netherlands --sni www.microsoft.com
#
# Параметры (все необязательные — есть дефолты):
#   --host <IP|домен>   адрес узла (по умолчанию — автоопределение публичного IP)
#   --code <ISO>        код страны для флага (NL)
#   --country <имя>     название страны (Netherlands)
#   --sni <домен>       маскировочный SNI Reality (www.microsoft.com)
#   --backend-port <N>  порт бэкенда (8000)
#   --no-backend        поднять ТОЛЬКО узел (бэкенд развернуть отдельно)
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
say()  { echo -e "${GREEN}[symbiont]${NC} $*"; }
warn() { echo -e "${YELLOW}[symbiont]${NC} $*"; }
die()  { echo -e "${RED}[symbiont] ОШИБКА:${NC} $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "запусти от root:  sudo bash bootstrap_vps.sh"

# ── параметры ────────────────────────────────────────────────────────────────
# Пустые code/country/sni = «не задано вручную» → возьмём из авто-подбора (node_autotune).
HOST=""; CODE=""; COUNTRY=""; SNI=""; NODE_ID=""; AUTOTUNE="yes"
BACKEND_PORT="8000"; DO_BACKEND="yes"; ROLE="node"; REGISTER_TO=""; NODE_SECRET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2;;
    --code) CODE="$2"; shift 2;;
    --country) COUNTRY="$2"; shift 2;;
    --sni) SNI="$2"; shift 2;;
    --node-id) NODE_ID="$2"; shift 2;;
    --backend-port) BACKEND_PORT="$2"; shift 2;;
    --role) ROLE="$2"; shift 2;;
    --register-to) REGISTER_TO="$2"; shift 2;;   # узел сам зарегистрируется на этом бэкенде
    --node-secret) NODE_SECRET="$2"; shift 2;;   # секрет оператора для саморегистрации
    --no-autotune) AUTOTUNE="no"; shift;;        # отключить авто-подбор (взять дефолты)
    --no-backend) DO_BACKEND="no"; shift;;
    *) die "неизвестный параметр: $1";;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # корень проекта (где backend/, server/)
BACKEND_SRC="$ROOT_DIR/backend"
[ -f "$SCRIPT_DIR/gen_server.py" ] || die "gen_server.py не найден рядом — запускай из папки server/ распакованного проекта"
[ -d "$BACKEND_SRC" ] || die "не найден $BACKEND_SRC — нужен весь проект, не только server/"

# ── 0) базовые пакеты ────────────────────────────────────────────────────────
say "ставлю базовые пакеты (python3, pip, curl, jq, openssl)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl tar jq openssl >/dev/null

# ── 1) публичный IP ──────────────────────────────────────────────────────────
if [ -z "$HOST" ]; then
  say "определяю публичный IP сервера…"
  for svc in "https://api.ipify.org" "https://ifconfig.me" "https://ipinfo.io/ip"; do
    HOST="$(curl -fsS --max-time 8 "$svc" 2>/dev/null | tr -d '[:space:]')" || true
    [ -n "$HOST" ] && break
  done
  [ -n "$HOST" ] || die "не удалось определить IP — задай вручную: --host <IP>"
fi
say "адрес узла: ${BOLD}$HOST${NC}"

# ── 1b) АВТО-ПОДБОР лучших параметров узла (гео, живой SNI, протоколы, ёмкость) ─
# node_autotune.py сам меряет СВОЮ сеть: страну по IP, самый быстрый живой SNI из
# пула, есть ли UDP-egress (для Hysteria2), стартовую загрузку по скорости. Явно
# заданные флаги (--code/--country/--sni/--node-id) имеют приоритет над авто-подбором.
if [ "$AUTOTUNE" = "yes" ]; then
  say "авто-подбор лучших параметров узла (гео · SNI · протоколы · ёмкость)…"
  AT="$(python3 "$BACKEND_SRC/node_autotune.py" --host "$HOST" --role "$ROLE" 2>/dev/null || true)"
  if [ -n "$AT" ] && echo "$AT" | jq -e . >/dev/null 2>&1; then
    [ -z "$CODE" ]    && CODE="$(echo "$AT"    | jq -r '.code // empty')"
    [ -z "$COUNTRY" ] && COUNTRY="$(echo "$AT" | jq -r '.country // empty')"
    [ -z "$SNI" ]     && SNI="$(echo "$AT"     | jq -r '.sni // empty')"
    [ -z "$NODE_ID" ] && NODE_ID="$(echo "$AT" | jq -r '.node_id // empty')"
    say "авто-подбор: страна=${BOLD}${CODE}/${COUNTRY}${NC}  SNI=${BOLD}${SNI}${NC}  id=${BOLD}${NODE_ID}${NC}"
  else
    warn "авто-подбор не удался (нет egress?) — беру значения по умолчанию"
  fi
fi
# дефолты, если ни флаг, ни авто-подбор не задали
CODE="${CODE:-NL}"; COUNTRY="${COUNTRY:-Netherlands}"; SNI="${SNI:-www.microsoft.com}"

# ── 2) генерация конфига узла ────────────────────────────────────────────────
OUT="/etc/symbiont/out"
mkdir -p "$OUT"
say "генерирую конфиг узла (Reality/Hysteria2/SS2022)…"
python3 -m pip install -q --break-system-packages cryptography >/dev/null 2>&1 || \
  python3 -m pip install -q cryptography >/dev/null 2>&1 || true
python3 "$SCRIPT_DIR/gen_server.py" --host "$HOST" --code "$CODE" --country "$COUNTRY" \
  --role "$ROLE" --sni "$SNI" ${NODE_ID:+--node-id "$NODE_ID"} --out "$OUT" >/dev/null
say "узел сгенерирован → $OUT (config.json, secrets.json, manifest_node.json)"

# ── 3) ставим узел (sing-box) через install.sh ───────────────────────────────
# install.sh ищет out/config.json рядом с собой — кладём симлинк, чтобы переиспользовать.
ln -sfn "$OUT" "$SCRIPT_DIR/out"
say "ставлю sing-box и поднимаю узел…"
bash "$SCRIPT_DIR/install.sh"

# ── 4) разворачиваем бэкенд ──────────────────────────────────────────────────
if [ "$DO_BACKEND" = "yes" ]; then
  APP=/opt/symbiont/backend
  say "разворачиваю бэкенд в $APP…"
  mkdir -p "$APP"
  cp -f "$BACKEND_SRC"/*.py "$APP"/ 2>/dev/null || true
  cp -f "$BACKEND_SRC"/requirements.txt "$APP"/ 2>/dev/null || true

  # venv + зависимости
  if [ ! -d "$APP/.venv" ]; then
    python3 -m venv "$APP/.venv"
  fi
  "$APP/.venv/bin/pip" install -q --upgrade pip >/dev/null
  "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt" >/dev/null
  say "зависимости бэкенда установлены"

  # СВЯЗКА: запись узла → nodes.json рядом с бэкендом (его подхватит манифест)
  python3 "$SCRIPT_DIR/publish_nodes.py" "$OUT/manifest_node.json" --out "$APP/nodes.json" >/dev/null
  say "узел вписан в манифест бэкенда (nodes.json)"

  # systemd для бэкенда (uvicorn, слушает все интерфейсы)
  cat > /etc/systemd/system/symbiont-backend.service <<UNIT
[Unit]
Description=Symbiont control backend (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP
Environment=SYMBIONT_NODE_SECRET=${NODE_SECRET:-demo-node-secret}
ExecStart=$APP/.venv/bin/uvicorn server:app --host 0.0.0.0 --port $BACKEND_PORT
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$APP
ProtectHome=true

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable symbiont-backend >/dev/null 2>&1 || true
  systemctl restart symbiont-backend
  sleep 3
  if systemctl is-active --quiet symbiont-backend; then
    say "бэкенд запущен ✔  (symbiont-backend, порт $BACKEND_PORT)"
  else
    warn "бэкенд не активен — журнал:  journalctl -u symbiont-backend -n 50 --no-pager"
  fi

  # firewall: порт бэкенда
  command -v ufw >/dev/null 2>&1 && ufw allow "${BACKEND_PORT}/tcp" >/dev/null 2>&1 || true
fi

# ── 4b) саморегистрация на удалённом бэкенде (узел на отдельном VPS) ──────────
if [ -n "$REGISTER_TO" ]; then
  say "регистрирую узел на бэкенде $REGISTER_TO…"
  SECRET_FILE="/etc/symbiont/node_secret"
  if SYMBIONT_NODE_SECRET="${NODE_SECRET:-demo-node-secret}" \
       python3 "$SCRIPT_DIR/register_node.py" --node "$OUT/manifest_node.json" \
         --backend "$REGISTER_TO" --secret-out "$SECRET_FILE"; then
    say "узел вписан в манифест бэкенда ✔ (появится у клиентов автоматически)"
    # ── heartbeat-таймер: узел раз в минуту держит себя живым в манифесте ──
    # Замолчал дольше порога → само-починка выкинет его; вернулся → появится снова.
    cp -f "$SCRIPT_DIR/heartbeat_node.py" /etc/symbiont/heartbeat_node.py
    cat > /etc/systemd/system/symbiont-heartbeat.service <<UNIT
[Unit]
Description=Symbiont node heartbeat
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/env python3 /etc/symbiont/heartbeat_node.py \\
  --node $OUT/manifest_node.json --backend $REGISTER_TO --secret $SECRET_FILE
UNIT
    cat > /etc/systemd/system/symbiont-heartbeat.timer <<UNIT
[Unit]
Description=Symbiont node heartbeat (every 60s)

[Timer]
OnBootSec=30
OnUnitActiveSec=60
AccuracySec=5

[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload
    systemctl enable --now symbiont-heartbeat.timer >/dev/null 2>&1 || true
    say "heartbeat-таймер включён (каждые 60с) — узел сам держится живым в сети"
  else
    warn "не удалось зарегистрировать узел — проверь --register-to и --node-secret"
  fi
fi

# ── 5) итог + ключ + команда сборки клиента ──────────────────────────────────
PUBKEY=""
if [ "$DO_BACKEND" = "yes" ]; then
  for _ in 1 2 3 4 5; do
    PUBKEY="$(curl -fsS --max-time 4 "http://127.0.0.1:${BACKEND_PORT}/v1/pubkey" 2>/dev/null | jq -r '.ed25519 // empty')" || true
    [ -n "$PUBKEY" ] && break; sleep 1
  done
fi

echo
echo -e "${BOLD}════════════════════ СИМБИОНТ: СЕРВИС ПОДНЯТ ════════════════════${NC}"
say "Узел:    sing-box на $HOST  (Reality :443/tcp, Hysteria2 :8443/udp, SS2022 :9443/tcp)"
if [ "$DO_BACKEND" = "yes" ]; then
  say "Бэкенд:  http://$HOST:$BACKEND_PORT   (манифест уже содержит этот узел)"
  if [ -n "$PUBKEY" ]; then
    say "Ключ подписи (прибей в клиент): ${BOLD}$PUBKEY${NC}"
    echo
    say "Сборка клиента «из коробки на твой VPS»:"
    echo "  flutter build windows --release \\"
    echo "    --dart-define=SYMBIONT_BASE_URL=http://$HOST:$BACKEND_PORT \\"
    echo "    --dart-define=SYMBIONT_PUBKEY=$PUBKEY"
  else
    warn "не удалось прочитать ключ подписи — проверь бэкенд и возьми ключ вручную: curl http://$HOST:$BACKEND_PORT/v1/pubkey"
  fi
fi
echo
say "Проверка: systemctl status sing-box-symbiont symbiont-backend"
warn "Если firewall провайдера в панели — открой там: 443/tcp, 8443/udp, 9443/tcp, $BACKEND_PORT/tcp."
warn "Секреты узла: /etc/symbiont/out/secrets.json — НИКОМУ, не в git."
echo -e "${BOLD}═════════════════════════════════════════════════════════════════${NC}"
