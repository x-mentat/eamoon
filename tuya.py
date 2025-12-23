"""Tuya Cloud API client for device status monitoring."""

import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# -----------------------------
# CONFIG
# -----------------------------
ACCESS_ID = (os.getenv("TUYA_ACCESS_ID", "") or "").strip()
ACCESS_SECRET = (os.getenv("TUYA_ACCESS_SECRET", "") or "").strip()
ENDPOINT = (
    os.getenv("TUYA_ENDPOINT", "https://openapi.tuyaeu.com")
    or "https://openapi.tuyaeu.com"
).strip()
PROJECT_ID = (os.getenv("TUYA_PROJECT_ID", "") or "").strip()
APP_SCHEMA = (os.getenv("TUYA_APP_SCHEMA", "smartlife") or "smartlife").strip()
USER_ID = (os.getenv("TUYA_USER_ID", "") or "").strip()

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

    try:
        resp = requests.request(method, url, headers=headers, data=body_json or None, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raise RuntimeError(f"Tuya API request failed ({path}): {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON response from Tuya API ({path}): {e}") from e

    if not data.get("success"):
        error_msg = data.get("msg", "Unknown error")
        error_code = data.get("code", "N/A")
        raise RuntimeError(f"Tuya API error ({path}): [{error_code}] {error_msg}")
    return data


def get_token() -> str:
    """Fetch access_token."""
    data = _request("GET", "/v1.0/token", query="grant_type=1")
    return data["result"]["access_token"]


def get_device_status(token: str, device_id: str) -> Dict[str, Any]:
    """Fetch device status for the given device using provided token."""
    data = _request("GET", f"/v1.0/devices/{device_id}/status", token=token)
    return data["result"]


def send_device_command(
    token: str, device_id: str, commands: list[Dict[str, Any]]
) -> Dict[str, Any]:
    """Send one or more commands to a device.

    Args:
        token: Access token.
        device_id: Device ID.
        commands: List of command dicts, e.g. [{"code": "switch_1", "value": False}].

    Returns:
        API response.
    """
    body = {"commands": commands}
    return _request(
        "POST", f"/v1.0/devices/{device_id}/commands", body=body, token=token
    )


def turn_device_off(token: str, device_id: str) -> Dict[str, Any]:
    """Turn off the primary switch (switch_1) for a device."""
    return send_device_command(token, device_id, [{"code": "switch_1", "value": False}])


def list_devices(token: str, schema: Optional[str] = None) -> list[Dict[str, Any]]:
    """List devices in user mode using /v1.0/users/{user_id}/devices/.

    Requires TUYA_USER_ID to be set. If missing, raises an error with guidance.

    Args:
        token: Access token from get_token().
        schema: Unused, kept for compatibility.

    Returns:
        List of device dictionaries.
    """
    if not USER_ID:
        raise RuntimeError(
            "TUYA_USER_ID is required for user-mode listing. Set it in .env (e.g., euXXXXXXXX)."
        )

    path = f"/v1.0/users/{USER_ID}/devices/"
    data = _request("GET", path, token=token)

    result = data.get("result", {})
    # Depending on response shape, it may be a list or an object with 'devices'. Handle both.
    if isinstance(result, list):
        devices = result
    else:
        devices = result.get("devices", [])
    total = len(devices)

    print(f"User mode: listing devices for user {USER_ID} (found {total})")
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
    print("Tuya Cloud API Client (User Mode)")
    print("=" * 50)
    print(f"Endpoint: {ENDPOINT}")
    print(f"Access ID: {ACCESS_ID[:8] if ACCESS_ID else 'NOT SET'}...")
    print(f"App Schema: {APP_SCHEMA}")
    print(f"User ID: {USER_ID or 'NOT SET'}")
    print("=" * 50)

    # Validate credentials
    if not ACCESS_ID or not ACCESS_SECRET:
        print("\n✗ Error: TUYA_ACCESS_ID and TUYA_ACCESS_SECRET must be configured")
        raise SystemExit(1)
    if not USER_ID:
        print("\n✗ Error: TUYA_USER_ID must be set for user-mode operations")
        raise SystemExit(1)

    # Get access token
    try:
        print("\n[1/3] Fetching access token...")
        token = get_token()
        print("✓ Token acquired successfully")
    except Exception as exc:
        print(f"✗ Failed to get token: {exc}")
        print("\nPossible issues:")
        print("- Invalid TUYA_ACCESS_ID or TUYA_ACCESS_SECRET")
        print("- Network connectivity issue")
        print("- Wrong endpoint region (try: tuyaeu.com, tuyaus.com, tuyacn.com)")
        raise SystemExit(1) from exc

    # Parse CLI arguments
    import argparse
    parser = argparse.ArgumentParser(description="Control Tuya devices")
    parser.add_argument(
        "action",
        nargs="?",
        default="list",
        help="Action: list (default), off <device_id>, on <device_id>, status",
    )
    parser.add_argument(
        "device_id",
        nargs="?",
        default="",
        help="Device ID for off/on/status actions",
    )
    args = parser.parse_args()

    action = args.action
    device_id = args.device_id

    # Handle non-list actions early
    if action in ("off", "on", "status") and device_id:
        try:
            if action == "off":
                print(f"\nTurning OFF device {device_id}...")
                result = turn_device_off(token, device_id)
                print(f"✓ Command sent: {result}")
            elif action == "on":
                print(f"\nTurning ON device {device_id}...")
                result = send_device_command(token, device_id, [{"code": "switch_1", "value": True}])
                print(f"✓ Command sent: {result}")
            elif action == "status":
                print(f"\nFetching status for device {device_id}...")
                status = get_device_status(token, device_id)
                status_items = status if isinstance(status, list) else status.get("status", [])
                print(_format_status(status_items))
        except Exception as exc:
            print(f"✗ Failed: {exc}")
            raise SystemExit(1) from exc
        raise SystemExit(0)

    # List devices (user mode)
    devices: list[Dict[str, Any]] = []
    try:
        print("\n[2/3] Listing user devices...")
        devices = list_devices(token)
        print(f"✓ Found {len(devices)} device(s)")
    except Exception as exc:
        error_str = str(exc)
        print(f"✗ Failed to list devices: {exc}")

        if "1106" in error_str or "permission" in error_str.lower():
            print("\n[!] Permission Denied - User Mode")
            print("\nCheck:")
            print("- TUYA_USER_ID is correct for this account/region")
            print("- Devices are linked to this Tuya account in the mobile app")
            print("- API client has user-device permissions in Tuya IoT Platform")
            print("- Endpoint matches your account region")
        else:
            print("\nTroubleshooting:")
            print("- Verify TUYA_ACCESS_ID / TUYA_ACCESS_SECRET")
            print("- Verify TUYA_USER_ID")
            print("- Check endpoint region")

        raise SystemExit(1) from exc

    if not devices:
        print("\n⚠ No devices found for this user")
        raise SystemExit(1)

    # Get device status
    print("\n[3/3] Fetching device status...")
    print("=" * 50)
    success_count = 0
    for i, dev in enumerate(devices, 1):
        dev_id = dev.get("id")
        if not dev_id:
            print(f"\n[{i}/{len(devices)}] Device missing 'id'; skipping")
            continue

        name = dev.get("name", dev_id)
        category = dev.get("category", "unknown")
        product_name = dev.get("product_name", "N/A")

        try:
            status = get_device_status(token, device_id=dev_id)
            status_items = status if isinstance(status, list) else status.get("status", [])
            print(f"\n[{i}/{len(devices)}] {name} ({category})")
            print(f"Product: {product_name}")
            print(f"ID: {dev_id}")
            print(f"Status:\n{_format_status(status_items)}")
            success_count += 1
        except Exception as exc:
            print(f"\n[{i}/{len(devices)}] {name} ({category})")
            print(f"ID: {dev_id}")
            print(f"✗ Failed to fetch status: {exc}")

    print("\n" + "=" * 50)
    print(f"Summary: {success_count}/{len(devices)} devices queried successfully")
