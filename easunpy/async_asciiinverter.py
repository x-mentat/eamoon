# easunpy/async_asciiinverter.py
import asyncio
import logging
from typing import Optional, Tuple

from .async_asciiclient import AsyncAsciiClient
from .models import BatteryData, PVData, GridData, OutputData, SystemStatus, RatingData
from .async_ascii_commands import parse_qpgis, parse_qmod, parse_qpiri, parse_qpiws, parse_qpgis2

logger = logging.getLogger(__name__)

class AsyncAsciiInverter:
    """High-level class to interact with a Voltronic ASCII inverter."""
    def __init__(self, inverter_ip: str, local_ip: str):
        self.client = AsyncAsciiClient(inverter_ip=inverter_ip, local_ip=local_ip)
        self.model = "VOLTRONIC_ASCII"

    async def get_all_data(self) -> Tuple[Optional[BatteryData], Optional[PVData], Optional[GridData], Optional[OutputData], Optional[SystemStatus], Optional[RatingData]]:
        """Fetches all data from the inverter by sending ASCII commands sequentially."""
        await self.client.ensure_connection()

        if not self.client.is_connected():
            logger.info("Inverter is not connected yet. Waiting for connection.")
            return None, None, None, None, None, None

        try:
            # Run commands sequentially
            qpgis_res = await self.client.send_command("QPIGS")
            await asyncio.sleep(0.2)
            qmod_res = await self.client.send_command("QMOD")
            await asyncio.sleep(0.2)
            qpiri_res = await self.client.send_command("QPIRI")
            await asyncio.sleep(0.2)
            qpiws_res = await self.client.send_command("QPIWS")
            await asyncio.sleep(0.2)
            
            qpgis2_res = None
            try:
                qpgis2_res = await self.client.send_command("QPIGS2")
            except Exception:
                logger.info("Command QPIGS2 failed (this may be normal for single MPPT models).")

            # Parse all responses
            qpgis_data = parse_qpgis(qpgis_res)
            op_mode = parse_qmod(qmod_res)
            rating_data_dict = parse_qpiri(qpiri_res)
            warnings = parse_qpiws(qpiws_res)
            qpgis2_data = parse_qpgis2(qpgis2_res) if qpgis2_res else {}

            if not qpgis_data or not rating_data_dict:
                logger.warning("Failed to get essential data (QPIGS or QPIRI).")
                return None, None, None, None, None, None

            # Populate data classes
            battery = BatteryData(
                voltage=qpgis_data.get('battery_voltage', 0.0),
                power=int(qpgis_data.get('battery_voltage', 0.0) * (qpgis_data.get('battery_charging_current', 0) - qpgis_data.get('battery_discharge_current', 0))),
                current=float(qpgis_data.get('battery_charging_current', 0) - qpgis_data.get('battery_discharge_current', 0)),
                soc=qpgis_data.get('battery_soc', 0),
                temperature=qpgis_data.get('inverter_temperature', 0)
            )

            pv = PVData(
                total_power=qpgis_data.get('pv_charging_power', 0) + qpgis2_data.get('pv2_charging_power', 0),
                charging_power=qpgis_data.get('pv_charging_power', 0) + qpgis2_data.get('pv2_charging_power', 0),
                charging_current=qpgis_data.get('pv1_input_current', 0.0) + qpgis2_data.get('pv2_input_current', 0.0),
                temperature=qpgis_data.get('inverter_temperature', 0),
                pv1_voltage=qpgis_data.get('pv1_input_voltage', 0.0),
                pv1_current=qpgis_data.get('pv1_input_current', 0.0),
                pv1_power=int(qpgis_data.get('pv1_input_voltage', 0.0) * qpgis_data.get('pv1_input_current', 0.0)),
                pv2_voltage=qpgis2_data.get('pv2_input_voltage', 0.0),
                pv2_current=qpgis2_data.get('pv2_input_current', 0.0),
                pv2_power=qpgis2_data.get('pv2_charging_power', 0),
                pv_generated_today=0.0, pv_generated_total=0.0,
            )
            
            grid = GridData(voltage=qpgis_data.get('grid_voltage', 0.0), power=0, frequency=int(qpgis_data.get('grid_frequency', 0.0) * 100))
            output = OutputData(
                voltage=qpgis_data.get('output_voltage', 0.0), current=0.0,
                power=qpgis_data.get('output_power', 0),
                apparent_power=qpgis_data.get('output_apparent_power', 0),
                load_percentage=qpgis_data.get('output_load_percentage', 0),
                frequency=int(qpgis_data.get('output_frequency', 0.0) * 100),
            )
            status = SystemStatus(operating_mode=op_mode, mode_name=op_mode.name if op_mode else "UNKNOWN", warnings=warnings, inverter_time=None)
            
            # This was the point of failure. Ensure the dictionary is not empty before creating the object.
            rating = RatingData(**rating_data_dict) if rating_data_dict else None

            return battery, pv, grid, output, status, rating

        except Exception as e:
            logger.error(f"Error getting all data for ASCII inverter: {e}", exc_info=True)
            await self.client.disconnect()
            return None, None, None, None, None, None
            
    async def update_model(self, model: str):
        logger.debug("Model update called for ASCII inverter; no action needed.")
        pass
