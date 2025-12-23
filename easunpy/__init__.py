"""Inverter communication library with async support for multiple protocols."""
import logging
from typing import Union

from .async_asciiinverter import AsyncAsciiInverter
from .async_isolar import AsyncISolar
from .models import MODEL_CONFIGS

logger = logging.getLogger(__name__)

def get_inverter(
    model: str, inverter_ip: str, local_ip: str
) -> Union[AsyncISolar, AsyncAsciiInverter]:
    """Get inverter instance based on model.

    Args:
        model: Inverter model name.
        inverter_ip: IP address of the inverter.
        local_ip: Local machine IP address.

    Returns:
        AsyncISolar or AsyncAsciiInverter instance.

    Raises:
        ValueError: If model or protocol is unsupported.
    """
    if model not in MODEL_CONFIGS:
        raise ValueError(
            f"Unknown inverter model: {model}. "
            f"Available models: {list(MODEL_CONFIGS.keys())}"
        )

    model_config = MODEL_CONFIGS[model]

    if model_config.protocol == "ascii":
        logger.info("Creating ASCII inverter instance for model %s", model)
        return AsyncAsciiInverter(inverter_ip, local_ip)
    if model_config.protocol == "modbus":
        logger.info("Creating Modbus inverter instance for model %s", model)
        return AsyncISolar(inverter_ip, local_ip, model)
    raise ValueError(
        f"Unsupported protocol '{model_config.protocol}' for model {model}"
    )

