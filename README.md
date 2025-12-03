# Easun Inverter Monitor

Flask UI + background Modbus poller + optional Telegram alerts for grid outage/restore. The poller stores readings in SQLite; the UI reads the latest entry and charts history.

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
