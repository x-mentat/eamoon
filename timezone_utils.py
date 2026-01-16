"""Timezone utilities for the eamoon project."""

from datetime import datetime, timezone, timedelta

# Timezone for Ukraine (EET - Eastern European Time, UTC+2)
EET = timezone(timedelta(hours=2))


def now_eet() -> datetime:
    """Get current time in EET timezone (Eastern European Time, UTC+2)."""
    return datetime.now(EET)


def utc_to_eet_str(utc_time_str: str) -> str:
    """
    Convert UTC timestamp string (ISO format) to EET formatted string.
    
    Args:
        utc_time_str: Timestamp string in ISO format (e.g., "2026-01-16T19:32:00")
    
    Returns:
        Formatted string in EET timezone
    """
    try:
        # Parse UTC time
        utc_dt = datetime.fromisoformat(utc_time_str.replace('Z', '+00:00'))
        # Convert to EET
        eet_dt = utc_dt.astimezone(EET)
        return eet_dt.strftime("%H:%M")
    except Exception:
        return utc_time_str
