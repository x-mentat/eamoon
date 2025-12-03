# easunpy/async_isolar.py
# This file now acts as the main class for Modbus-based inverters.

import logging
from typing import List, Optional, Dict, Tuple, Any
from .async_modbusclient import AsyncModbusClient
from .modbusclient import create_request, decode_modbus_response
from .models import BatteryData, PVData, GridData, OutputData, SystemStatus, OperatingMode, MODEL_CONFIGS, ModelConfig
import datetime

logger = logging.getLogger(__name__)

class AsyncISolar:
    def __init__(self, inverter_ip: str, local_ip: str, model: str = "ISOLAR_SMG_II_11K"):
        self.client = AsyncModbusClient(inverter_ip=inverter_ip, local_ip=local_ip)
        self._transaction_id = 0x0772
        
        if model not in MODEL_CONFIGS:
            raise ValueError(f"Unknown inverter model: {model}. Available models: {list(MODEL_CONFIGS.keys())}")
        
        self.model = model
        self.model_config = MODEL_CONFIGS[model]
        if self.model_config.protocol != 'modbus':
            raise ValueError(f"Model {model} uses protocol '{self.model_config.protocol}', not 'modbus'.")
            
        logger.info(f"AsyncISolar (Modbus) initialized with model: {model}")

    def update_model(self, model: str):
        """Update the model configuration."""
        if model not in MODEL_CONFIGS:
            raise ValueError(f"Unknown inverter model: {model}. Available models: {list(MODEL_CONFIGS.keys())}")
        
        logger.info(f"Updating AsyncISolar to model: {model}")
        self.model = model
        self.model_config = MODEL_CONFIGS[model]

    def _get_next_transaction_id(self) -> int:
        """Get next transaction ID and increment counter."""
        current_id = self._transaction_id
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        return current_id

    async def _read_registers_bulk(self, register_groups: list[tuple[int, int]], data_format: str = "Int") -> list[Optional[list[int]]]:
        """Read multiple groups of registers in a single connection."""
        unit_id = 0x01
        func_preference = [self.model_config.read_function]
        # If the preferred function fails, try the alternate one (3 vs 4) as a fallback.
        if self.model_config.read_function == 4:
            func_preference.append(3)
        elif self.model_config.read_function == 3:
            func_preference.append(4)

        for func in func_preference:
            try:
                func_code = 0x04 if func == 4 else 0x03
                requests = [
                    create_request(self._get_next_transaction_id(), 0x0001, unit_id, func_code, start, count)
                    for start, count in register_groups
                ]

                logger.debug(f"Sending bulk request (FC={func_code}) for register groups: {register_groups}")
                responses = await self.client.send_bulk(requests)

                decoded_groups = [None] * len(register_groups)
                for i, (response, (_, count)) in enumerate(zip(responses, register_groups)):
                    try:
                        if response:
                            decoded = decode_modbus_response(response, count, data_format)
                            decoded_groups[i] = decoded
                    except Exception as e:
                        logger.warning(f"Failed to decode register group {register_groups[i]}: {e}")

                if any(group is not None for group in decoded_groups):
                    return decoded_groups
            except Exception as e:
                logger.error(f"Error reading register groups with FC={func}: {str(e)}")

        # If all attempts failed, return a list of Nones.
        return [None] * len(register_groups)

    async def get_all_data(self) -> tuple[Optional[BatteryData], Optional[PVData], Optional[GridData], Optional[OutputData], Optional[SystemStatus], None]:
        """Get all inverter data in a single bulk request."""
        logger.info(f"Getting all data for Modbus model: {self.model}")
        
        register_groups = self._create_register_groups()
        
        results = await self._read_registers_bulk(register_groups)
        if not results:
            return None, None, None, None, None, None
            
        values = {}
        
        for i, (start_address, count) in enumerate(register_groups):
            if results[i] is None:
                continue
                
            for reg_name, config in self.model_config.register_map.items():
                if config.address >= start_address and config.address < start_address + count:
                    idx = config.address - start_address
                    if idx < len(results[i]):
                        values[reg_name] = self.model_config.process_value(reg_name, results[i][idx])
        
        battery = self._create_battery_data(values)
        pv = self._create_pv_data(values)
        grid = self._create_grid_data(values)
        output = self._create_output_data(values)
        status = self._create_system_status(values)
        
        # Modbus models do not have rating data, so return None for the 6th element
        return battery, pv, grid, output, status, None
        
    def _create_register_groups(self) -> list[tuple[int, int]]:
        """Create optimized register groups for reading."""
        min_addr = 0 if self.model_config.include_zero_addresses else 1
        addresses = sorted([
            config.address for config in self.model_config.register_map.values() if config.address >= min_addr
        ])
        
        if not addresses:
            return []
            
        groups = []
        current_start = addresses[0]
        current_end = current_start
        
        for addr in addresses[1:]:
            if addr <= current_end + 10:
                current_end = addr
            else:
                groups.append((current_start, current_end - current_start + 1))
                current_start = addr
                current_end = addr
                
        groups.append((current_start, current_end - current_start + 1))
        
        return groups
        
    def _create_battery_data(self, values: Dict[str, Any]) -> Optional[BatteryData]:
        try:
            return BatteryData(
                voltage=values.get("battery_voltage"), current=values.get("battery_current"),
                power=values.get("battery_power"), soc=values.get("battery_soc"),
                temperature=values.get("battery_temperature")
            )
        except (TypeError, KeyError): return None
        
    def _create_pv_data(self, values: Dict[str, Any]) -> Optional[PVData]:
        try:
            return PVData(
                total_power=values.get("pv_total_power"), charging_power=values.get("pv_charging_power"),
                charging_current=values.get("pv_charging_current"), temperature=values.get("pv_temperature"),
                pv1_voltage=values.get("pv1_voltage"), pv1_current=values.get("pv1_current"),
                pv1_power=values.get("pv1_power"), pv2_voltage=values.get("pv2_voltage"),
                pv2_current=values.get("pv2_current"), pv2_power=values.get("pv2_power"),
                pv_generated_today=values.get("pv_energy_today"), pv_generated_total=values.get("pv_energy_total")
            )
        except (TypeError, KeyError): return None
        
    def _create_grid_data(self, values: Dict[str, Any]) -> Optional[GridData]:
        try:
            return GridData(
                voltage=values.get("grid_voltage"), power=values.get("grid_power"),
                frequency=values.get("grid_frequency")
            )
        except (TypeError, KeyError): return None
        
    def _create_output_data(self, values: Dict[str, Any]) -> Optional[OutputData]:
        try:
            return OutputData(
                voltage=values.get("output_voltage"), current=values.get("output_current"),
                power=values.get("output_power"), apparent_power=values.get("output_apparent_power"),
                load_percentage=values.get("output_load_percentage"), frequency=values.get("output_frequency")
            )
        except (TypeError, KeyError): return None
        
    def _create_system_status(self, values: Dict[str, Any]) -> Optional[SystemStatus]:
        inverter_timestamp = None
        if all(f"time_register_{i}" in values for i in range(6)):
            try:
                y, m, d = values["time_register_0"], values["time_register_1"], values["time_register_2"]
                hh, mm, ss = values["time_register_3"], values["time_register_4"], values["time_register_5"]
                # Ignore obviously invalid timestamps (e.g., all zeros or year 0)
                if y and m and d and y > 0:
                    inverter_timestamp = datetime.datetime(y, m, d, hh, mm, ss)
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Failed to create timestamp: {e}")

        op_mode = None
        mode_name = "UNKNOWN"
        if "operation_mode" in values:
            mode_value = values["operation_mode"]
            try:
                op_mode = OperatingMode(mode_value)
                mode_name = op_mode.name
            except ValueError:
                mode_name = f"UNKNOWN ({mode_value})"

        return SystemStatus(
            operating_mode=op_mode,
            mode_name=mode_name,
            inverter_time=inverter_timestamp,
            warnings=[] # Explicitly provide empty list for Modbus models
        )
