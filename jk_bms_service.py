"""Background poller that reads JK BMS via Bluetooth and updates battery data in SQLite."""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import yaml
from typing import Dict, Optional
from pathlib import Path
from dotenv import load_dotenv
from bleak import BleakClient

from data_store import get_latest_reading, save_reading

logger = logging.getLogger("jk_bms_service")

load_dotenv()

JK_BMS_ADDRESS = os.getenv("JK_BMS_ADDRESS", "")
JK_DEVICES_FILE = os.getenv("JK_DEVICES_FILE", "jk_bms/jk_devices.yaml")
DB_PATH = os.getenv("DB_PATH", "inverter.db")
POLL_INTERVAL = int(os.getenv("JK_BMS_POLL_INTERVAL", "30"))  # Poll every 30 seconds
MAX_CONSECUTIVE_ERRORS = int(os.getenv("MAX_CONSECUTIVE_ERRORS", "5"))

# JK BMS Bluetooth UUIDs
SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# JK BMS Protocol Commands
CMD_CELL_DATA = bytes([
    0xAA, 0x55, 0x90, 0xEB,  # Header
    0x96,                     # Command code 0x96 = getCellData
    0x00,                     # Length
    0x00, 0x00, 0x00, 0x00,  # Padding
    0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00,
    0x00, 0x10                # CRC
])


def load_device_address() -> Optional[str]:
    """Load JK BMS device address from YAML file or environment."""
    if JK_BMS_ADDRESS:
        return JK_BMS_ADDRESS
    
    try:
        if Path(JK_DEVICES_FILE).exists():
            with open(JK_DEVICES_FILE, 'r') as f:
                devices = yaml.safe_load(f) or {}
                if devices:
                    # Get first device
                    return list(devices.keys())[0]
    except Exception as e:
        logger.error(f"Failed to load device address from YAML: {e}")
    
    return None


def parse_jk_bms_data(data: bytes) -> Optional[Dict[str, float]]:
    """
    Parse JK BMS data packet and extract battery information.
    Returns dict with battery metrics or None on error.
    """
    try:
        if len(data) < 150:
            logger.warning(f"Data too short: {len(data)} bytes")
            return None
        
        # Check for valid JK BMS packet header
        if data[0:2] != b'\x55\xAA' or data[2:4] != b'\xEB\x90':
            logger.warning("Invalid packet header")
            return None
        
        record_type = data[4]
        
        # We want record type 0x02 (cell info with real-time telemetry)
        if record_type != 0x02:
            logger.debug(f"Skipping record type 0x{record_type:02X}")
            return None
        
        result = {}
        
        # Parse cell voltages (16 cells max, 2 bytes each, starting at offset 6)
        cells = []
        for i in range(16):
            offset = 6 + (i * 2)
            if offset + 2 <= len(data):
                cell_v_raw = int.from_bytes(data[offset:offset+2], byteorder='little')
                cell_v = cell_v_raw / 1000.0
                
                # Valid cell voltage check (2.0 - 4.5V)
                if 2.0 < cell_v < 4.5:
                    cells.append(cell_v)
                elif cell_v_raw == 0:
                    break
        
        if cells:
            result['cell_count'] = len(cells)
            result['cell_voltage_min'] = min(cells)
            result['cell_voltage_max'] = max(cells)
            result['cell_voltage_avg'] = sum(cells) / len(cells)
            result['cell_voltage_delta'] = max(cells) - min(cells)
        
        # Total voltage (32-bit at offset 150, in mV)
        if len(data) > 154:
            total_v_raw = int.from_bytes(data[150:154], byteorder='little')
            result['battery_voltage'] = total_v_raw / 1000.0
        elif cells:
            # Fallback to calculated
            result['battery_voltage'] = sum(cells)
        
        # Current (32-bit signed at offset 108, in mA)
        if len(data) > 112:
            current_raw = int.from_bytes(data[108:112], byteorder='little', signed=True)
            result['battery_current'] = current_raw / 1000.0
        
        # Temperature (16-bit signed at offset 114, in 0.1°C)
        if len(data) > 116:
            temp_raw = int.from_bytes(data[114:116], byteorder='little', signed=True)
            result['temperature'] = temp_raw / 10.0
        
        # Calculate SOC if we have voltage (rough estimate for LiFePO4)
        if 'battery_voltage' in result and 'cell_count' in result:
            cell_count = result['cell_count']
            voltage = result['battery_voltage']
            avg_cell_v = voltage / cell_count
            
            # LiFePO4 SOC estimation (very rough)
            # 3.65V = 100%, 3.30V = 50%, 2.50V = 0%
            if avg_cell_v >= 3.65:
                soc = 100
            elif avg_cell_v <= 2.50:
                soc = 0
            else:
                # Linear interpolation
                soc = ((avg_cell_v - 2.50) / (3.65 - 2.50)) * 100
            
            result['battery_soc'] = max(0, min(100, soc))
        
        # Calculate power
        if 'battery_voltage' in result and 'battery_current' in result:
            result['battery_power'] = result['battery_voltage'] * result['battery_current']
        
        return result
        
    except Exception as e:
        logger.error(f"Error parsing BMS data: {e}")
        return None


async def read_jk_bms(address: str) -> Optional[Dict[str, float]]:
    """Connect to JK BMS and read battery data."""
    received_data = bytearray()
    
    def notification_handler(sender, data):
        nonlocal received_data
        received_data.extend(data)
    
    try:
        async with BleakClient(address, timeout=15.0) as client:
            logger.debug(f"Connected to JK BMS at {address}")
            
            # Enable notifications
            await client.start_notify(CHARACTERISTIC_UUID, notification_handler)
            await asyncio.sleep(0.5)
            
            # Request cell data
            await client.write_gatt_char(CHARACTERISTIC_UUID, CMD_CELL_DATA, response=False)
            await asyncio.sleep(2.0)
            
            # Stop notifications
            await client.stop_notify(CHARACTERISTIC_UUID)
            
            # Parse received data
            if received_data and len(received_data) > 100:
                # Find record type 0x02 in the data
                pos = 0
                while pos < len(received_data) - 150:
                    if (received_data[pos:pos+2] == b'\x55\xAA' and 
                        received_data[pos+2:pos+4] == b'\xEB\x90'):
                        
                        record_type = received_data[pos+4]
                        if record_type == 0x02:
                            # Found cell info record
                            end_pos = min(pos + 300, len(received_data))
                            return parse_jk_bms_data(bytes(received_data[pos:end_pos]))
                    pos += 1
            
            logger.warning("No valid data received from BMS")
            return None
            
    except Exception as e:
        logger.error(f"Failed to read JK BMS: {e}")
        return None


async def update_battery_data_in_db(bms_data: Dict[str, float]) -> None:
    """Update battery data in the database while preserving inverter data."""
    try:
        # Get current reading from database
        current_data, error, timestamp = get_latest_reading(DB_PATH)
        
        if current_data is None:
            current_data = {}
        
        # Update battery fields from BMS
        if 'battery_voltage' in bms_data:
            current_data['battery_voltage'] = f"{bms_data['battery_voltage']:.1f}"
        
        if 'battery_current' in bms_data:
            current_data['battery_current'] = f"{bms_data['battery_current']:.1f}"
        
        if 'battery_soc' in bms_data:
            current_data['battery_soc'] = f"{bms_data['battery_soc']:.0f}"
        
        if 'battery_power' in bms_data:
            current_data['battery_power'] = f"{bms_data['battery_power']:.0f}"
        
        if 'temperature' in bms_data:
            current_data['temperature'] = f"{bms_data['temperature']:.0f}"
        
        # Add BMS-specific metadata
        current_data['bms_cell_count'] = bms_data.get('cell_count', 0)
        current_data['bms_cell_min_v'] = f"{bms_data.get('cell_voltage_min', 0):.3f}"
        current_data['bms_cell_max_v'] = f"{bms_data.get('cell_voltage_max', 0):.3f}"
        current_data['bms_cell_delta_v'] = f"{bms_data.get('cell_voltage_delta', 0):.3f}"
        
        # Save updated data
        save_reading(DB_PATH, current_data, None)
        logger.info(
            "Battery updated: %.1fV, %.1fA, %.0f%%, %.1f°C, %d cells (delta: %.3fV)",
            bms_data.get('battery_voltage', 0),
            bms_data.get('battery_current', 0),
            bms_data.get('battery_soc', 0),
            bms_data.get('temperature', 0),
            bms_data.get('cell_count', 0),
            bms_data.get('cell_voltage_delta', 0)
        )
        
    except Exception as e:
        logger.error(f"Failed to update database: {e}")


async def run_forever() -> None:
    """Main polling loop."""
    address = load_device_address()
    
    if not address:
        logger.error("No JK BMS address configured!")
        logger.error("Set JK_BMS_ADDRESS in .env or run jk-util.py --scan to discover devices")
        return
    
    logger.info(
        "Starting JK BMS poller for %s -> %s every %ss (max errors: %s)",
        address,
        DB_PATH,
        POLL_INTERVAL,
        MAX_CONSECUTIVE_ERRORS,
    )
    
    failure_count = 0
    
    while True:
        try:
            bms_data = await read_jk_bms(address)
            
            if bms_data:
                await update_battery_data_in_db(bms_data)
                failure_count = 0
            else:
                failure_count += 1
                logger.warning(f"No data from BMS ({failure_count}/{MAX_CONSECUTIVE_ERRORS})")
                
                if failure_count >= MAX_CONSECUTIVE_ERRORS:
                    logger.critical("Too many consecutive errors. Exiting.")
                    raise RuntimeError("Max consecutive errors reached")
            
        except Exception as exc:
            failure_count += 1
            logger.error(f"Collect failed ({failure_count}/{MAX_CONSECUTIVE_ERRORS}): {exc}")
            
            if failure_count >= MAX_CONSECUTIVE_ERRORS:
                logger.critical("Too many consecutive errors. Exiting to allow service restart.")
                await asyncio.sleep(1)
                raise
        
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
        logger.info("JK BMS poller stopped by user")
        return 0
    except Exception as exc:
        logger.critical(f"JK BMS poller crashed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
