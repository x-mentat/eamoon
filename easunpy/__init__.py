# easunpy/__init__.py
# Main entry point for the library, includes a factory to get the correct inverter instance.

import logging
from typing import Union

from .models import MODEL_CONFIGS
from .async_isolar import AsyncISolar
from .async_asciiinverter import AsyncAsciiInverter

logger = logging.getLogger(__name__)

def get_inverter(model: str, inverter_ip: str, local_ip: str) -> Union[AsyncISolar, AsyncAsciiInverter]:
    """
    Factory function to get the correct inverter communication class based on the model.
    """
    if model not in MODEL_CONFIGS:
        raise ValueError(f"Unknown inverter model: {model}. Available models: {list(MODEL_CONFIGS.keys())}")

    model_config = MODEL_CONFIGS[model]
    
    if model_config.protocol == "ascii":
        logger.info(f"Creating ASCII inverter instance for model {model}")
        return AsyncAsciiInverter(inverter_ip, local_ip)
    elif model_config.protocol == "modbus":
        logger.info(f"Creating Modbus inverter instance for model {model}")
        return AsyncISolar(inverter_ip, local_ip, model)
    else:
        raise ValueError(f"Unsupported protocol '{model_config.protocol}' for model {model}")

