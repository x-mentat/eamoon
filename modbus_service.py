"""Background poller that reads the inverter and stores values into SQLite."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict

from dotenv import load_dotenv

from data_store import init_db, save_reading
from easunpy.async_isolar import AsyncISolar
from easunpy.models import MODEL_CONFIGS
from easunpy.utils import get_local_ip

logger = logging.getLogger("modbus_service")

# Load .env early so CLI/env overrides are available.
load_dotenv()

INVERTER_IP = os.getenv("INVERTER_IP") or os.getenv("MODBUS_HOST")
LOCAL_IP_ENV = os.getenv("LOCAL_IP")
INVERTER_MODEL = os.getenv("INVERTER_MODEL", "ISOLAR_SMG_II_11K")
DB_PATH = os.getenv("DB_PATH", "inverter.db")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))


def _resolve_local_ip() -> str:
    return LOCAL_IP_ENV or get_local_ip() or ""


def _format_value(value, divisor: float = 1.0, digits: int = 1) -> str:
    """Format values defensively, returning 'N/A' if missing."""
    if value is None:
        return "N/A"
    try:
        val = value / divisor
    except Exception:
        return "N/A"
    return f"{val:.{digits}f}"


def _as_display(battery, pv, grid, output, status) -> Dict[str, str]:
    """Convert dataclass objects into template-friendly strings."""
    voltage_val = getattr(output, "voltage", None) or getattr(grid, "voltage", None)
    freq_val = getattr(output, "frequency", None)
    if freq_val is None:
        freq_val = getattr(grid, "frequency", None)
        freq_divisor = 1.0  # grid frequency in 4K is already scaled by 0.1
    else:
        freq_divisor = 100.0

    # Best-effort grid current if not provided explicitly.
    raw_grid_current = getattr(grid, "current", None)
    if raw_grid_current is None:
        try:
            gp = getattr(grid, "power", None)
            gv = getattr(grid, "voltage", None)
            raw_grid_current = gp / gv if gp not in (None, 0) and gv not in (None, 0) else None
        except Exception:
            raw_grid_current = None

    return {
        "grid_voltage": _format_value(getattr(grid, "voltage", None), digits=1),
        "grid_power": _format_value(getattr(grid, "power", None), digits=0),
        "grid_current": _format_value(raw_grid_current, digits=2),
        "ac_output_voltage": _format_value(voltage_val, digits=1),
        "ac_output_freq": _format_value(freq_val, divisor=freq_divisor, digits=2),
        "ac_output_power": _format_value(getattr(output, "power", None), digits=0),
        "ac_output_current": _format_value(getattr(output, "current", None), digits=1),
        "battery_voltage": _format_value(getattr(battery, "voltage", None), digits=1),
        "battery_current": _format_value(getattr(battery, "current", None), digits=1),
        "battery_power": _format_value(getattr(battery, "power", None), digits=0),
        "battery_soc": _format_value(getattr(battery, "soc", None), digits=0),
        "battery_charge_power": _format_value(
            (getattr(grid, "power", None) or 0) - (getattr(output, "power", None) or 0),
            digits=0,
        ),
        "pv_input_voltage": _format_value(getattr(pv, "pv1_voltage", None), digits=1),
        "pv_input_power": _format_value(
            getattr(pv, "total_power", None)
            or getattr(pv, "pv1_power", None)
            or getattr(pv, "pv2_power", None),
            digits=0,
        ),
        "pv_input_current": _format_value(getattr(pv, "pv1_current", None), digits=1),
        "temperature": _format_value(getattr(battery, "temperature", None), digits=0),
        "status_text": getattr(status, "mode_name", "Unknown"),
    }


async def collect_once(inverter: AsyncISolar) -> None:
    battery, pv, grid, output, status, *_ = await inverter.get_all_data()

    # Console debug: show raw dataclasses and their attributes
    def _dump(label, obj):
        try:
            data = vars(obj) if obj is not None else {}
        except TypeError:
            data = {}
        print(f"{label}:", obj, data)

    _dump("battery", battery)
    _dump("pv", pv)
    _dump("grid", grid)
    _dump("output", output)
    _dump("status", status)

    payload = _as_display(battery, pv, grid, output, status)

    # щоб не зберігати повністю порожні N/A:
    if not any(
        key != "status_text" and val != "N/A"
        for key, val in payload.items()
    ):
        raise RuntimeError("No valid register data returned from inverter")

    save_reading(DB_PATH, payload, None)
    logger.info("Saved inverter reading")


async def run_forever() -> None:
    init_db(DB_PATH)

    local_ip = _resolve_local_ip()
    if not local_ip:
        raise RuntimeError("Could not determine local IP; set LOCAL_IP env var")

    if INVERTER_MODEL not in MODEL_CONFIGS:
        raise RuntimeError(f"Unsupported inverter model '{INVERTER_MODEL}'")
    if not INVERTER_IP:
        raise RuntimeError("INVERTER_IP (or MODBUS_HOST) is not set")

    inverter = AsyncISolar(INVERTER_IP, local_ip, model=INVERTER_MODEL)

    logger.info(
        "Starting poller for %s (model=%s) -> %s every %ss",
        INVERTER_IP,
        INVERTER_MODEL,
        DB_PATH,
        POLL_INTERVAL,
    )
    while True:
        try:
            await collect_once(inverter)
        except Exception as exc:  # noqa: BLE001
            logger.error("Collect failed: %s", exc)
            save_reading(DB_PATH, None, str(exc))
        await asyncio.sleep(POLL_INTERVAL)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        asyncio.run(run_forever())
        return 0
    except KeyboardInterrupt:
        logger.info("Poller stopped by user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
