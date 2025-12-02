# Easun Inverter Monitor

Flask UI + background Modbus poller + optional Telegram alerts for grid outage/restore. The poller stores readings in SQLite; the UI reads the latest entry and charts history.

## Setup
1) Install deps (use venv if you like):
```bash
pip install -r requirements.txt
```
2) Copy and edit env:
```bash
cp .env.example .env  # or copy manually on Windows
```
Fill in at least:
- `INVERTER_IP` (or `MODBUS_HOST`), `INVERTER_MODEL`
- `LOCAL_IP` (if auto-detect fails), `DB_PATH`
- `POLL_INTERVAL`
- Telegram (optional): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BOT_POLL_INTERVAL`

## Run
Poller (Modbus -> SQLite):
```bash
python modbus_service.py
```
Web UI (reads DB only):
```bash
python app.py
# open http://localhost:8000/
```
Telegram alerts (/start, /status, outage/restore):
```bash
python telegram_bot.py
```

## Notes
- Data is cached in `DB_PATH` (default `inverter.db`); the UI does not hit the inverter.
- Register map/model selection lives in `easunpy/models.py` (`ISOLAR_SMG_II_4K` etc.).
- Charts downsample to ~5-minute buckets to avoid clutter.
