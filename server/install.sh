#!/usr/bin/env bash
# Встановлення цілодобового збирача «Рейтинг влучань» на Ubuntu 24.04.
# Запуск (від root):
#   інтерактивно:          bash <(curl -sL https://raw.githubusercontent.com/ugi-analytics/rating/main/server/install.sh)
#   як Startup Script Vultr: curl -sL .../install.sh | bash -s -- --web <КЛЮЧ>
#     (вхід Telegram тоді через сторінку https://<IP-з-дефісами>.sslip.io/login?k=<КЛЮЧ>)
set -e
WEBKEY=""; [ "$1" = "--web" ] && WEBKEY="$2"
exec > >(tee -a /var/log/rating-install.log) 2>&1
REPO=https://github.com/ugi-analytics/rating.git
APP=/opt/rating
say() { echo -e "\n\033[1;33m== $*\033[0m"; }

[ "$(id -u)" = 0 ] || { echo "Запустіть від root"; exit 1; }

say "1/5 Оновлення системи і потрібні програми (2–4 хв)"
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=600 update -qq
apt-get -o DPkg::Lock::Timeout=600 install -y -qq git python3-venv caddy curl >/dev/null

say "2/5 Програма збирача"
id rating >/dev/null 2>&1 || useradd --system --home /var/lib/rating --shell /usr/sbin/nologin rating
if [ -d $APP/.git ]; then git -C $APP pull -q; else git clone -q --depth 1 $REPO $APP; fi
python3 -m venv $APP/venv
$APP/venv/bin/pip install -q --upgrade pip
$APP/venv/bin/pip install -q "telethon>=1.36" qrcode
mkdir -p /etc/rating /var/lib/rating /var/www/rating
chown rating:rating /var/lib/rating /var/www/rating
chgrp rating /etc/rating; chmod 750 /etc/rating

cat > /usr/local/bin/rating-login <<'X'
#!/bin/bash
/opt/rating/venv/bin/python /opt/rating/server/login.py || exit 1
chgrp rating /etc/rating/env /etc/rating/session; chmod 640 /etc/rating/env /etc/rating/session
systemctl restart rating-live 2>/dev/null || true
X
chmod +x /usr/local/bin/rating-login

say "3/5 Початкові дані (з GitHub)"
if [ ! -s /var/lib/rating/events.json ]; then
  curl -sL https://raw.githubusercontent.com/ugi-analytics/rating/data/events.json -o /var/lib/rating/events.json || true
  curl -sL https://raw.githubusercontent.com/ugi-analytics/rating/data/state.json -o /var/lib/rating/state.json || true
  chown rating:rating /var/lib/rating/*.json
fi


say "4/5 Сервіси"
cat > /etc/systemd/system/rating-live.service <<'X'
[Unit]
Description=Рейтинг влучань — цілодобовий збирач
After=network-online.target
Wants=network-online.target
[Service]
User=rating
Group=rating
WorkingDirectory=/opt/rating
ExecStart=/opt/rating/venv/bin/python -u /opt/rating/server/live.py
Restart=always
RestartSec=20
[Install]
WantedBy=multi-user.target
X
# автооновлення програми й списку каналів з GitHub кожні 10 хв
cat > /usr/local/bin/rating-update <<'X'
#!/bin/bash
cd /opt/rating || exit 0
old=$(git rev-parse HEAD); git pull -q --ff-only || exit 0
[ "$old" != "$(git rev-parse HEAD)" ] && systemctl restart rating-live && echo "оновлено до $(git rev-parse --short HEAD)"
exit 0
X
chmod +x /usr/local/bin/rating-update
cat > /etc/systemd/system/rating-update.service <<'X'
[Unit]
Description=Рейтинг влучань — оновлення з GitHub
[Service]
Type=oneshot
ExecStart=/usr/local/bin/rating-update
X
cat > /etc/systemd/system/rating-update.timer <<'X'
[Unit]
Description=Рейтинг влучань — перевірка оновлень кожні 10 хв
[Timer]
OnBootSec=3min
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
X
systemctl daemon-reload
systemctl enable -q --now rating-live rating-update.timer
systemctl restart rating-live

say "5/5 Веб-адреса з HTTPS"
IP=$(curl -s -4 https://api.ipify.org || hostname -I | awk '{print $1}')
HOST="$(echo $IP | tr . -).sslip.io"
cat > /etc/caddy/Caddyfile <<X
$HOST {
	root * /var/www/rating
	encode gzip
	header {
		Access-Control-Allow-Origin *
		Cache-Control "no-cache"
	}
	handle /login* {
		reverse_proxy 127.0.0.1:8099
	}
	handle {
		file_server
	}
}
X
if command -v ufw >/dev/null && ufw status | grep -q active; then ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; fi
systemctl reload caddy || systemctl restart caddy

say "Вхід службового акаунта Telegram"
if [ -s /etc/rating/session ]; then
  echo "Сесія вже є (повторний вхід: rating-login)"
elif [ -n "$WEBKEY" ]; then
  systemctl stop rating-login-web 2>/dev/null || true
  systemd-run --unit=rating-login-web --collect /opt/rating/venv/bin/python -u /opt/rating/server/login_web.py "$WEBKEY"
  echo "Відкрийте в браузері: https://$HOST/login?k=$WEBKEY"
else
  rating-login
fi

sleep 8
echo
systemctl --no-pager -n 8 status rating-live | tail -8
echo -e "\n\033[1;32mГОТОВО.\033[0m Дані: https://$HOST/events.json"
echo "Журнал роботи:  journalctl -u rating-live -f     (вихід — Ctrl+C)"
