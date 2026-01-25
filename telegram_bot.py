"""Telegram bot for inverter monitoring and smart device control."""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from data_store import get_latest_reading
from timezone_utils import EET
try:
    import tuya
    TUYA_AVAILABLE = True
except ImportError:
    TUYA_AVAILABLE = False

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "inverter.db")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # optional, куди слати алерти
POLL_INTERVAL = int(os.getenv("BOT_POLL_INTERVAL", "10"))
TUYA_TURN_OFF_ON_POWER_LOSS = os.getenv("TUYA_TURN_OFF_ON_POWER_LOSS", "false").lower() in ("true", "1", "yes")
TUYA_TURN_ON_ON_GRID_BACK = os.getenv("TUYA_TURN_ON_ON_GRID_BACK", "false").lower() in ("true", "1", "yes")
QUEUE_NUMBER = os.getenv("QUEUE_NUMBER", "5.2")

if BOT_TOKEN:
    API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
else:
    API_URL = ""

SCHEDULE_CHECK_INTERVAL = int(os.getenv("SCHEDULE_CHECK_INTERVAL_MINUTES", "10")) * 60

# WARNING: вимикає перевірку TLS (як у твоєму середовищі на Windows)
UNVERIFIED_CTX = ssl._create_unverified_context()
SCHEDULE_SNAPSHOT_PATH = os.getenv("SCHEDULE_SNAPSHOT_PATH", "schedule_snapshot.json")


# ------------- Helpers -------------


def send_message(chat_id: int | str, text: str, parse_mode: str = "HTML", buttons: Optional[Dict[str, Any]] = None) -> None:
    """Send a message via Telegram bot API with optional inline buttons."""
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    
    if buttons:
        params["reply_markup"] = json.dumps(buttons)

    data = urllib.parse.urlencode(params).encode("utf-8")

    url = f"{API_URL}/sendMessage"
    with urllib.request.urlopen(  # noqa: S310
        url,
        data=data,
        timeout=10,
        context=UNVERIFIED_CTX,
    ) as resp:
        body = resp.read().decode("utf-8")
        data = json.loads(body)
        if not data.get("ok"):
            raise RuntimeError(f"Telegram send failed: {data}")


def get_status_buttons() -> Dict[str, Any]:
    """Build inline keyboard buttons for status message."""
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Refresh", "callback_data": "refresh_status"},
                {"text": "📊 Dashboard", "url": "http://172.16.0.41"},
            ],
            [
                {"text": "⚙️ Bot", "callback_data": "bot_menu"},
            ],
        ]
    }


def _load_schedule_snapshot() -> Dict[str, Any]:
    """Load stored schedule snapshot from disk."""
    if not os.path.exists(SCHEDULE_SNAPSHOT_PATH):
        return {}
    try:
        with open(SCHEDULE_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_schedule_snapshot(snapshot: Dict[str, Any]) -> None:
    """Persist schedule snapshot to disk."""
    try:
        with open(SCHEDULE_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"Failed to save schedule snapshot: {exc}")


def _format_minutes(total_minutes: int) -> str:
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0 and minutes > 0:
        return f"{hours}г {minutes}хв"
    if hours > 0:
        return f"{hours}г"
    return f"{minutes}хв"


def _total_minutes_for_day(queues: List[Dict[str, Any]]) -> int:
    total = 0
    for slot in queues:
        try:
            from_time = slot.get("from", "00:00")
            to_time = slot.get("to", "00:00")
            from_hour, from_min = map(int, from_time.split(":"))
            to_hour, to_min = map(int, to_time.split(":"))
            start = from_hour * 60 + from_min
            end = to_hour * 60 + to_min
            if end <= start:
                end += 24 * 60
            total += end - start
        except Exception:
            continue
    return total


def _format_day_slots(queues: List[Dict[str, Any]]) -> List[str]:
    """Format outage slots for human-friendly alert message."""
    if not queues:
        return ["  ✅ Без відключень"]

    lines: List[str] = []
    for slot in queues:
        shutdown_hours = slot.get("shutdownHours", "")
        from_time = slot.get("from", "")
        to_time = slot.get("to", "")
        try:
            from_hour, from_min = map(int, from_time.split(":"))
            to_hour, to_min = map(int, to_time.split(":"))
            start = from_hour * 60 + from_min
            end = to_hour * 60 + to_min
            if end <= start:
                end += 24 * 60
            duration_minutes = end - start
            duration_str = _format_minutes(duration_minutes)
            lines.append(f"  • {shutdown_hours} ({duration_str})")
        except Exception:
            lines.append(f"  • {shutdown_hours}")
    return lines


def _parse_event_date(event_date: str):
    try:
        return datetime.fromisoformat(event_date).date()
    except Exception:
        return None


def _filter_future_or_today(snapshot: Dict[str, Any], today) -> Dict[str, Any]:
    """Drop past dates to avoid false alerts about previous days."""
    filtered: Dict[str, Any] = {}
    for event_date, payload in snapshot.items():
        parsed = _parse_event_date(event_date)
        if parsed is not None and parsed < today:
            continue
        filtered[event_date] = payload
    return filtered


def _notify_schedule_changes_if_needed(raw_data: List[Dict[str, Any]]) -> None:
    """Detect daily schedule changes and send Telegram alert once per change."""
    if not CHAT_ID or not BOT_TOKEN:
        return

    today_eet = datetime.now(EET).date()

    current_snapshot: Dict[str, Any] = {}
    for day in raw_data:
        event_date = day.get("eventDate")
        if not event_date:
            continue
        queues = day.get("queues", {}).get(QUEUE_NUMBER, [])
        current_snapshot[event_date] = {
            "queues": queues,
            "createdAt": day.get("createdAt"),
            "scheduleApprovedSince": day.get("scheduleApprovedSince"),
        }

    current_snapshot = _filter_future_or_today(current_snapshot, today_eet)

    previous_snapshot = _load_schedule_snapshot()
    previous_snapshot = _filter_future_or_today(previous_snapshot, today_eet)

    # On first run, just store the snapshot without notifying.
    if not previous_snapshot:
        _save_schedule_snapshot(current_snapshot)
        return

    changed_days: List[str] = []

    # Check for changed or new dates - compare only the queues, not metadata timestamps
    for event_date, payload in current_snapshot.items():
        prev_payload = previous_snapshot.get(event_date)
        if prev_payload is None:
            # New date added
            changed_days.append(event_date)
        elif payload.get("queues") != prev_payload.get("queues"):
            # Actual schedule changed
            changed_days.append(event_date)

    # Check for removed dates
    for event_date in previous_snapshot.keys():
        if event_date not in current_snapshot:
            changed_days.append(event_date)

    if not changed_days:
        return

    # Compose alert message
    changed_days_sorted = sorted(set(changed_days))
    lines = ["⚠️ Графік відключень оновлено"]
    for day in changed_days_sorted:
        queues = current_snapshot.get(day, {}).get("queues", [])
        total_minutes = _total_minutes_for_day(queues)
        duration_str = _format_minutes(total_minutes) if total_minutes > 0 else "—"
        lines.append(f"📅 {day}: новий графік (всього {duration_str})")
        lines.extend(_format_day_slots(queues))

    try:
        send_message(CHAT_ID, "\n".join(lines))
    except Exception as exc:
        print(f"Failed to send schedule change notification: {exc}")
    finally:
        _save_schedule_snapshot(current_snapshot)


def _check_schedule_updates_periodic() -> None:
    """Poll schedule API and notify if it changed."""
    try:
        url = f"https://be-svitlo.oe.if.ua/schedule-by-queue?queue={QUEUE_NUMBER}"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        req.add_header('Accept', 'application/json, text/plain, */*')

        with urllib.request.urlopen(req, timeout=10, context=UNVERIFIED_CTX) as response:
            data = json.loads(response.read().decode('utf-8'))

        if data:
            _notify_schedule_changes_if_needed(data)
    except Exception as exc:
        print(f"Periodic schedule check failed: {exc}")


def edit_message_text(chat_id: int | str, message_id: int, text: str, parse_mode: str = "HTML", buttons: Optional[Dict[str, Any]] = None) -> bool:
    """Edit an existing message via Telegram bot API. Returns True if successful."""
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    params = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    
    if buttons:
        params["reply_markup"] = json.dumps(buttons)

    data = urllib.parse.urlencode(params).encode("utf-8")

    url = f"{API_URL}/editMessageText"
    try:
        with urllib.request.urlopen(  # noqa: S310
            url,
            data=data,
            timeout=10,
            context=UNVERIFIED_CTX,
        ) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
            if not result.get("ok"):
                error_desc = result.get("description", "")
                # Ignore "message is not modified" error - content is the same
                if "message is not modified" not in error_desc.lower():
                    print(f"Edit message failed: {result}")
                    return False
            return True
    except Exception as exc:
        print(f"Edit message error: {exc}")
        return False


def answer_callback_query(callback_id: str, text: str = "", show_alert: bool = False) -> None:
    """Answer a callback query (button click) notification."""
    if not BOT_TOKEN:
        return

    params = {
        "callback_query_id": callback_id,
        "text": text,
        "show_alert": "true" if show_alert else "false",
    }

    data = urllib.parse.urlencode(params).encode("utf-8")

    url = f"{API_URL}/answerCallbackQuery"
    try:
        with urllib.request.urlopen(  # noqa: S310
            url,
            data=data,
            timeout=10,
            context=UNVERIFIED_CTX,
        ) as resp:
            body = resp.read().decode("utf-8")
            json.loads(body)
    except Exception as exc:
        print(f"Answer callback error: {exc}")


def to_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        if isinstance(val, str) and val.strip().upper() == "N/A":
            return None
        return float(val)
    except Exception:
        return None


def all_na(payload: Dict[str, Any], keys: List[str]) -> bool:
    """
    Повертає True, якщо для всіх перелічених ключів значення == 'N/A' або None.
    Якщо жодного ключа не знайшли у payload – повертає False.
    """
    has_any = False
    for k in keys:
        if k not in payload:
            continue
        has_any = True
        v = payload.get(k)
        if v is None:
            # Окей, це теж "немає значення"
            continue
        if isinstance(v, str) and v.strip().upper() == "N/A":
            # Теж "немає значення"
            continue
        # якщо хоч одне значення не N/A і не None -> вже не "все N/A"
        return False
    return has_any


def is_grid_up(payload: Dict[str, Any]) -> bool:
    """Мережа вважається є, якщо є потужність або напруга > порогу."""
    grid_power = to_float(payload.get("grid_power"))
    grid_voltage = to_float(payload.get("grid_voltage"))

    if grid_power is not None:
        return grid_power > 10
    if grid_voltage is not None:
        return grid_voltage > 50
    return False


def get_tuya_token() -> Optional[str]:
    """Get Tuya access token. Returns None if Tuya not available."""
    if not TUYA_AVAILABLE:
        return None
    try:
        return tuya.get_token()
    except Exception as exc:
        print(f"Failed to get Tuya token: {exc}")
        return None


def get_tuya_devices_status(token: str) -> str:
    """Get formatted status of all Tuya devices."""
    if not TUYA_AVAILABLE or not token:
        return ""
    try:
        devices = tuya.list_devices(token)
        if not devices:
            return ""
        
        device_lines = []
        for dev in devices:
            dev_id = dev.get("id")
            name = dev.get("name", dev_id)
            if not dev_id:
                continue
            try:
                status = tuya.get_device_status(token, dev_id)
                status_items = status if isinstance(status, list) else status.get("status", [])
                switch_on = False
                for item in status_items:
                    if item.get("code") == "switch_1":
                        switch_on = item.get("value", False)
                        break
                state_str = "✅ ON" if switch_on else "❌ OFF"
                device_lines.append(f"• {name}: {state_str}")
            except Exception:
                device_lines.append(f"• {name}: ⚠️ (недоступно)")
        
        if not device_lines:
            return ""
        
        header = "🏠 <b>Smart Devices</b>"
        content = "\n".join(device_lines)
        return f"{header}\n{content}"
    except Exception as exc:
        print(f"Failed to get Tuya devices: {exc}")
        return ""


def turn_off_tuya_devices(token: str) -> str:
    """Turn off all Tuya devices and return status message."""
    if not TUYA_AVAILABLE or not token:
        return ""
    try:
        devices = tuya.list_devices(token)
        if not devices:
            return ""
        action_lines = []
        for dev in devices:
            dev_id = dev.get("id")
            name = dev.get("name", dev_id)
            if not dev_id:
                continue
            try:
                tuya.turn_device_off(token, dev_id)
                action_lines.append(f"✓ {name} вимкнено")
            except Exception as exc:
                action_lines.append(f"✗ {name} - помилка: {exc}")
        
        if not action_lines:
            return ""
        
        header = "<b>🔌 Вимикаю Tuya пристрої:</b>"
        content = "\n".join(action_lines)
        
        # Add current status after turning off
        time.sleep(1)  # Give devices time to update
        status_msg = get_tuya_devices_status(token)
        
        result = f"{header}\n{content}"
        if status_msg:
            result += f"\n\n{status_msg}"
        return result
    except Exception as exc:
        print(f"Failed to turn off devices: {exc}")
        return ""

def turn_on_tuya_devices(token: str) -> str:
    """Turn on all Tuya devices and return status message."""
    if not TUYA_AVAILABLE or not token:
        return ""
    try:
        devices = tuya.list_devices(token)
        if not devices:
            return ""
        action_lines = []
        for dev in devices:
            dev_id = dev.get("id")
            name = dev.get("name", dev_id)
            if not dev_id:
                continue
            try:
                tuya.send_device_command(token, dev_id, [{"code": "switch_1", "value": True}])
                action_lines.append(f"✓ {name} увімкнено")
            except Exception as exc:
                action_lines.append(f"✗ {name} - помилка: {exc}")
        
        if not action_lines:
            return ""
        
        header = "<b>🔌 Вмикаю Tuya пристрої:</b>"
        content = "\n".join(action_lines)
        
        # Add current status after turning on
        time.sleep(1)  # Give devices time to update
        status_msg = get_tuya_devices_status(token)
        
        result = f"{header}\n{content}"
        if status_msg:
            result += f"\n\n{status_msg}"
        return result
    except Exception as exc:
        print(f"Failed to turn on devices: {exc}")
        return ""


def get_battery_soc(payload: Dict[str, Any]) -> Optional[float]:
    """Повертає SOC батареї як float, якщо є."""
    return to_float(payload.get("battery_soc"))


def battery_emoji(soc: float) -> str:
    """
    Повертає емодзі індикатор батареї по рівню заряду:
    >= 80%  -> 🔋🟢
    50–79%  -> 🔋🟡
    20–49%  -> 🔋🟠
    < 20%   -> 🔋🔴
    """
    if soc >= 80:
        return "🔋🟢"
    if soc >= 50:
        return "🔋🟡"
    if soc >= 20:
        return "🔋🟠"
    return "🔋🔴"


def get_electricity_schedule() -> str:
    """Fetch and format electricity schedule from be-svitlo API."""
    try:
        url = f"https://be-svitlo.oe.if.ua/schedule-by-queue?queue={QUEUE_NUMBER}"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        req.add_header('Accept', 'application/json, text/plain, */*')
        
        with urllib.request.urlopen(req, timeout=10, context=UNVERIFIED_CTX) as response:
            data = json.loads(response.read().decode('utf-8'))

        # Notify if schedule changed compared to stored snapshot
        try:
            _notify_schedule_changes_if_needed(data)
        except Exception as exc:
            print(f"Schedule change detection failed: {exc}")
        
        if not data or len(data) == 0:
            return ""
        
        # Check current time
        now = datetime.now(EET)
        
        all_sections = []
        
        # Process up to 2 days
        for day_data in data[:2]:
            event_date = day_data.get('eventDate', '')
            
            # Safely get queues, handle missing queue number
            queues_dict = day_data.get('queues', {})
            if not queues_dict:
                queues = []
            else:
                queues = queues_dict.get(str(QUEUE_NUMBER), []) or queues_dict.get(QUEUE_NUMBER, [])
            
            # Parse eventDate (format: "18.01.2026")
            try:
                day, month, year = map(int, event_date.split('.'))
                event_datetime = datetime(year, month, day, tzinfo=EET)
            except:
                event_datetime = datetime(now.year, now.month, now.day, tzinfo=EET)
            
            if not queues or len(queues) == 0:
                section = f"📅 <b>{event_date}</b>\n✅ Відключень не заплановано"
            else:
                schedule_lines = []
                total_minutes = 0
                
                for slot in queues:
                    shutdown_hours = slot.get('shutdownHours', '')
                    from_time = slot.get('from', '')
                    to_time = slot.get('to', '')
                    
                    # Calculate duration for summary
                    try:
                        from_hour, from_min = map(int, from_time.split(':'))
                        to_hour, to_min = map(int, to_time.split(':'))
                        from_minutes = from_hour * 60 + from_min
                        to_minutes = to_hour * 60 + to_min
                        
                        # Handle times spanning midnight
                        if to_minutes <= from_minutes:
                            to_minutes += 24 * 60
                        
                        total_minutes += to_minutes - from_minutes
                    except:
                        pass
                    
                    # Check if outage is active now
                    try:
                        from_hour, from_min = map(int, from_time.split(':'))
                        to_hour, to_min = map(int, to_time.split(':'))
                        from_dt = datetime(event_datetime.year, event_datetime.month, event_datetime.day, from_hour, from_min, tzinfo=EET)
                        to_dt = datetime(event_datetime.year, event_datetime.month, event_datetime.day, to_hour, to_min, tzinfo=EET)
                        
                        if from_dt <= now <= to_dt:
                            schedule_lines.append(f"🔴 {shutdown_hours} (ЗАРАЗ)")
                        elif now < from_dt:
                            schedule_lines.append(f"⏰ {shutdown_hours}")
                        else:
                            schedule_lines.append(f"✓ {shutdown_hours}")
                    except:
                        schedule_lines.append(f"⚠️ {shutdown_hours}")
                
                # Format total duration
                total_hours = total_minutes // 60
                total_mins = total_minutes % 60
                if total_hours > 0 and total_mins > 0:
                    duration_str = f"{total_hours}г {total_mins}хв"
                elif total_hours > 0:
                    duration_str = f"{total_hours}г"
                else:
                    duration_str = f"{total_mins}хв"
                
                schedule_text = "<code>" + "\n".join(schedule_lines) + "</code>"
                section = f"📅 <b>{event_date}</b> (Всього: {duration_str})\n{schedule_text}"
            
            all_sections.append(section)
        
        return "\n\n".join(all_sections)
    
    except Exception as e:
        print(f"Failed to fetch electricity schedule: {e}")
        return ""


def build_status_text() -> str:
    """Текст для /status — мережа + споживання + батарея."""
    payload, error, ts = get_latest_reading()

    if error:
        return f"Статус недоступний: {error}"

    if not payload:
        return "Дані ще не отримано."

    # --- Якщо все важливе N/A -> вважаємо, що зв'язок втрачено ---
    if all_na(
        payload,
        [
            "grid_voltage",
            "grid_power",
            "ac_output_power",
            "battery_voltage",
            "battery_current",
            "battery_soc",
        ],
    ):
        return (
            "<b>🚨 Мережі немає</b> <code>(OFFLINE)</code>\n\n"
            "Дані з інвертора зараз недоступні (усі основні показники N/A).\n"
            f"<i>Остання спроба оновлення: {ts or 'невідомо'}</i>"
        )

    net_state = (
        "<b>✅ Мережа є</b> <code>(ONLINE)</code>" if is_grid_up(payload) else "<b>🚨 Мережі немає</b> <code>(OFFLINE)</code>"
    )

    # Батарея
    soc = get_battery_soc(payload)
    
    # Форматування показників у <code> блоці
    metrics_lines = []
    
    if soc is not None:
        metrics_lines.append(f"🔋 Battery SOC: {soc:.0f} %")
    
    gv = payload.get("grid_voltage")
    if gv is not None:
        metrics_lines.append(f"⚡ Grid Volt  : {gv} V")
    
    gp = payload.get("grid_power")
    if gp is not None:
        metrics_lines.append(f"⚡ Grid Power : {gp} W")
    
    ac = payload.get("ac_output_power")
    if ac is not None:
        metrics_lines.append(f"🔌 AC Load    : {ac} W")
    
    bv = payload.get("battery_voltage")
    if bv is not None:
        metrics_lines.append(f"🔋 Batt Volt  : {bv} V")
    
    bc = payload.get("battery_current")
    if bc is not None:
        metrics_lines.append(f"🔄 Batt Curr  : {bc} A")
    
    metrics_block = "<code>" + "\n".join(metrics_lines) + "</code>" if metrics_lines else ""
    
    parts: List[str] = [net_state]
    if metrics_block:
        parts.append(metrics_block)

    # Інтелектуальне попередження по батареї
    if soc is not None and soc < 20:
        parts.append(
            "\n<b>‼️ Увага: заряд батареї &lt; 20%</b>\n"
            "Рекомендація: максимально обмежити споживання, "
            "не вмикати потужні прилади."
        )

    # Tuya devices status
    if TUYA_AVAILABLE:
        tuya_token = get_tuya_token()
        if tuya_token:
            tuya_status = get_tuya_devices_status(tuya_token)
            if tuya_status:
                parts.append("\n" + tuya_status)

    # Electricity schedule
    schedule_text = get_electricity_schedule()
    if schedule_text:
        parts.append("\n" + schedule_text)
    
    # Timestamp
    parts.append(f"<i>Останнє оновлення: {ts or 'невідомо'}</i>")

    return "\n".join(parts)


def build_battery_text() -> str:
    """Текст для /battery — детальний статус батареї."""
    payload, error, ts = get_latest_reading()

    if error:
        return f"Статус батареї недоступний: {error}"

    if not payload:
        return "Дані по батареї ще не отримано."

    # Якщо по батареї всі ключові поля N/A -> теж вважаємо, що немає зв'язку
    if all_na(payload, ["battery_voltage", "battery_current", "battery_soc"]):
        return (
            "Зв'язок з інвертором втрачено.\n"
            "Дані по батареї зараз недоступні (усі показники N/A).\n"
            f"Остання спроба оновлення: {ts or 'невідомо'}"
        )

    soc = get_battery_soc(payload)
    parts: List[str] = ["🔋 Статус батареї"]

    if soc is not None:
        parts.append(f"Рівень заряду: {soc:.0f}% {battery_emoji(soc)}")
    else:
        parts.append("Рівень заряду: невідомо")

    bv = payload.get("battery_voltage")
    bc = payload.get("battery_current")

    if bv is not None:
        parts.append(f"Напруга батареї: {bv}")
    if bc is not None:
        parts.append(f"Струм батареї: {bc}")

    parts.append(f"Останнє оновлення: {ts or 'невідомо'}")

    if soc is not None and soc < 20:
        parts.append(
            "\n‼️ Низький рівень заряду (< 20%).\n"
            "Будь ласка, по можливості вимикайте непотрібні прилади "
            "та уникайте використання потужної техніки."
        )

    return "\n".join(parts)


def build_schedule_text() -> str:
    """Текст для /schedule — детальний графік відключень."""
    try:
        url = f"https://be-svitlo.oe.if.ua/schedule-by-queue?queue={QUEUE_NUMBER}"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        req.add_header('Accept', 'application/json, text/plain, */*')
        
        with urllib.request.urlopen(req, timeout=10, context=UNVERIFIED_CTX) as response:
            data = json.loads(response.read().decode('utf-8'))
        
        if not data or len(data) == 0:
            return f"📅 Графік відключень для черги {QUEUE_NUMBER}\n\nДані недоступні"
        
        now = datetime.now(EET)
        
        parts = [f"📅 Графік відключень для черги {QUEUE_NUMBER}\n"]
        
        # Process each day in schedule
        for day_data in data[:3]:  # Show max 3 days
            event_date = day_data.get('eventDate', '')
            
            # Skip if no event date
            if not event_date:
                continue
            
            # Safely get queues, handle missing queue number
            queues_dict = day_data.get('queues', {})
            if not queues_dict:
                queues = []
            else:
                queues = queues_dict.get(str(QUEUE_NUMBER), []) or queues_dict.get(QUEUE_NUMBER, [])
            
            created_at = day_data.get('createdAt', '')
            
            parts.append(f"📆 {event_date}")
            
            # Parse eventDate (format: "18.01.2026")
            try:
                day, month, year = map(int, event_date.split('.'))
                event_datetime = datetime(year, month, day, tzinfo=EET)
            except:
                event_datetime = datetime(now.year, now.month, now.day, tzinfo=EET)
            
            if not queues or len(queues) == 0:
                parts.append("  ✅ Відключень не заплановано\n")
            else:
                for slot in queues:
                    shutdown_hours = slot.get('shutdownHours', '')
                    from_time = slot.get('from', '')
                    to_time = slot.get('to', '')
                    
                    # Skip invalid slots
                    if not shutdown_hours or not from_time or not to_time:
                        continue
                    
                    # Check if outage is active now
                    try:
                        from_hour, from_min = map(int, from_time.split(':'))
                        to_hour, to_min = map(int, to_time.split(':'))
                        from_dt = datetime(event_datetime.year, event_datetime.month, event_datetime.day, from_hour, from_min, tzinfo=EET)
                        to_dt = datetime(event_datetime.year, event_datetime.month, event_datetime.day, to_hour, to_min, tzinfo=EET)
                        
                        # Handle times spanning midnight
                        if to_dt <= from_dt:
                            to_dt = to_dt.replace(day=to_dt.day + 1)
                        
                        duration = (to_hour * 60 + to_min) - (from_hour * 60 + from_min)
                        # Handle negative duration (midnight-spanning)
                        if duration <= 0:
                            duration += 24 * 60
                        hours = duration // 60
                        minutes = duration % 60
                        duration_str = f"{hours}г {minutes}хв" if hours > 0 else f"{minutes}хв"
                        
                        if event_date == now.strftime('%d.%m.%Y'):
                            if from_dt <= now <= to_dt:
                                parts.append(f"  🔴 {shutdown_hours} ({duration_str}) - ЗАРАЗ")
                            elif now < from_dt:
                                parts.append(f"  ⏰ {shutdown_hours} ({duration_str})")
                            else:
                                parts.append(f"  ✓ {shutdown_hours} ({duration_str}) - завершено")
                        else:
                            parts.append(f"  ⚠️ {shutdown_hours} ({duration_str})")
                    except:
                        parts.append(f"  ⚠️ {shutdown_hours}")
                parts.append("")
        
        if created_at:
            parts.append(f"🕐 Оновлено: {created_at}")
        
        return "\n".join(parts)
    
    except Exception as e:
        return f"📅 Графік відключень для черги {QUEUE_NUMBER}\n\n❌ Помилка завантаження: {e}"


def get_updates(offset: Optional[int]) -> List[Dict[str, Any]]:
    if not BOT_TOKEN:
        return []

    params: Dict[str, Any] = {"timeout": 20}
    if offset is not None:
        params["offset"] = offset

    url = f"{API_URL}/getUpdates?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(  # noqa: S310
            url,
            timeout=25,
            context=UNVERIFIED_CTX,
        ) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
    except Exception as exc:  # noqa: BLE001
        print(f"getUpdates failed: {exc}")
        return []

    if not data.get("ok"):
        print(f"getUpdates returned not ok: {data}")
        return []

    return data.get("result", [])


def extract_command(text: str) -> Optional[str]:
    """
    /status          -> /status
    /status@mybot    -> /status
    /status foo bar  -> /status
    """
    if not text:
        return None

    text = text.strip()
    if not text.startswith("/"):
        return None

    first = text.split()[0].lower()  # '/status@mybot'
    if "@" in first:
        first = first.split("@", 1)[0]
    return first


# ------------- Main loop -------------


def main() -> int:
    if not BOT_TOKEN:
        print("Missing TELEGRAM_BOT_TOKEN in environment/.env")
        return 1

    offset: Optional[int] = None
    previous_state: Optional[bool] = None
    last_grid_check = 0.0
    last_schedule_check = 0.0
    last_command_chat_id: Optional[int | str] = None

    while True:
        # --- 1) Обробка апдейтів / команд ---
        updates = get_updates(offset)

        for upd in updates:
            upd_id = upd.get("update_id")
            if upd_id is not None:
                offset = upd_id + 1

            # приват/групи: message/edited_message
            # канали: channel_post/edited_channel_post
            msg = (
                upd.get("message")
                or upd.get("edited_message")
                or upd.get("channel_post")
                or upd.get("edited_channel_post")
            )
            if not msg:
                continue

            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            text = msg.get("text") or ""

            if chat_id is None:
                continue

            print(f"[UPDATE] chat_id={chat_id}, text={text!r}")

            cmd = extract_command(text)

            if cmd in ("/start", "/status"):
                last_command_chat_id = chat_id
                try:
                    send_message(chat_id, build_status_text(), buttons=get_status_buttons())
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed to send status: {exc}")

            elif cmd == "/battery":
                last_command_chat_id = chat_id
                try:
                    send_message(chat_id, build_battery_text())
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed to send battery status: {exc}")

            elif cmd == "/schedule":
                last_command_chat_id = chat_id
                try:
                    send_message(chat_id, build_schedule_text())
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed to send schedule: {exc}")

            elif cmd == "/chatid":
                last_command_chat_id = chat_id
                try:
                    send_message(chat_id, f"Ваш chat_id: {chat_id}")
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed to send chat_id: {exc}")
            
            # Handle callback queries (button clicks)
            callback_query = upd.get("callback_query")
            if callback_query:
                callback_id = callback_query.get("id")
                callback_data = callback_query.get("data")
                callback_chat_id = callback_query.get("from", {}).get("id")
                msg_id = callback_query.get("message", {}).get("message_id")
                
                print(f"[CALLBACK] chat_id={callback_chat_id}, data={callback_data}, msg_id={msg_id}")
                
                if callback_data == "refresh_status" and callback_chat_id:
                    try:
                        status_text = build_status_text()
                        # Edit existing message with new status
                        if msg_id:
                            success = edit_message_text(callback_chat_id, msg_id, status_text, buttons=get_status_buttons())
                            if success:
                                answer_callback_query(callback_id, "✅ Оновлено")
                            else:
                                answer_callback_query(callback_id, "Дані не змінились")
                        else:
                            send_message(callback_chat_id, status_text, buttons=get_status_buttons())
                            answer_callback_query(callback_id, "✅ Оновлено")
                    except Exception as exc:  # noqa: BLE001
                        print(f"Failed to refresh status: {exc}")
                        answer_callback_query(callback_id, "❌ Помилка оновлення", show_alert=True)
                
                elif callback_data == "bot_menu" and callback_chat_id:
                    try:
                        menu_text = "⚙️ <b>Меню бота</b>\n\n/status - Статус мережі\n/battery - Статус батареї\n/schedule - Графік відключень"
                        answer_callback_query(callback_id, "Меню")
                    except Exception as exc:  # noqa: BLE001
                        print(f"Failed to show menu: {exc}")

        # --- 2) Періодична перевірка мережі + автопостинг ---
        now = time.time()
        if now - last_grid_check >= POLL_INTERVAL:
            payload, error, ts = get_latest_reading()
            if payload and not error:
                # тут, якщо все N/A, is_grid_up поверне False (бо to_float -> None)
                grid_up = is_grid_up(payload)

                if previous_state is None:
                    # перший запуск — просто запам'ятати стан
                    previous_state = grid_up
                elif grid_up != previous_state:
                    # Стан мережі змінився -> формуємо алерт+повний статус
                    target_chat = CHAT_ID or last_command_chat_id
                    
                    if target_chat is None:
                        print(
                            "Стан мережі змінився, але немає TELEGRAM_CHAT_ID "
                            "і ще жодного чату з командами – нікуди слати алерт."
                        )
                    else:
                        # Обробити Tuya дії ПЕРШИМИ і відправити їх
                        if grid_up:
                            header = "✅ Мережу відновлено"
                            # Turn on Tuya devices if configured
                            if TUYA_TURN_ON_ON_GRID_BACK and TUYA_AVAILABLE:
                                tuya_token = get_tuya_token()
                                if tuya_token:
                                    tuya_action = turn_on_tuya_devices(tuya_token)
                                    if tuya_action:
                                        try:
                                            send_message(target_chat, tuya_action)
                                        except Exception as exc:  # noqa: BLE001
                                            print(f"Failed to send tuya action: {exc}")
                        else:
                            header = (
                                "⚠️ Мережа зникла!\n"
                                "‼️ Увага: будь ласка, не користуйтеся духовкою, "
                                "пральною машиною, електрочайником та іншими потужними приладами."
                            )
                            # Turn off Tuya devices if configured
                            if TUYA_TURN_OFF_ON_POWER_LOSS and TUYA_AVAILABLE:
                                tuya_token = get_tuya_token()
                                if tuya_token:
                                    tuya_action = turn_off_tuya_devices(tuya_token)
                                    if tuya_action:
                                        try:
                                            send_message(target_chat, tuya_action)
                                        except Exception as exc:  # noqa: BLE001
                                            print(f"Failed to send tuya action: {exc}")

                        # Потім відправити повний статус зі заголовком
                        status_text = build_status_text()
                        alert_text = f"{header}\n\n{status_text}"

                        try:
                            send_message(target_chat, alert_text, buttons=get_status_buttons())
                        except Exception as exc:  # noqa: BLE001
                            print(f"Failed to send grid alert: {exc}")

                    previous_state = grid_up

            last_grid_check = now

        # 3) Periodic schedule change polling (independent of user commands)
        if now - last_schedule_check >= SCHEDULE_CHECK_INTERVAL:
            _check_schedule_updates_periodic()
            last_schedule_check = now

        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
