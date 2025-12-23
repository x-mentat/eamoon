"""ASCII command handling for inverter communication."""
import logging
from typing import Any, Dict, List, Optional

from .models import OperatingMode

logger = logging.getLogger(__name__)

def parse_qpgis(raw: str) -> Dict[str, Any]:
    """Parses the response from the QPIGS command."""
    try:
        fields = raw.strip('(').split(' ')
        if len(fields) < 21:
            return {}
        return {
            'grid_voltage': float(fields[0]),
            'grid_frequency': float(fields[1]),
            'output_voltage': float(fields[2]),
            'output_frequency': float(fields[3]),
            'output_apparent_power': int(fields[4]),
            'output_power': int(fields[5]),
            'output_load_percentage': int(fields[6]),
            'bus_voltage': int(fields[7]),
            'battery_voltage': float(fields[8]),
            'battery_charging_current': int(fields[9]),
            'battery_soc': int(fields[10]),
            'inverter_temperature': int(fields[11]),
            'pv1_input_current': float(fields[12]),
            'pv1_input_voltage': float(fields[13]),
            'battery_voltage_scc': float(fields[14]),
            'battery_discharge_current': int(fields[15]),
            'device_status': fields[16],
            'pv_charging_power': int(fields[19]),
        }
    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse QPIGS response '{raw}': {e}")
        return {}

def parse_qmod(raw: str) -> Optional[OperatingMode]:
    """Parses the response from the QMOD command."""
    mode_char = raw.strip('(')
    mode_map = {
        'P': OperatingMode.POWER_ON, 'S': OperatingMode.STANDBY,
        'L': OperatingMode.LINE, 'B': OperatingMode.BATTERY,
        'F': OperatingMode.FAULT, 'H': OperatingMode.POWER_SAVING,
    }
    return mode_map.get(mode_char)

def parse_qpiri(raw: str) -> Dict[str, Any]:
    """Parses the response from the QPIRI command (Device Rating Information)."""
    try:
        fields = raw.strip('(').split(' ')
        if len(fields) < 25:
            return {}

        battery_type_map = {'0': "AGM", '1': "Flooded", '2': "User Defined", '3': "Pylontech"}
        priority_map = {'0': "Utility->Solar->Battery", '1': "Solar->Utility->Battery", '2': "Solar->Battery->Utility"}
        charger_priority_map = {'1': "Solar First", '2': "Solar and Utility", '3': "Solar Only"}

        return {
            'grid_rating_voltage': float(fields[0]),
            'grid_rating_current': float(fields[1]),
            'ac_output_rating_voltage': float(fields[2]),
            'ac_output_rating_frequency': float(fields[3]),
            'ac_output_rating_current': float(fields[4]),
            'ac_output_rating_apparent_power': int(fields[5]),
            'ac_output_rating_active_power': int(fields[6]),
            'battery_rating_voltage': float(fields[7]),
            'battery_recharge_voltage': float(fields[8]),
            'battery_under_voltage': float(fields[9]),
            'battery_bulk_voltage': float(fields[10]),
            'battery_float_voltage': float(fields[11]),
            'battery_type': battery_type_map.get(fields[12], f"Unknown ({fields[12]})"),
            'max_ac_charging_current': int(fields[13]),
            'max_charging_current': int(fields[14]),
            'output_source_priority': priority_map.get(fields[16], f"Unknown ({fields[16]})"),
            'charger_source_priority': charger_priority_map.get(fields[17], f"Unknown ({fields[17]})"),
        }
    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse QPIRI response '{raw}': {e}")
        return {}

def parse_qpiws(raw: str) -> List[str]:
    """Parses the response from the QPIWS command (Device Warning Status)."""
    warnings = []
    try:
        bits = raw.strip('(')
        if len(bits) < 32: return ["Invalid response length"]

        warning_map = {
            1: "Inverter fault", 2: "Bus over-voltage", 3: "Bus under-voltage",
            4: "Bus soft fail", 5: "Line fail", 6: "OPV short",
            7: "Inverter voltage too low", 8: "Inverter voltage too high",
            10: "Over temperature", 11: "Fan locked", 12: "Battery voltage high",
            13: "Battery low alarm", 15: "Battery under shutdown", 18: "Overload",
            19: "EEPROM fault", 22: "Power limit"
        }
        for i, bit in enumerate(bits):
            if bit == '1' and i in warning_map:
                warnings.append(warning_map[i])

    except Exception as e:
        logger.error(f"Failed to parse QPIWS response '{raw}': {e}")
        return ["Parsing Error"]

    return warnings if warnings else ["No warnings"]

def parse_qpgis2(raw: str) -> Dict[str, Any]:
    """Parses the response from the QPIGS2 command."""
    try:
        fields = raw.strip('(').split(' ')
        if len(fields) < 3: return {}
        return {
            'pv2_input_current': float(fields[0]),
            'pv2_input_voltage': float(fields[1]),
            'pv2_charging_power': int(fields[2]),
        }
    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse QPIGS2 response '{raw}': {e}")
        return {}
