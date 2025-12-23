"""Inverter communication library (Modbus only)."""
import logging

from .async_isolar import AsyncISolar
from .models import MODEL_CONFIGS

logger = logging.getLogger(__name__)


def get_inverter(model: str, inverter_ip: str, local_ip: str) -> AsyncISolar:
    """Get Modbus inverter instance for the selected model."""
    if model not in MODEL_CONFIGS:
        raise ValueError(
            f"Unknown inverter model: {model}. "
            f"Available models: {list(MODEL_CONFIGS.keys())}"
        )

    model_config = MODEL_CONFIGS[model]

    if model_config.protocol != "modbus":
        raise ValueError(
            f"Unsupported protocol '{model_config.protocol}' for model {model}"
        )

    logger.info("Creating Modbus inverter instance for model %s", model)
    return AsyncISolar(inverter_ip, local_ip, model)

