#!/usr/bin/env bash
# get.sh — Симбионт «Server-in-a-Box»: установка VPN-узла ОДНОЙ командой.
#
# На чистом VPS (Ubuntu 22.04/24.04) выполнить ОДНУ строку:
#
#   curl -fsSL https://raw.githubusercontent.com/TRITONnNER/Symbiont/main/symbiont_build/server/get.sh \
#     | sudo bash -s -- --register-to https://ВАШ_БЭКЕНД --node-secret СЕКРЕТ_ОПЕРАТОРА
#
# Что делает само:
#   1) ставит зависимости (python3, curl, …);
#   2) скачивает набор скриптов узла в /opt/symbiont;
#   3) запускает bootstrap_vps.sh: авто-подбор страны/SNI, установка sing-box (Reality+
#      Hysteria2+SS2022), systemd, firewall, САМО-РЕГИСТРАЦИЯ в манифесте бэкенда, heartbeat;
#   4) ставит CLI управления `symbiont-node` (status/logs/restart/update/remove).
#
# После — узел САМ появляется в списке серверов клиента. Флаги --code/--country/--sni
# необязательны (авто-подбор); их и любые другие флаги можно передать после -- .
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "${GREEN}[server-in-a-box]${NC} $*"; }
warn() { echo -e "${YELLOW}[server-in-a-box]${NC} $*"; }
die()  { echo -e "${RED}[server-in-a-box] ОШИБКА:${NC} $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "запусти от root:  curl … | sudo bash -s -- …"

# ── аргументы: --register-to и --node-secret обязательны; остальное пробрасываем ──
REGISTER_TO=""; NODE_SECRET=""; PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --register-to) REGISTER_TO="${2:-}"; PASS+=("$1" "$2"); shift 2;;
    --node-secret) NODE_SECRET="${2:-}"; PASS+=("$1" "$2"); shift 2;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) PASS+=("$1"); shift;;
  esac
done
[ -n "$REGISTER_TO" ] || die "нужен --register-to https://ВАШ_БЭКЕНД"
[ -n "$NODE_SECRET" ] || die "нужен --node-secret <секрет оператора> (SYMBIONT_NODE_SECRET бэкенда)"

# ── откуда качать скрипты (переопределяется SYMBIONT_SRC=…) ──
SRC="${SYMBIONT_SRC:-https://raw.githubusercontent.com/TRITONnNER/Symbiont/${SYMBIONT_REF:-main}/symbiont_build}"
DEST=/opt/symbiont

say "ставлю зависимости…"
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y -qq curl ca-certificates python3 python3-pip >/dev/null 2>&1 || true
fi
command -v curl >/dev/null 2>&1 || die "нет curl"
command -v python3 >/dev/null 2>&1 || die "нет python3"

say "скачиваю набор узла из $SRC …"
mkdir -p "$DEST/server" "$DEST/backend"
fetch() { curl -fsSL "$SRC/$1" -o "$DEST/$1" || die "не скачался $1 (проверь SYMBIONT_SRC/ветку)"; }
for f in server/bootstrap_vps.sh server/install.sh server/gen_server.py \
         server/register_node.py server/heartbeat_node.py server/symbiont-node \
         backend/node_autotune.py; do
  fetch "$f"
done
chmod +x "$DEST/server/bootstrap_vps.sh" "$DEST/server/symbiont-node" 2>/dev/null || true

# запомним параметры оператора для управления (update/remove)
mkdir -p /etc/symbiont
cat > /etc/symbiont/sib.env <<EOF
SYMBIONT_SRC="$SRC"
REGISTER_TO="$REGISTER_TO"
NODE_SECRET="$NODE_SECRET"
DEST="$DEST"
EOF
chmod 600 /etc/symbiont/sib.env

say "запускаю установку узла (bootstrap)…"
bash "$DEST/server/bootstrap_vps.sh" "${PASS[@]}"

# CLI управления в PATH
install -m 0755 "$DEST/server/symbiont-node" /usr/local/bin/symbiont-node 2>/dev/null || \
  { cp "$DEST/server/symbiont-node" /usr/local/bin/symbiont-node; chmod +x /usr/local/bin/symbiont-node; }

say "готово. Узел устанавливается/регистрируется и скоро появится в списке серверов."
say "управление:  symbiont-node status | logs | restart | update | remove"
