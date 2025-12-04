"""
Minimal Flask app that serves the latest inverter reading from SQLite.
Run modbus_service.py separately to collect data via AsyncISolar.
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from data_store import get_latest_reading, init_db
from data_store import get_recent_readings, get_readings_since

app = Flask(__name__)

# Load .env if present so users can configure without touching code.
load_dotenv()

DB_PATH = os.getenv("DB_PATH", "inverter.db")
INVERTER_IP = os.getenv("INVERTER_IP") or os.getenv("MODBUS_HOST", "192.168.1.100")
LOCAL_IP = os.getenv("LOCAL_IP", "")
INVERTER_MODEL = os.getenv("INVERTER_MODEL", "ISOLAR_SMG_II_11K")

# Ensure the DB exists so the UI can show helpful messaging even before data arrives.
init_db(DB_PATH)


@app.route("/")
def home():
    data, error, updated_at = get_latest_reading(DB_PATH)
    status_error: Optional[str] = error
    if not data and not error:
        status_error = "No data yet; start modbus_service.py to collect readings."
    return render_template(
        "status.html",
        data=data,
        error=status_error,
        host=INVERTER_IP,
        local_ip=LOCAL_IP,
        model=INVERTER_MODEL,
        updated_at=updated_at,
    )


@app.route("/history")
def history():
    """Return recent readings for charts."""
    days_param = request.args.get("days")
    if days_param:
        try:
            days = float(days_param)
            readings = get_readings_since(DB_PATH, days=days)
        except (ValueError, TypeError):
            readings = get_recent_readings(DB_PATH, limit=200)
    else:
        readings = get_recent_readings(DB_PATH, limit=200)
    return jsonify(readings)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
