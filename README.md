# Easun Inverter Monitor

Flask UI + background Modbus poller + optional Telegram bot alerts + JK BMS battery device scanner, with Tuya smart plug integration. The poller stores readings in SQLite; the UI reads the latest entry and charts history. Battery devices (JK BMS) are continuously scanned and their status displayed on the dashboard.

## Features
- Web dashboard with real-time status and historical charts (downsampling by period).
- Electricity schedule viewer showing planned outages from be-svitlo.oe.if.ua.
- Electricity schedule viewer showing planned outages from be-svitlo.oe.if.ua.
- Battery device monitoring: JK BMS scanner service continuously monitors battery devices (voltage, current, temperature, cell balance).
- Background Modbus poller (no inverter load from the UI).
- Telegram bot: `/status`, `/battery`, and automatic alerts on grid loss/restore.
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
python modbus_service.py        # poller -> SQLite
python app.py                   # UI at 0.0.0.0:8000
python jk_bms_service.py        # battery device scanner (optional)
python telegram_bot.py          # optional bot (/start, /status, outage/restore)
```
4) Systemd (all services):
```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now easun-web.service easun-poller.service easun-jk-bms.service easun-bot.service
```

To start only specific services:
```bash
sudo systemctl enable --now easun-web.service easun-poller.service
```
Units assume `/opt/eamoon` and `/opt/eamoon/.venv/bin/python`; adjust paths if different.

## Notes
- Data is cached in `DB_PATH` (default `inverter.db`); the UI does not hit the inverter.
- Register map/model selection lives in `easunpy/models.py` (`ISOLAR_SMG_II_4K` etc.).
- Charts downsample to ~5-minute buckets to avoid clutter.
- Battery devices (JK BMS) are scanned and updated by the independent `jk_bms_service.py` service.
- Device discovery is stored in `jk_devices.yaml` and automatically updated by the service.

## Configuration (.env)
Copy `.env.example` to `.env` and fill as needed.

Core:
- `MODBUS_HOST`, `MODBUS_PORT`, `MODBUS_UNIT_ID`
- `INVERTER_MODEL` (e.g. `ISOLAR_SMG_II_4K`)
- `LOCAL_IP` (optional, helps device discovery)
- `DB_PATH` (SQLite path), `POLL_INTERVAL`

JK BMS Battery Devices (optional):
- `JK_SCAN_INTERVAL` (default: 300s) – how often to update device readings
- `JK_RESCAN_INTERVAL` (default: 3600s) – how often to discover new devices
- `DEVICES_FILE` (default: `jk_devices.yaml`) – saved device list

Telegram (optional):
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BOT_POLL_INTERVAL`

Tuya Cloud (optional):
- `TUYA_ACCESS_ID`, `TUYA_ACCESS_SECRET`, `TUYA_USER_ID`
- `TUYA_ENDPOINT` (e.g. `https://openapi.tuyaeu.com`), `TUYA_APP_SCHEMA`
- `TUYA_TURN_OFF_ON_POWER_LOSS` (true/false): turn OFF all devices on grid loss
- `TUYA_TURN_ON_ON_GRID_BACK` (true/false): turn ON all devices on grid restore

Electricity Schedule (optional):
- `QUEUE_NUMBER` (e.g. `5.2`): Your electricity queue number for be-svitlo.oe.if.ua API

Notes on Tuya:
- Devices are listed via user-mode endpoint (`/v1.0/users/{USER_ID}/devices`).
- Commands use `switch_1: True|False` for basic on/off smart plugs.
- If a device is offline, the API returns an error; the bot reports and continues.

## Battery Device Management
The JK BMS service continuously monitors Bluetooth battery devices:

**Initial Setup:**
1. Ensure JK BMS devices are powered and discoverable
2. Start the service: `python jk_bms_service.py` (or via systemd)
3. Service will auto-discover devices on first run and save to `jk_devices.yaml`
4. Readings are stored in SQLite and displayed on the web dashboard

**Device Status Display:**
- Voltage, current, temperature, cell count
- Health indicator (green/orange/red based on voltage levels)
- All cells' individual voltages available in detail view

**Service Behavior:**
- Updates all devices every `JK_SCAN_INTERVAL` seconds (default: 5 minutes)
- Rediscovers new devices every `JK_RESCAN_INTERVAL` seconds (default: 1 hour)
- Logs to `jk_bms_service.log` and systemd journal
- Runs independently from web UI (background daemon)

## Bot quick commands
- `/status` – overall status (grid, key metrics) + battery device states + Tuya device states (if configured) + today's electricity schedule
- `/battery` – battery-only focus (SOC, voltage, current) with low-SOC tips
- `/schedule` – detailed electricity schedule for queue (up to 3 days)

Alerts:
- Grid loss → alert + optional Tuya auto-off
- Grid restore → alert + optional Tuya auto-on
