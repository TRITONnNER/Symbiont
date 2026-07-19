#!/usr/bin/env bash
# deploy-backend.sh — Симбионт: развернуть БЭКЕНД одной командой.
#
# На чистом VPS (Ubuntu 22.04/24.04, от root):
#
#   curl -fsSL https://raw.githubusercontent.com/TRITONnNER/Symbiont/main/symbiont_build/server/deploy-backend.sh \
#     | sudo bash -s -- --domain api.example.com --email you@example.com
#
# Без домена (только по IP, без TLS):  … | sudo bash -s -- --no-tls
#
# Что делает: ставит зависимости, тянет код, venv+pip, генерит секреты, поднимает
# systemd-сервис (uvicorn, ОДИН воркер — состояние в памяти+json), nginx + TLS (certbot),
# durable-данные в /var/lib/symbiont. В конце печатает base URL, ADMIN_TOKEN и NODE_SECRET
# (их подставляете в get.sh для узлов и в панель управления).
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[deploy]${NC} $*"; }
die()  { echo -e "${RED}[deploy] ОШИБКА:${NC} $*" >&2; exit 1; }
[ "$(id -u)" = "0" ] || die "запусти от root (sudo)"

DOMAIN=""; EMAIL=""; TLS=1; PORT=8000
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2;;
    --email) EMAIL="${2:-}"; shift 2;;
    --no-tls) TLS=0; shift;;
    --port) PORT="${2:-8000}"; shift 2;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) shift;;
  esac
done
[ "$TLS" = "1" ] && [ -z "$DOMAIN" ] && { warn "нет --domain → ставлю без TLS (по IP)"; TLS=0; }

REF="${SYMBIONT_REF:-main}"
REPO="${SYMBIONT_REPO:-https://github.com/TRITONnNER/Symbiont}"
SRCDIR=/opt/symbiont-src
BACKEND="$SRCDIR/symbiont_build/backend"
DATA=/var/lib/symbiont
VENV=/opt/symbiont-backend/.venv

say "ставлю зависимости…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1 || true
apt-get install -y -qq git python3 python3-venv python3-pip nginx curl ca-certificates >/dev/null 2>&1 || true
[ "$TLS" = "1" ] && { apt-get install -y -qq certbot python3-certbot-nginx >/dev/null 2>&1 || warn "certbot не поставился"; }

say "тяну код ($REPO @ $REF)…"
if [ -d "$SRCDIR/.git" ]; then git -C "$SRCDIR" fetch -q --depth 1 origin "$REF" && git -C "$SRCDIR" reset -q --hard FETCH_HEAD
else git clone -q --depth 1 -b "$REF" "$REPO" "$SRCDIR" || die "git clone не удался (проверь SYMBIONT_REPO/SYMBIONT_REF)"; fi
[ -f "$BACKEND/server.py" ] || die "не нашёл $BACKEND/server.py"

say "venv + зависимости Python…"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
"$VENV/bin/pip" install -q -r "$BACKEND/requirements.txt" >/dev/null || die "pip install не удался"

mkdir -p "$DATA"
# Секреты и весь стейт — в symbiont_state.db (CWD=$DATA). Первый импорт генерит недостающие
# и сохраняет; читаем их как атрибуты модуля (не как файл — persist хранит в SQLite).
say "генерирую секреты…"
ADMIN_TOKEN="$( cd "$DATA" && PYTHONPATH="$BACKEND" "$VENV/bin/python" -c "import server;print(server.ADMIN_TOKEN)" 2>/dev/null || true )"
NODE_SECRET="$( cd "$DATA" && PYTHONPATH="$BACKEND" "$VENV/bin/python" -c "import server;print(server.NODE_SECRET)" 2>/dev/null || true )"
[ -n "$ADMIN_TOKEN" ] && [ -n "$NODE_SECRET" ] || die "не удалось инициализировать бэкенд/секреты"

say "systemd-сервис (uvicorn, один воркер)…"
cat > /etc/systemd/system/symbiont-backend.service <<EOF
[Unit]
Description=Symbiont backend (FastAPI)
After=network.target
[Service]
WorkingDirectory=$DATA
Environment=PYTHONPATH=$BACKEND
Environment=SYMBIONT_ENV=prod
Environment=SYMBIONT_CORS_ORIGINS=${CORS_ORIGINS:-*}
# Платежи по умолчанию БЕЗ фейкового подтверждения: purchase вернёт pending, пока не
# подключён боевой провайдер (его checkoutUrl + вебхук /v1/billing/webhook). Раньше
# здесь стоял mock — «боевой» деплой из коробки принимал покупки без денег. Чтобы
# осознанно включить ТЕСТОВЫЙ режим (страница оплаты без списания), запусти скрипт как:
#   PAY_MODE=mock sudo ./deploy-backend.sh …
Environment=SYMBIONT_PAY_PROVIDER=${PAY_MODE:-sandbox}
# ВНИМАНИЕ: один воркер — состояние в SQLite (symbiont_state.db, WAL) в $DATA.
# Не масштабировать воркерами без вынесения in-memory-структур (см. роадмап).
ExecStart=$VENV/bin/uvicorn server:app --host 127.0.0.1 --port $PORT --workers 1
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now symbiont-backend >/dev/null 2>&1 || true
sleep 2
systemctl is-active --quiet symbiont-backend || { journalctl -u symbiont-backend -n 20 --no-pager; die "бэкенд не запустился"; }

say "ежедневный бэкап БД (systemd-таймер, онлайн-снимок SQLite)…"
cat > /etc/systemd/system/symbiont-backup.service <<EOF
[Unit]
Description=Symbiont DB backup (consistent SQLite snapshot)
[Service]
Type=oneshot
WorkingDirectory=$DATA
Environment=PYTHONPATH=$BACKEND
ExecStart=$VENV/bin/python $BACKEND/db_backup.py $DATA/backups
EOF
cat > /etc/systemd/system/symbiont-backup.timer <<EOF
[Unit]
Description=Daily Symbiont DB backup
[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now symbiont-backup.timer >/dev/null 2>&1 || true
$VENV/bin/python "$BACKEND/db_backup.py" "$DATA/backups" >/dev/null 2>&1 || warn "первый бэкап не сделан (сделается по таймеру)"

say "nginx…"
SERVER_NAME="${DOMAIN:-_}"
cat > /etc/nginx/sites-available/symbiont <<EOF
server {
    listen 80;
    server_name $SERVER_NAME;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sfn /etc/nginx/sites-available/symbiont /etc/nginx/sites-enabled/symbiont
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t >/dev/null 2>&1 && systemctl reload nginx || warn "nginx -t не прошёл — проверь конфиг"

BASE="http://${DOMAIN:-$(curl -fsS --max-time 8 ifconfig.me 2>/dev/null || echo SERVER_IP)}"
if [ "$TLS" = "1" ] && command -v certbot >/dev/null 2>&1; then
  say "TLS через certbot для $DOMAIN…"
  certbot --nginx -n --agree-tos ${EMAIL:+-m "$EMAIL"} ${EMAIL:+} -d "$DOMAIN" >/dev/null 2>&1 \
    && { BASE="https://$DOMAIN"; say "TLS выдан ✓"; } || warn "certbot не выдал серт (домен должен указывать на этот IP; порт 80 открыт)"
fi

echo ""
say "ГОТОВО. Бэкенд Симбионта развёрнут."
echo "  Base URL:     $BASE"
echo "  ADMIN_TOKEN:  ${ADMIN_TOKEN:-<initialize error>}   (он же вход ВЛАДЕЛЬЦА в панель)"
echo "  NODE_SECRET:  ${NODE_SECRET:-<initialize error>}"
echo ""
echo "  База данных:  РЕАЛЬНАЯ SQLite/WAL → $DATA/symbiont_state.db"
echo "                ежедневный бэкап (03:30) → $DATA/backups/ (symbiont-backup.timer)"
echo "  Платежи:      ТЕСТОВЫЙ режим (mock): покупка проходит как реальная, деньги НЕ списываются."
echo ""
echo "  Панель управления (Server-in-a-Box console) — открыть на любом устройстве:"
echo "    файл symbiont_build/server/sib-console.html → Base URL: $BASE, токен: ADMIN_TOKEN (владелец)"
echo "    работникам выдать ограниченный доступ:  POST $BASE/v1/admin/staff  (name, scopes)"
echo ""
echo "  Поднять узел (на ДРУГОМ VPS):"
echo "    curl -fsSL $BASE/../get.sh | sudo bash -s -- --register-to $BASE --node-secret ${NODE_SECRET:-<SECRET>}"
echo "    (get.sh: см. docs/СИМБИОНТ_server_in_a_box.md)"
echo "  Управление флотом:  GET $BASE/v1/admin/nodes  (заголовок X-Admin-Token: ${ADMIN_TOKEN:-…})"
echo "  Обновить бэкенд:    повторить эту же команду (идемпотентно)."
warn "Платежи: покупки возвращают pending, пока не подключён боевой провайдер (checkoutUrl + вебхук)."
