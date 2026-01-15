# JK BMS Integration Guide

This guide explains how to integrate JK BMS (Battery Management System) with your Easun Inverter monitoring system for more accurate battery data.

## What is JK BMS?

JK BMS is a popular Battery Management System that monitors individual cell voltages, current, temperature, and provides accurate State of Charge (SOC) readings. When integrated with this monitoring system, it replaces the inverter's battery readings with more precise data directly from the BMS.

## Prerequisites

- JK BMS device with Bluetooth Low Energy (BLE) capability
- Linux system with Bluetooth adapter
- Python 3.8 or higher
- `bleak` and `pyyaml` packages installed

## Quick Start

### 1. Discover Your JK BMS Device

First, scan for available JK BMS devices:

```bash
cd /opt/eamoon
source .venv/bin/activate
python jk_bms/jk-util.py --scan
```

This will output something like:

```
Scanning for JK BMS devices for 10 seconds...
------------------------------------------------------------
✓ Found JK BMS Device:
  Name: JK-B2A24S
  Address: AA:BB:CC:DD:EE:FF
  RSSI: -65 dBm
------------------------------------------------------------

✓ Saved devices to jk_devices.yaml
```

The device information will be automatically saved to `jk_bms/jk_devices.yaml`.

### 2. Test Connection

Test reading data from your BMS:

```bash
python jk_bms/jk-util.py --address AA:BB:CC:DD:EE:FF
```

Or use the saved device:

```bash
python jk_bms/jk-util.py
```

You should see output like:

```
=============================================================
BATTERY STATUS
=============================================================

--- Cell Information (Real-time Data) ---

Cell Count: 16

Cell Voltages:
  Cell  1: 3.285V
  Cell  2: 3.287V
  ...
  Cell 16: 3.286V

Min Cell: 3.282V
Max Cell: 3.290V
Delta: 0.008V
Average: 3.286V
Total Voltage (from BMS): 52.58V

Current: 5.234A
Temperature: 18.5°C
```

### 3. Configure Environment

Add the JK BMS configuration to your `.env` file:

```bash
# JK BMS Bluetooth configuration
JK_BMS_ADDRESS=AA:BB:CC:DD:EE:FF
JK_BMS_POLL_INTERVAL=30
```

Or leave `JK_BMS_ADDRESS` empty to use the first device in `jk_devices.yaml`.

### 4. Install and Run Service

#### Manual Testing

```bash
python jk_bms_service.py
```

You should see logs like:

```
2026-01-15 12:00:00 - jk_bms_service - INFO - Starting JK BMS poller for AA:BB:CC:DD:EE:FF -> inverter.db every 30s
2026-01-15 12:00:02 - jk_bms_service - INFO - Battery updated: 52.6V, 5.2A, 85%, 18.5°C, 16 cells (delta: 0.008V)
```

#### Systemd Service (Recommended)

```bash
# Copy service file
sudo cp systemd/easun-jkbms.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable and start service
sudo systemctl enable --now easun-jkbms.service

# Check status
sudo systemctl status easun-jkbms.service

# View logs
sudo journalctl -u easun-jkbms.service -f
```

### 5. Enable in Docker (Optional)

If using Docker, edit `docker-compose.yml` and remove/comment the `replicas: 0` line under the `jkbms` service:

```yaml
  jkbms:
    build:
      context: .
      dockerfile: Dockerfile.poller
    command: python jk_bms_service.py
    env_file:
      - .env
    volumes:
      - .:/app
      - /var/run/dbus:/var/run/dbus:ro
    network_mode: host
    privileged: true
    # deploy:
    #   replicas: 0  # Comment this out to enable
```

Then restart:

```bash
docker compose up -d jkbms
```

## How It Works

### Data Flow

1. **JK BMS Service** connects to the BMS via Bluetooth every 30 seconds (configurable)
2. Reads battery data including:
   - Individual cell voltages (up to 16 cells)
   - Total battery voltage
   - Current (charge/discharge)
   - Temperature
   - Calculates SOC based on cell voltages
3. **Merges data** into existing database record (preserving inverter data)
4. **Web UI** displays the updated battery information
5. **Telegram bot** uses BMS data for `/status` and `/battery` commands

### What Data Is Updated

The JK BMS service updates these fields in the database:

- `battery_voltage` - More accurate than inverter reading
- `battery_current` - Direct measurement from BMS
- `battery_soc` - Calculated from cell voltages
- `battery_power` - Calculated as voltage × current
- `temperature` - Battery temperature from BMS
- `bms_cell_count` - Number of cells detected
- `bms_cell_min_v` - Lowest cell voltage
- `bms_cell_max_v` - Highest cell voltage
- `bms_cell_delta_v` - Voltage difference between cells

### SOC Calculation

SOC is estimated using LiFePO4 voltage curve:
- 3.65V/cell = 100%
- 3.30V/cell ≈ 50%
- 2.50V/cell = 0%

Linear interpolation is used between these points. For more accuracy, you may want to customize this in `jk_bms_service.py`.

## Troubleshooting

### BMS Not Found During Scan

1. Ensure Bluetooth is enabled: `sudo systemctl status bluetooth`
2. Check if your BMS is in range (< 10 meters recommended)
3. Make sure no other device is connected to the BMS
4. Try enabling the BMS's Bluetooth (some models have a power-saving mode)

### Connection Fails

1. Check Bluetooth permissions:
   ```bash
   sudo usermod -a -G bluetooth eamoon
   ```
2. Restart Bluetooth service:
   ```bash
   sudo systemctl restart bluetooth
   ```
3. Verify the MAC address is correct in `.env`

### No Data Received

1. Check the BMS firmware version (some old firmware may not respond correctly)
2. Try increasing the timeout in `jk_bms_service.py`
3. Use `jk-util.py` to test and capture raw data for analysis

### Service Keeps Restarting

1. Check logs: `sudo journalctl -u easun-jkbms.service -n 50`
2. Verify dependencies are installed: `pip install -r requirements.txt`
3. Test manually first: `python jk_bms_service.py`

## Advanced Configuration

### Custom SOC Calculation

Edit `jk_bms_service.py` and modify the SOC calculation in `parse_jk_bms_data()`:

```python
# Custom SOC curve for your battery chemistry
if avg_cell_v >= 3.65:
    soc = 100
elif avg_cell_v <= 2.50:
    soc = 0
else:
    # Your custom formula here
    soc = ((avg_cell_v - 2.50) / (3.65 - 2.50)) * 100
```

### Change Poll Interval

In `.env`:
```bash
JK_BMS_POLL_INTERVAL=60  # Poll every 60 seconds instead of 30
```

### Multiple BMS Devices

Currently only one BMS is supported. To use multiple devices, you would need to modify the service to handle multiple addresses.

## Data Displayed in Web UI

With JK BMS enabled, the battery section will show:

- **State of Charge**: From BMS SOC calculation
- **Voltage**: Direct from BMS (more accurate than inverter)
- **Current**: From BMS shunt measurement
- **Temperature**: Battery pack temperature

Additional BMS metrics are stored in the database but not displayed by default (cell min/max/delta voltages).

## Benefits

- **Accurate SOC**: Based on cell voltages, not just pack voltage
- **Cell Balancing Monitoring**: See voltage delta between cells
- **Better Temperature**: Direct from battery pack, not inverter
- **Precise Current**: From BMS shunt resistor
- **Early Warning**: Detect cell imbalances before they become problems

## Support

For issues specific to JK BMS integration:

1. Check the logs first
2. Run the test utility with debug output
3. Save the hex data output for analysis
4. Check the JK BMS community forums for protocol updates
