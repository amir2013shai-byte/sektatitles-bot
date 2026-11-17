#!/usr/bin/env bash
# Установка бота титулов на VPS (Ubuntu/Debian). Запуск из папки с файлами бота:
#     sudo bash install_on_vps.sh
# Скрипт: ставит Python, создаёт venv, ставит зависимости, пишет .env,
# регистрирует systemd-сервис с автоперезапуском и запускает бота.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/titlebot}"
SERVICE="titlebot"
BOT_TOKEN="${BOT_TOKEN:-8884403083:AAFP01Fb17trfjZn8qdfN_UopcsdeV7ZY-U}"
OWNER_IDS="${OWNER_IDS:-6456120346}"

say() { printf "\n\033[1;36m== %s\033[0m\n" "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Запустите от root: sudo bash install_on_vps.sh"; exit 1; }

say "1/6 Пакеты"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip unzip >/dev/null

say "2/6 Файлы в $APP_DIR"
mkdir -p "$APP_DIR"
# если скрипт лежит рядом с файлами бота — копируем их
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$SRC" != "$APP_DIR" ] && [ -f "$SRC/main.py" ]; then
  cp -r "$SRC"/. "$APP_DIR"/
fi
if [ ! -f "$APP_DIR/main.py" ]; then
  echo "✖ В $APP_DIR нет main.py. Загрузите файлы бота (scp/архив) и повторите."
  exit 1
fi
rm -rf "$APP_DIR"/__pycache__ "$APP_DIR"/*/__pycache__ 2>/dev/null || true
mkdir -p "$APP_DIR/data"

say "3/6 Виртуальное окружение и зависимости"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

say "4/6 Настройки (.env)"
cat > "$APP_DIR/.env" <<EOF
BOT_TOKEN=$BOT_TOKEN
OWNER_IDS=$OWNER_IDS
DATABASE_PATH=$APP_DIR/data/titlebot.sqlite3
START_COINS=50
DAILY_BONUS=75
PAYMENTS_ENABLED=1
AUTO_BACKUP_HOURS=24
LOG_LEVEL=INFO
EOF
chmod 600 "$APP_DIR/.env"

say "5/6 Сервис systemd (автозапуск и перезапуск при падении)"
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Sekta Titles Telegram bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
systemctl restart "$SERVICE"

say "6/6 Проверка"
sleep 8
systemctl --no-pager --lines=0 status "$SERVICE" || true
echo
journalctl -u "$SERVICE" --no-pager -n 15 || true
echo
if journalctl -u "$SERVICE" --no-pager -n 40 | grep -q "Run polling for bot"; then
  echo "✅ Бот подключился к Telegram и работает 24/7."
else
  echo "⚠️ Строка 'Run polling for bot' не найдена — смотрите логи выше."
fi
echo
echo "Полезное:"
echo "  journalctl -u $SERVICE -f          # логи в реальном времени"
echo "  systemctl restart $SERVICE         # перезапуск"
echo "  systemctl stop $SERVICE            # остановить"
