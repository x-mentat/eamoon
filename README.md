# Easun Inverter Monitor

Flask UI + background Modbus poller + optional Telegram bot alerts, with Tuya smart plug integration. Supports both SQLite (default) and MySQL for data storage.

## Features
- Web dashboard with real-time status and historical charts (downsampling by period).
- Background Modbus poller (no inverter load from the UI).
- Telegram bot: `/status`, `/battery`, and automatic alerts on grid loss/restore.
- **Database options**: SQLite (default) or MySQL for better concurrent access.
- Tuya Cloud integration (user account mode):
	- Show device ON/OFF state in `/status` bot message.
	- Optional auto-turn-off all Tuya devices when grid power is lost.
	- Optional auto-turn-on all Tuya devices when grid returns.
	- One-token-per-action (token is fetched on demand).

## Install: Docker
1) Copy `.env.example` to `.env` and fill in IP/model/Telegram if used.
2) Build/run three services (web, poller, bot):
```bash
docker compose up --build
```
Web UI: http://localhost:8000/ (reads SQLite from poller). Bot is optional; set TELEGRAM_* or scale it down.

## Install: Raw system
1) Create user and layout:
```bash
sudo useradd -r -s /usr/sbin/nologin eamoon
sudo mkdir -p /opt/eamoon && sudo chown -R eamoon:eamoon /opt/eamoon
# copy repo into /opt/eamoon
```
2) Python venv + deps:
```bash
cd /opt/eamoon
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit .env
```
3) Run services manually:
```bash
source .venv/bin/activate
python modbus_service.py      # poller -> SQLite
python app.py                 # UI at 0.0.0.0:8000
python telegram_bot.py        # optional bot (/start, /status, outage/restore)
```
4) Systemd:
```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now easun-web.service easun-poller.service easun-bot.service
```
Units assume `/opt/eamoon` and `/opt/eamoon/.venv/bin/python`; adjust paths if different.

## Notes
- Data is cached in `DB_PATH` (default `inverter.db`); the UI does not hit the inverter.
- Register map/model selection lives in `easunpy/models.py` (`ISOLAR_SMG_II_4K` etc.).
- Charts downsample to ~5-minute buckets to avoid clutter.

## Configuration (.env)
Copy `.env.example` to `.env` and fill as needed.

Core:
- `MODBUS_HOST`, `MODBUS_PORT`, `MODBUS_UNIT_ID`
- `INVERTER_MODEL` (e.g. `ISOLAR_SMG_II_4K`)
- `LOCAL_IP` (optional, helps device discovery)
- `DB_PATH` (SQLite path), `POLL_INTERVAL`

Telegram (optional):
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BOT_POLL_INTERVAL`

Tuya Cloud (optional):
- `TUYA_ACCESS_ID`, `TUYA_ACCESS_SECRET`, `TUYA_USER_ID`
- `TUYA_ENDPOINT` (e.g. `https://openapi.tuyaeu.com`), `TUYA_APP_SCHEMA`
- `TUYA_TURN_OFF_ON_POWER_LOSS` (true/false): turn OFF all devices on grid loss
- `TUYA_TURN_ON_ON_GRID_BACK` (true/false): turn ON all devices on grid restore

Database configuration (optional):
- `DB_TYPE` (default: `sqlite`) – set to `mysql` to use MySQL instead of SQLite
- `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`

See [DATABASE.md](DATABASE.md) for MySQL setup and migration instructions.

Notes on Tuya:
- Devices are listed via user-mode endpoint (`/v1.0/users/{USER_ID}/devices`).
- Commands use `switch_1: True|False` for basic on/off smart plugs.
- If a device is offline, the API returns an error; the bot reports and continues.

## Database Migration

To migrate from SQLite to MySQL:
```bash
# Install MySQL support
pip install -r requirements.txt

# Run migration script
python migrate_to_mysql.py --mysql-password your_password

# Update .env and restart
echo "DB_TYPE=mysql" >> .env
sudo systemctl restart easun-web easun-poller easun-bot
```

Full documentation: [DATABASE.md](DATABASE.md)

## Bot quick commands
- `/status` – overall status (grid, key metrics) + Tuya device states (if configured)
- `/battery` – battery-only focus (SOC, voltage, current) with low-SOC tips

Alerts:
- Grid loss → alert + optional Tuya auto-off
- Grid restore → alert + optional Tuya auto-on
