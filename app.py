"""Flask web server for inverter status dashboard with real-time charts."""
from __future__ import annotations

import os
import urllib.request
import json
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from data_store import (
    get_latest_reading,
    get_readings_since,
    get_recent_readings,
    init_db,
)

app = Flask(__name__)

# Load .env if present so users can configure without touching code.
load_dotenv()

DB_PATH = os.getenv("DB_PATH", "inverter.db")
INVERTER_IP = os.getenv("INVERTER_IP") or os.getenv("MODBUS_HOST", "192.168.1.100")
LOCAL_IP = os.getenv("LOCAL_IP", "")
INVERTER_MODEL = os.getenv("INVERTER_MODEL", "ISOLAR_SMG_II_11K")
QUEUE_NUMBER = os.getenv("QUEUE_NUMBER", "5.2")

# Ensure the DB exists so the UI can show helpful messaging even before data arrives.
init_db(DB_PATH)


@app.route("/")
def home():
    """Serve the main status dashboard."""
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


@app.route("/tuya_devices")
def tuya_devices():
    """Return Tuya device statuses."""
    try:
        import tuya
        token = tuya.get_token()
        if not token:
            return jsonify({"error": "Failed to get Tuya token"})
        devices = tuya.list_devices(token)
        result = []
        for dev in devices:
            dev_id = dev.get("id")
            name = dev.get("name", dev_id)
            if not dev_id:
                continue
            try:
                status = tuya.get_device_status(token, dev_id)
                status_items = status if isinstance(status, list) else status.get("status", [])
                switch_on = False
                for item in status_items:
                    if item.get("code") == "switch_1":
                        switch_on = item.get("value", False)
                        break
                result.append({"id": dev_id, "name": name, "online": True, "switch_on": switch_on})
            except Exception:
                result.append({"id": dev_id, "name": name, "online": False, "switch_on": False})
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "Tuya not available"})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/electricity_schedule")
def electricity_schedule():
    """Fetch electricity schedule from be-svitlo API."""
    try:
        url = f"https://be-svitlo.oe.if.ua/schedule-by-queue?queue={QUEUE_NUMBER}"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        req.add_header('Accept', 'application/json, text/plain, */*')
        req.add_header('Accept-Language', 'en-US,en;q=0.9,uk;q=0.8')
        req.add_header('Referer', 'https://be-svitlo.oe.if.ua/')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            return jsonify({
                "success": True,
                "queue": QUEUE_NUMBER,
                "data": data
            })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "queue": QUEUE_NUMBER
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
