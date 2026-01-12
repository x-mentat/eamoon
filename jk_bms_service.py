#!/usr/bin/env python3
"""
JK BMS Background Service
Continuously scans and updates battery device data in the database
Runs as a systemd service independent of the web interface
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from device_service import DeviceService

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('jk_bms_service.log')
    ]
)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv()

DB_PATH = os.getenv("DB_PATH", "inverter.db")
DEVICES_FILE = os.getenv("DEVICES_FILE", "jk_devices.yaml")
SCAN_INTERVAL = int(os.getenv("JK_SCAN_INTERVAL", "300"))  # 5 minutes
RESCAN_INTERVAL = int(os.getenv("JK_RESCAN_INTERVAL", "3600"))  # 1 hour

# Global shutdown flag
shutdown_event = asyncio.Event()


def signal_handler(signum, frame):
    """Handle termination signals."""
    logger.info(f"Received signal {signum}, shutting down...")
    shutdown_event.set()


async def scan_and_update_devices(device_service: DeviceService) -> None:
    """Scan for new devices and update all known devices."""
    try:
        # Load known devices
        devices = device_service.load_devices()

        if not devices:
            logger.info("No configured devices. Attempting discovery...")
            discovered = await device_service.scan_for_devices(scan_time=15.0)
            if discovered:
                device_service.save_devices(discovered)
                devices = discovered
                logger.info(f"Discovered and saved {len(devices)} devices")
            else:
                logger.warning("No devices discovered")
                return

        # Update each device
        logger.info(f"Updating {len(devices)} device(s)...")
        for device in devices:
            if shutdown_event.is_set():
                logger.info("Shutdown requested, stopping device updates")
                return

            address = device.get("address")
            name = device.get("name", address)

            try:
                logger.info(f"Reading {name} ({address})...")
                data = await device_service.read_device_data(address)

                if data:
                    device_service.save_device_reading(address, data)
                    voltage = data.get("voltage", "N/A")
                    current = data.get("current", "N/A")
                    temp = data.get("temperature", "N/A")
                    cells = len(data.get("cells", []))
                    logger.info(
                        f"✓ {name}: {voltage}V, {current}A, {temp}°C, {cells} cells"
                    )
                else:
                    logger.warning(f"✗ Failed to read {name}")

            except Exception as e:
                logger.error(f"Error reading device {name}: {e}")

            # Brief pause between devices
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"Error in scan_and_update_devices: {e}")


async def periodic_discovery(device_service: DeviceService) -> None:
    """Periodically scan for new devices."""
    try:
        await asyncio.sleep(RESCAN_INTERVAL)

        if shutdown_event.is_set():
            return

        logger.info("Running periodic device discovery...")
        discovered = await device_service.scan_for_devices(scan_time=15.0)

        if discovered:
            device_service.save_devices(discovered)
            logger.info(f"Discovery found {len(discovered)} device(s)")
        else:
            logger.info("No new devices discovered")

    except Exception as e:
        logger.error(f"Error in periodic_discovery: {e}")


async def main_loop(device_service: DeviceService) -> None:
    """Main service loop."""
    logger.info("=" * 60)
    logger.info("JK BMS Background Service Started")
    logger.info(f"Database: {DB_PATH}")
    logger.info(f"Devices file: {DEVICES_FILE}")
    logger.info(f"Update interval: {SCAN_INTERVAL}s")
    logger.info(f"Discovery interval: {RESCAN_INTERVAL}s")
    logger.info("=" * 60)

    # Create tasks for concurrent operations
    update_task = asyncio.create_task(update_loop(device_service))
    discovery_task = asyncio.create_task(discovery_loop(device_service))

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Cancel tasks
    update_task.cancel()
    discovery_task.cancel()

    try:
        await asyncio.gather(update_task, discovery_task)
    except asyncio.CancelledError:
        pass

    logger.info("JK BMS Service stopped")


async def update_loop(device_service: DeviceService) -> None:
    """Continuously update device readings."""
    while not shutdown_event.is_set():
        try:
            await scan_and_update_devices(device_service)
        except Exception as e:
            logger.error(f"Error in update loop: {e}")

        # Wait for next update or shutdown
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=SCAN_INTERVAL
            )
            break  # Shutdown was signaled
        except asyncio.TimeoutError:
            pass  # Continue with next update


async def discovery_loop(device_service: DeviceService) -> None:
    """Periodically discover new devices."""
    while not shutdown_event.is_set():
        try:
            await periodic_discovery(device_service)
        except Exception as e:
            logger.error(f"Error in discovery loop: {e}")


def main():
    """Entry point."""
    # Ensure database exists
    from data_store import init_db
    init_db(DB_PATH)

    # Initialize service
    device_service = DeviceService(DB_PATH, DEVICES_FILE)

    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        asyncio.run(main_loop(device_service))
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
