#!/usr/bin/env python3
"""
JK BMS Bluetooth Scanner and Reader
Scans for JK BMS devices via Bluetooth Low Energy (BLE) and reads battery data
"""

import asyncio
import struct
import yaml
import argparse
from pathlib import Path
from datetime import datetime
from bleak import BleakScanner, BleakClient

DEVICES_FILE = "jk_devices.yaml"


async def scan_for_jk_bms(scan_time=10.0):
    """
    Scan for JK BMS devices
    
    Args:
        scan_time: Duration to scan in seconds (default: 10)
    """
    print(f"Scanning for JK BMS devices for {scan_time} seconds...")
    print("-" * 60)
    
    devices = await BleakScanner.discover(timeout=scan_time)
    
    jk_devices = []
    
    for device in devices:
        # JK BMS devices typically have "JK" in their name or specific service UUIDs
        device_name = device.name or "Unknown"
        
        # Check if device name contains JK-BMS identifiers
        if any(keyword in device_name.upper() for keyword in ["JK", "JK-BMS", "JIKONG"]):
            jk_devices.append(device)
            print(f"✓ Found JK BMS Device:")
            print(f"  Name: {device_name}")
            print(f"  Address: {device.address}")
            if hasattr(device, 'rssi') and device.rssi is not None:
                print(f"  RSSI: {device.rssi} dBm")
            if hasattr(device, 'details') and device.details:
                print(f"  Details: {device.details}")
            print("-" * 60)
    
    if not jk_devices:
        print("\nNo JK BMS devices found.")
        print("\nAll discovered devices:")
        for device in devices:
            print(f"  {device.name or 'Unknown'} - {device.address}")
    else:
        print(f"\nTotal JK BMS devices found: {len(jk_devices)}")
        save_devices_to_yaml(jk_devices)
    
    return jk_devices


def save_devices_to_yaml(devices):
    """Save discovered JK BMS devices to YAML file"""
    try:
        # Load existing data if file exists
        existing_data = load_devices_from_yaml()
        
        # Update with new devices
        for device in devices:
            device_info = {
                'name': device.name,
                'address': device.address,
                'last_seen': datetime.now().isoformat()
            }
            
            # Update or add device
            existing_data[device.address] = device_info
        
        # Save to file
        with open(DEVICES_FILE, 'w') as f:
            yaml.dump(existing_data, f, default_flow_style=False)
        
        print(f"\n✓ Saved devices to {DEVICES_FILE}")
        
    except Exception as e:
        print(f"Warning: Could not save devices to YAML: {e}")


def load_devices_from_yaml():
    """Load known JK BMS devices from YAML file"""
    try:
        if Path(DEVICES_FILE).exists():
            with open(DEVICES_FILE, 'r') as f:
                return yaml.safe_load(f) or {}
        return {}
    except Exception as e:
        print(f"Warning: Could not load devices from YAML: {e}")
        return {}


async def read_jk_bms_data(address):
    """
    Connect to JK BMS and read battery data using notifications (default method)
    
    Args:
        address: Bluetooth address of the JK BMS device
    """
    await read_jk_bms_notifications(address)


async def read_jk_bms_notifications(address):
    """
    Read JK BMS data using notifications - based on mpp-solar jkbleio implementation
    
    KEY FIXES (addressing firmware behavior):
    1. Enable notifications BEFORE sending commands (critical: BMS only pushes 0x02 if listener is registered first)
    2. Negotiate MTU size to ensure cell-data frames (~300+ bytes) aren't truncated
    3. Log raw command bytes to validate frame structure against JK spec
    """
    SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
    CHARACTERISTIC_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
    
    # JK BMS Protocol Commands - from mpp-solar jkabstractprotocol.py
    # Command format: aa 55 90 eb [command_code] [length] ... [crc]
    # Full command is 20 bytes
    
    # getCellData command (0x96, record type 0x02)
    CMD_CELL_DATA = bytes([
        0xAA, 0x55, 0x90, 0xEB,  # Header
        0x96,                     # Command code 0x96 = getCellData
        0x00,                     # Length
        0x00, 0x00, 0x00, 0x00,  # Padding
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x10                # CRC
    ])
    
    # getInfo command (0x97, record type 0x03)
    CMD_INFO = bytes([
        0xAA, 0x55, 0x90, 0xEB,  # Header  
        0x97,                     # Command code 0x97 = getInfo
        0x00,                     # Length
        0x00, 0x00, 0x00, 0x00,  # Padding
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x11                # CRC (from mpp-solar)
    ])
    
    received_data = bytearray()
    
    def notification_handler(sender, data):
        nonlocal received_data
        received_data.extend(data)
        print(f"  → Notification: {len(data)} bytes")
    
    print(f"\nConnecting to JK BMS at {address}...")
    
    try:
        async with BleakClient(address, timeout=20.0) as client:
            print(f"Connected to {address}")
            
            # Try to negotiate higher MTU (default 23 bytes may truncate cell-data frames)
            # Cell data frames are ~300+ bytes, so we need MTU >= 512 if possible
            try:
                print("\nNegotiating MTU size...")
                mtu = await client.get_mtu()
                print(f"  Current MTU: {mtu} bytes")
                
                # Try to set MTU to 512 (if BMS supports it)
                new_mtu = await client.set_mtu(512)
                print(f"  Negotiated MTU: {new_mtu} bytes")
            except Exception as e:
                print(f"  MTU negotiation skipped: {e}")
            
            # Check available characteristics
            for service in client.services:
                if "ffe0" in service.uuid.lower():
                    print(f"\nService: {service.uuid}")
                    for char in service.characteristics:
                        print(f"  Characteristic: {char.uuid}")
                        print(f"    Properties: {char.properties}")
            
            # CRITICAL FIX: Enable notifications FIRST before sending any commands
            # Some JK firmware versions only push record 0x02 (cell data) if the 
            # notification listener is already registered when the command is sent.
            print(f"\nEnabling notifications on characteristic {CHARACTERISTIC_UUID}...")
            await client.start_notify(CHARACTERISTIC_UUID, notification_handler)
            print("Notifications enabled - BMS will now send 0x02 frames on command")
            
            # Allow a brief settle time after enabling notifications
            await asyncio.sleep(0.5)
            
            # Log command frames for validation
            print("\nCommand frames (for protocol validation):")
            print(f"  CMD_INFO (0x97):      {CMD_INFO.hex().upper()}")
            print(f"  CMD_CELL_DATA (0x96): {CMD_CELL_DATA.hex().upper()}")
            
            # Sequence: info handshake first, then cell data
            print("\n[1/5] Sending getInfo command (0x97) - device info handshake...")
            await client.write_gatt_char(CHARACTERISTIC_UUID, CMD_INFO, response=False)
            await asyncio.sleep(2.0)
            
            # Now request cell data multiple times
            print("[2/5] Sending getCellData command (0x96) - attempt 1...")
            await client.write_gatt_char(CHARACTERISTIC_UUID, CMD_CELL_DATA, response=False)
            await asyncio.sleep(2.5)
            
            print("[3/5] Sending getCellData command (0x96) - attempt 2...")
            await client.write_gatt_char(CHARACTERISTIC_UUID, CMD_CELL_DATA, response=False)
            await asyncio.sleep(2.5)
            
            print("[4/5] Sending getCellData command (0x96) - attempt 3...")
            await client.write_gatt_char(CHARACTERISTIC_UUID, CMD_CELL_DATA, response=False)
            await asyncio.sleep(2.5)
            
            print("[5/5] Waiting for final responses...")
            await asyncio.sleep(2.0)
            
            # Stop notifications
            await client.stop_notify(CHARACTERISTIC_UUID)
            
            # Parse multiple records if received
            if received_data and len(received_data) > 100:
                print("\n" + "=" * 60)
                print("BATTERY STATUS")
                print("=" * 60)
                print(f"\nReceived {len(received_data)} bytes of data")
                print(f"Full data (hex): {received_data.hex()}")
                print(f"Full hex data saved to jk_bms_data.hex for analysis")
                with open('jk_bms_data.hex', 'w') as f:
                    f.write(received_data.hex())
                
                # Split and parse multiple records
                pos = 0
                record_num = 1
                while pos < len(received_data) - 10:
                    # Look for record start
                    if pos + 6 <= len(received_data) and \
                       received_data[pos:pos+2] == b'\x55\xAA' and \
                       received_data[pos+2:pos+4] == b'\xEB\x90':
                        
                        record_type = received_data[pos+4]
                        frame_len = received_data[pos+5]
                        
                        # Estimate record length (usually 300 bytes for type 0x03, variable for others)
                        if record_type == 0x03:
                            record_len = 320  # Device info is typically 320 bytes
                        else:
                            record_len = 300  # Cell data is typically 300 bytes
                        
                        end_pos = min(pos + record_len, len(received_data))
                        
                        if record_num > 1:
                            print("\n" + "-" * 60)
                        print(f"\n### Record {record_num} ###")
                        parse_jk_bms_data(bytes(received_data[pos:end_pos]))
                        
                        pos = end_pos
                        record_num += 1
                    else:
                        pos += 1
                        
            else:
                print(f"\nReceived {len(received_data)} bytes")
                print("Raw data:", received_data.hex() if received_data else "No data")
                
    except Exception as e:
        print(f"Error: {e}")


def parse_jk_bms_data(data):
    """
    Parse JK BMS data packet - Record types 0x01/0x02 (cell info) or 0x03 (device info)
    Based on esphome-jk-bms and mpp-solar protocol documentation
    """
    try:
        if len(data) < 6:
            print(f"Data too short: {len(data)} bytes")
            return
            
        if data[0:2] == b'\x55\xAA' and data[2:4] == b'\xEB\x90':
            record_type = data[4]
            frame_counter = data[5]
            
            print(f"\nRecord Type: 0x{record_type:02X}, Frame Counter: {frame_counter}")
            
            # Record type 0x01 - Settings/Configuration
            if record_type == 0x01:
                print("\n--- BMS Settings & Protection Parameters ---")
                
                # These are settings, not cell voltages
                settings_map = [
                    (6, "Cell Overvoltage Protection"),
                    (10, "Cell Undervoltage Protection"),
                    (14, "Cell Undervoltage Recovery"),
                    (18, "Cell Overvoltage Recovery"),
                    (22, "Battery Overvoltage Protection"),
                    (30, "Battery Overvoltage Recovery"),
                    (34, "Battery Undervoltage Protection"),
                    (46, "Cell Undervoltage Protection (duplicate)"),
                    (50, "Total Battery Voltage"),
                ]
                
                for offset, name in settings_map:
                    if offset + 4 <= len(data):
                        value_raw = int.from_bytes(data[offset:offset+2], byteorder='little')
                        value = value_raw / 1000.0
                        if value > 0.1:
                            print(f"  {name}: {value:.3f}V")
                
                # Total voltage at offset 50
                if len(data) > 54:
                    total_v = int.from_bytes(data[50:52], byteorder='little') / 1000.0
                    if total_v > 10:
                        print(f"\n  Total Battery Voltage: {total_v:.2f}V")
                
                # Cell count might be at offset 114
                if len(data) > 118:
                    cell_count = data[114]
                    if 0 < cell_count < 33:
                        print(f"  Number of Cells: {cell_count}")
            
            # Record type 0x02 - Cell Info (Real-time telemetry)
            elif record_type == 0x02:
                print("\n--- Cell Information (Real-time Data) ---")
                
                # Cell voltages start at offset 6, 2 bytes each (little-endian, NOT 4-byte stride)
                # This matches JK BMS protocol: all 16 cell slots stored consecutively
                cells = []
                for i in range(16):  # Check up to 16 possible cells
                    offset = 6 + (i * 2)  # 2 bytes per cell voltage
                    if offset + 2 <= len(data):
                        cell_v_raw = int.from_bytes(data[offset:offset+2], byteorder='little')
                        cell_v = cell_v_raw / 1000.0
                        
                        # Valid cell voltage check (2.0 - 4.5V for LiFePO4/Li-ion)
                        if 2.0 < cell_v < 4.5:
                            cells.append(cell_v)
                        elif cell_v_raw == 0:
                            # Empty slot - stop reading
                            break
                
                if cells:
                    print(f"\nCell Count: {len(cells)}")
                    print("\nCell Voltages:")
                    for i, v in enumerate(cells, 1):
                        print(f"  Cell {i:2d}: {v:.3f}V")
                    
                    print(f"\nMin Cell: {min(cells):.3f}V")
                    print(f"Max Cell: {max(cells):.3f}V")
                    print(f"Delta: {max(cells) - min(cells):.3f}V")
                    print(f"Average: {sum(cells)/len(cells):.3f}V")
                    
                    # Total Voltage: BMS measures and reports directly at offset 150 (32-bit, little-endian, mV)
                    if len(data) > 154:
                        total_v_raw = int.from_bytes(data[150:154], byteorder='little')
                        total_v = total_v_raw / 1000.0
                        print(f"Total Voltage (from BMS at offset 150): {total_v:.2f}V")
                    else:
                        # Fallback to calculated
                        print(f"Total Voltage (calculated): {sum(cells):.2f}V")
                
                # Extract Current and Temperature from record 0x02
                # Current: typically 32-bit signed at offset ~108, in mA
                # Temperature: 16-bit signed at offset ~114, in 0.1°C units
                if len(data) > 112:
                    try:
                        current_raw = int.from_bytes(data[108:112], byteorder='little', signed=True)
                        current = current_raw / 1000.0  # Convert mA to A
                        print(f"\nCurrent: {current:.3f}A")
                    except:
                        pass
                
                if len(data) > 116:
                    try:
                        temp_raw = int.from_bytes(data[114:116], byteorder='little', signed=True)
                        temp = temp_raw / 10.0  # Convert 0.1°C to °C
                        print(f"Temperature: {temp:.1f}°C")
                    except:
                        pass
            
            # Record type 0x03 - Device Info
            elif record_type == 0x03:
                print("\n--- Device Information ---")
                print("(This record type does not contain real-time battery data)")
                print("Requesting cell info instead...")
                
            else:
                print(f"\nUnknown record type: 0x{record_type:02X}")
                print("Dumping first 100 bytes for analysis:")
                print(data[:100].hex())
        
        print("\n" + "=" * 60)
        
    except Exception as e:
        print(f"Error parsing data: {e}")
        import traceback
        traceback.print_exc()
        print("\nCheck jk_bms_data.hex for raw data")


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='JK BMS Bluetooth Reader')
    parser.add_argument('--scan', action='store_true', help='Scan for devices and update YAML file')
    parser.add_argument('--address', type=str, help='Specific device address to connect to')
    args = parser.parse_args()
    
    try:
        if args.scan:
            # Scan for devices and save to YAML
            print("Scanning mode enabled...")
            devices = await scan_for_jk_bms(scan_time=10.0)
            
            # If devices found, connect to first one
            if devices:
                print("\n" + "=" * 60)
                await read_jk_bms_data(devices[0].address)
        else:
            # Use saved devices from YAML
            saved_devices = load_devices_from_yaml()
            
            if args.address:
                # Use specified address
                print(f"Using specified address: {args.address}")
                await read_jk_bms_data(args.address)
            elif saved_devices:
                # Use first saved device
                first_address = list(saved_devices.keys())[0]
                device_info = saved_devices[first_address]
                print(f"Using saved device: {device_info.get('name', 'Unknown')} ({first_address})")
                print(f"Last seen: {device_info.get('last_seen', 'Unknown')}")
                await read_jk_bms_data(first_address)
            else:
                print(f"No saved devices found in {DEVICES_FILE}")
                print("Run with --scan to discover devices:")
                print("  python jk-util.py --scan")
            
    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("  1. Bluetooth is enabled on your system")
        print("  2. You have the required permissions")
        print("  3. bleak package is installed: pip install bleak")


if __name__ == "__main__":
    asyncio.run(main())
