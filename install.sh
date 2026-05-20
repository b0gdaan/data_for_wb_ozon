#!/bin/bash
# WB + Ozon Analytics Dashboard — Ubuntu 22.04 setup
# Run as root: bash install.sh
set -e

APP_DIR="/opt/analytics"
APP_USER="analytics"
DB_NAME="analytics_db"
DB_USER="analytics_user"
DB_PASS="$(tr -dc A-Za-z0-9 </dev/urandom | head -c 16)"
SERVICE="analytics-api"

echo "=== WB + Ozon Analytics Setup ==="

# ── 1. System packages ───────────────────────────────────────────────────────
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv postgresql nginx

# ── 2. User and directory ────────────────────────────────────────────────────
useradd -m -s /bin/bash "$APP_USER" 2>/dev/null || true
mkdir -p "$APP_DIR"
cp -r ./* "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ── 3. PostgreSQL ─────────────────────────────────────────────────────────────
echo "--- Setting up PostgreSQL..."
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || true
sudo -u postgres psql -d "$DB_NAME" -f "$APP_DIR/schema.sql"
sudo -u postgres psql -d "$DB_NAME" -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO $DB_USER;"
sudo -u postgres psql -d "$DB_NAME" -c "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO $DB_USER;"
echo "--- Database created: $DB_NAME"

# ── 4. Python venv + deps ─────────────────────────────────────────────────────
echo "--- Installing Python packages..."
cd "$APP_DIR"
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

# ── 5. .env file ──────────────────────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    sed -i "s|analytics_user:CHANGE_ME|$DB_USER:$DB_PASS|g" "$APP_DIR/.env"
    echo ""
    echo "!!! Fill in your API keys before starting the service !!!"
    echo "    nano $APP_DIR/.env"
    echo ""
fi
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

# ── 6. Systemd service ────────────────────────────────────────────────────────
echo "--- Registering service..."
cp "$APP_DIR/systemd/$SERVICE.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl start "$SERVICE"
echo "--- Service started"

# ── 7. Nginx ──────────────────────────────────────────────────────────────────
echo "--- Configuring Nginx..."
cp "$APP_DIR/nginx/site.conf" /etc/nginx/sites-available/analytics
ln -sf /etc/nginx/sites-available/analytics /etc/nginx/sites-enabled/analytics
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
echo "--- Nginx configured"

# ── 8. Cron jobs ──────────────────────────────────────────────────────────────
echo "--- Setting up cron..."
(crontab -u "$APP_USER" -l 2>/dev/null; echo "0 6 * * * cd $APP_DIR && venv/bin/python run_etl.py >> /var/log/analytics_etl.log 2>&1") | crontab -u "$APP_USER" -
(crontab -u "$APP_USER" -l 2>/dev/null; echo "0 9 * * * cd $APP_DIR && venv/bin/python tg_notify.py >> /var/log/analytics_tg.log 2>&1") | crontab -u "$APP_USER" -
echo "--- Cron set (ETL: 06:00, Telegram alerts: 09:00)"

# ── Done ──────────────────────────────────────────────────────────────────────
IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         INSTALLATION COMPLETE!                       ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Dashboard: http://$IP"
echo "║"
echo "║  1. Fill in API keys:  nano $APP_DIR/.env"
echo "║  2. First data import: "
echo "║     sudo -u $APP_USER $APP_DIR/venv/bin/python $APP_DIR/run_etl.py --days 30"
echo "╚══════════════════════════════════════════════════════╝"
