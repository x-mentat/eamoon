"""Device management service for scanning and updating battery device data."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List

import yaml
from bleak import BleakScanner, BleakClient


class DeviceService:
    """Service for managing battery devices (JK BMS, etc.) and storing readings in database."""

    def __init__(self, db_path: str | Path, devices_file: str = "jk_devices.yaml"):
        """
        Initialize the device service.

        Args:
            db_path: Path to SQLite database
            devices_file: Path to YAML file containing device list
        """
        self.db_path = Path(db_path)
        self.devices_file = devices_file

    async def scan_for_devices(self, scan_time: float = 10.0) -> list[dict]:
        """
        Scan for JK BMS devices via Bluetooth.

        Args:
            scan_time: Duration to scan in seconds

        Returns:
            List of discovered device info dicts
        """
        print(f"Scanning for JK BMS devices for {scan_time} seconds...")
        devices = await BleakScanner.discover(timeout=scan_time)

        discovered = []
        for device in devices:
            device_name = device.name or "Unknown"
            if any(
                keyword in device_name.upper()
                for keyword in ["JK", "JK-BMS", "JIKONG"]
            ):
                device_info = {
                    "name": device_name,
                    "address": device.address,
                    "last_seen": datetime.now().isoformat(),
                }
                discovered.append(device_info)
                print(f"✓ Found: {device_name} ({device.address})")

        return discovered

    def save_devices(self, devices: list[dict]) -> None:
        """Save discovered devices to YAML file."""
        try:
            existing = self._load_devices_yaml()
            for device in devices:
                existing[device["address"]] = device
            
            with open(self.devices_file, "w") as f:
                yaml.dump(existing, f, default_flow_style=False)
            print(f"✓ Saved {len(devices)} devices to {self.devices_file}")
        except Exception as e:
            print(f"Error saving devices: {e}")

    def load_devices(self) -> list[dict]:
        """Load devices from YAML file."""
        try:
            devices_dict = self._load_devices_yaml()
            return list(devices_dict.values())
        except Exception as e:
            print(f"Error loading devices: {e}")
            return []

    def _load_devices_yaml(self) -> dict:
        """Load devices YAML as dictionary keyed by address."""
        try:
            if Path(self.devices_file).exists():
                with open(self.devices_file, "r") as f:
                    return yaml.safe_load(f) or {}
            return {}
        except Exception as e:
            print(f"Error reading YAML: {e}")
            return {}

    async def read_device_data(self, address: str) -> Dict[str, Any] | None:
        """
        Read battery data from a JK BMS device.

        Args:
            address: Bluetooth address of device

        Returns:
            Dictionary with battery data or None if failed
        """
        SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
        CHARACTERISTIC_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

        CMD_CELL_DATA = bytes(
            [
                0xAA, 0x55, 0x90, 0xEB,
                0x96,
                0x00,
                0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00,
                0x00, 0x10,
            ]
        )

        CMD_INFO = bytes(
            [
                0xAA, 0x55, 0x90, 0xEB,
                0x97,
                0x00,
                0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00,
                0x00, 0x11,
            ]
        )

        received_data = bytearray()

        def notification_handler(sender, data):
            nonlocal received_data
            received_data.extend(data)

        try:
            async with BleakClient(address, timeout=20.0) as client:
                print(f"Connected to {address}")

                try:
                    mtu = await client.get_mtu()
                    new_mtu = await client.set_mtu(512)
                    print(f"MTU negotiated: {new_mtu} bytes")
                except Exception:
                    pass

                await client.start_notify(CHARACTERISTIC_UUID, notification_handler)
                await asyncio.sleep(0.5)

                print("Sending getInfo command...")
                await client.write_gatt_char(CHARACTERISTIC_UUID, CMD_INFO, response=False)
                await asyncio.sleep(2.0)

                print("Sending getCellData command...")
                await client.write_gatt_char(CHARACTERISTIC_UUID, CMD_CELL_DATA, response=False)
                await asyncio.sleep(2.5)

                await client.stop_notify(CHARACTERISTIC_UUID)

                if len(received_data) > 100:
                    return self._parse_battery_data(bytes(received_data))

        except Exception as e:
            print(f"Error reading device {address}: {e}")

        return None

    def _parse_battery_data(self, data: bytes) -> Dict[str, Any] | None:
        """Parse JK BMS battery data packet."""
        try:
            if len(data) < 6 or data[0:2] != b'\x55\xAA':
                return None

            result = {
                "timestamp": datetime.now().isoformat(),
                "cells": [],
                "voltage": None,
                "current": None,
                "temperature": None,
                "soc": None,
            }

            # Find record type 0x02 (cell data)
            pos = 0
            while pos < len(data) - 10:
                if (
                    pos + 6 <= len(data)
                    and data[pos:pos+2] == b'\x55\xAA'
                    and data[pos+2:pos+4] == b'\xEB\x90'
                ):
                    record_type = data[pos + 4]

                    if record_type == 0x02:
                        # Parse cell voltages (2 bytes each)
                        for i in range(16):
                            offset = pos + 6 + (i * 2)
                            if offset + 2 <= len(data):
                                cell_v_raw = int.from_bytes(
                                    data[offset : offset + 2],
                                    byteorder="little",
                                )
                                cell_v = cell_v_raw / 1000.0
                                if 2.0 < cell_v < 4.5:
                                    result["cells"].append(
                                        {"cell": i + 1, "voltage": cell_v}
                                    )
                                elif cell_v_raw == 0:
                                    break

                        # Total voltage at offset 150
                        if pos + 154 <= len(data):
                            total_v_raw = int.from_bytes(
                                data[pos + 150 : pos + 154],
                                byteorder="little",
                            )
                            result["voltage"] = total_v_raw / 1000.0

                        # Current at offset 108
                        if pos + 112 <= len(data):
                            try:
                                current_raw = int.from_bytes(
                                    data[pos + 108 : pos + 112],
                                    byteorder="little",
                                    signed=True,
                                )
                                result["current"] = current_raw / 1000.0
                            except Exception:
                                pass

                        # Temperature at offset 114
                        if pos + 116 <= len(data):
                            try:
                                temp_raw = int.from_bytes(
                                    data[pos + 114 : pos + 116],
                                    byteorder="little",
                                    signed=True,
                                )
                                result["temperature"] = temp_raw / 10.0
                            except Exception:
                                pass

                        return result

                pos += 1

            return result if result["cells"] else None

        except Exception as e:
            print(f"Error parsing battery data: {e}")
            return None

    def save_device_reading(
        self, device_address: str, data: Dict[str, Any]
    ) -> None:
        """
        Save device reading to database.

        Args:
            device_address: Bluetooth address of device
            data: Battery data dictionary
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO device_readings
                    (device_address, created_at, voltage, current, temperature, 
                     cell_count, cell_data, soc)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_address,
                        data.get("timestamp"),
                        data.get("voltage"),
                        data.get("current"),
                        data.get("temperature"),
                        len(data.get("cells", [])),
                        json.dumps(data.get("cells", [])),
                        data.get("soc"),
                    ),
                )
                conn.commit()
        except sqlite3.OperationalError:
            # Table might not exist yet
            pass
        except Exception as e:
            print(f"Error saving reading: {e}")

    def get_device_status(self, device_address: str) -> Dict[str, Any] | None:
        """Get latest reading for a device."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    """
                    SELECT voltage, current, temperature, cell_count, cell_data, soc
                    FROM device_readings
                    WHERE device_address = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (device_address,),
                ).fetchone()

            if row:
                return {
                    "voltage": row[0],
                    "current": row[1],
                    "temperature": row[2],
                    "cell_count": row[3],
                    "cells": json.loads(row[4]) if row[4] else [],
                    "soc": row[5],
                }
        except Exception as e:
            print(f"Error getting device status: {e}")

        return None

    async def update_all_devices(self) -> None:
        """Scan and update all configured devices."""
        devices = self.load_devices()
        if not devices:
            print("No configured devices found")
            return

        print(f"Updating {len(devices)} devices...")
        for device in devices:
            address = device.get("address")
            if address:
                print(f"\nUpdating {device.get('name', address)}...")
                data = await self.read_device_data(address)
                if data:
                    self.save_device_reading(address, data)
                    print(f"✓ Saved: {data.get('voltage')}V, {data.get('current')}A")
                await asyncio.sleep(1)
