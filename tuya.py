import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

import requests

# -----------------------------
# CONFIG
# -----------------------------
ACCESS_ID = "v9mytkyd9cx9qacccktq"
ACCESS_SECRET = "5f70e63cd0dd466f8a7e4ddd6b7ed7cf"
ENDPOINT = "https://openapi.tuyaeu.com"  # EU region
# Optional: set your Tuya project ID to enumerate devices
PROJECT_ID = "p17648925072237qsmsn"
APP_SCHEMA = "smartlife"

# -----------------------------


def _string_to_sign(method: str, path: str, query: str = "", body: str = "") -> str:
    """Build Tuya v2 string-to-sign."""
    body = body or ""
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    path_with_query = f"{path}?{query}" if query else path
    return "\n".join([method.upper(), body_hash, "", path_with_query])


def _calc_sign(
    method: str,
    path: str,
    query: str = "",
    body: str = "",
    token: str = "",
) -> tuple[str, str]:
    """Return (sign, timestamp_ms) for Tuya requests."""
    timestamp = str(int(time.time() * 1000))
    string_to_sign = _string_to_sign(method, path, query, body)
    message = f"{ACCESS_ID}{token}{timestamp}{string_to_sign}"
    signature = hmac.new(
        ACCESS_SECRET.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest().upper()
    return signature, timestamp


def _request(
    method: str,
    path: str,
    *,
    query: str = "",
    body: Optional[Dict[str, Any]] = None,
    token: str = "",
) -> Dict[str, Any]:
    """Send a signed Tuya Cloud request."""
    body_json = "" if body is None else json.dumps(body, separators=(",", ":"))
    sign, timestamp = _calc_sign(method, path, query, body_json, token)

    url = f"{ENDPOINT}{path}"
    if query:
        url = f"{url}?{query}"

    headers = {
        "client_id": ACCESS_ID,
        "sign": sign,
        "t": timestamp,
        "sign_method": "HMAC-SHA256",
        "sign_version": "1.0",
    }
    if token:
        headers["access_token"] = token
    if body_json:
        headers["Content-Type"] = "application/json"

    resp = requests.request(method, url, headers=headers, data=body_json or None, timeout=30)
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Tuya API failed ({path}): {data}")
    return data


def get_token() -> str:
    """Fetch access_token."""
    data = _request("GET", "/v1.0/token", query="grant_type=1")
    return data["result"]["access_token"]


def get_device_status(token: str, device_id: str) -> Dict[str, Any]:
    """Fetch device status for the given device using provided token."""
    data = _request("GET", f"/v1.0/devices/{device_id}/status", token=token)
    return data["result"]


def list_devices(token: str) -> list[Dict[str, Any]]:
    """
    List devices that belong to the current Tuya app/account.
    Uses /v1.0/devices with page_no/page_size.
    """
    # ПАРАМЕТРИ МАЮТЬ БУТИ В АЛФАВІТНОМУ ПОРЯДКУ: page_no, page_size, schema
    query = f"page_no=1&page_size=100&schema={APP_SCHEMA}"
    data = _request("GET", "/v1.0/devices", query=query, token=token)

    # see docs: result.devices[]
    result = data.get("result") or {}
    devices = result.get("devices") or []
    return devices


def _format_status(status_items: list[Dict[str, Any]]) -> str:
    """Create a human-friendly string from Tuya status list."""
    lines = []
    for item in status_items:
        code = item.get("code")
        value = item.get("value")
        lines.append(f"- {code}: {value}")
    return "\n".join(lines) if lines else "- no status data"


if __name__ == "__main__":
    try:
        token = get_token()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Failed to query Tuya API: {exc}") from exc

    # Try to enumerate all devices under the project
    devices: list[Dict[str, Any]] = []
    try:
        devices = list_devices(token)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not list devices: {exc}")

    if not devices:
        print("\nNo devices returned; check APP_SCHEMA/PROJECT_ID/region settings.")
        raise SystemExit(1)

    for dev in devices:
        dev_id = dev.get("id")
        if not dev_id:
            print("\nDevice entry missing 'id'; skipping.")
            continue
        name = dev.get("name", dev_id)
        try:
            status = get_device_status(token, device_id=dev_id)
            status_items = status if isinstance(status, list) else status.get("status", [])
            print(f"\nDevice: {name} ({dev_id})")
            print(_format_status(status_items))
        except Exception as exc:  # noqa: BLE001
            print(f"\nDevice: {name} ({dev_id})")
            print(f"- failed to fetch status: {exc}")
