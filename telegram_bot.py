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

# WARNING: вимикає перевірку TLS (як у твоєму середовищі на Windows)
UNVERIFIED_CTX = ssl._create_unverified_context()


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


def edit_message_text(chat_id: int | str, message_id: int, text: str, parse_mode: str = "HTML", buttons: Optional[Dict[str, Any]] = None) -> None:
    """Edit an existing message via Telegram bot API."""
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
            data = json.loads(body)
            if not data.get("ok"):
                print(f"Edit message failed: {data}")
    except Exception as exc:
        print(f"Edit message error: {exc}")


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
        
        header = "📱 <b>Tuya</b>"
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
        return f"{header}\n{content}"
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
        return f"{header}\n{content}"
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
        
        if not data or len(data) == 0:
            return ""
        
        # Get today's schedule
        today = data[0]
        event_date = today.get('eventDate', '')
        queues = today.get('queues', {}).get(QUEUE_NUMBER, [])
        
        if len(queues) == 0:
            return f"📅 <b>Графік відключень</b> <code>({event_date})</code>\n✅ Відключень не заплановано"
        
        schedule_lines = []
        
        # Check current time to mark active outages
        now = datetime.now(EET)
        
        for slot in queues:
            shutdown_hours = slot.get('shutdownHours', '')
            from_time = slot.get('from', '')
            to_time = slot.get('to', '')
            
            # Check if outage is active now
            try:
                from_hour, from_min = map(int, from_time.split(':'))
                to_hour, to_min = map(int, to_time.split(':'))
                from_dt = datetime(now.year, now.month, now.day, from_hour, from_min)
                to_dt = datetime(now.year, now.month, now.day, to_hour, to_min)
                
                if from_dt <= now <= to_dt:
                    schedule_lines.append(f"🔴 {shutdown_hours} (ЗАРАЗ)")
                elif now < from_dt:
                    schedule_lines.append(f"⏰ {shutdown_hours}")
                else:
                    schedule_lines.append(f"✓ {shutdown_hours}")
            except:
                schedule_lines.append(f"⚠️ {shutdown_hours}")
        
        schedule_text = "<pre>" + "\n".join(schedule_lines) + "</pre>"
        header = f"📅 <b>Графік відключень</b> <code>({event_date})</code>"
        return f"{header}\n{schedule_text}"
    
    except Exception as e:
        print(f"Failed to fetch electricity schedule: {e}")
        return ""


def build_status_text() -> str:
    """Текст для /status — мережа + споживання + батарея."""
    payload, error, ts = get_latest_reading(DB_PATH)

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
    
    # Форматування показників у <pre> блоці
    metrics_lines = []
    
    if soc is not None:
        metrics_lines.append(f"🔋 Battery SOC   : {soc:.0f} %")
    
    gv = payload.get("grid_voltage")
    if gv is not None:
        metrics_lines.append(f"⚡ Grid Volt     : {gv} V")
    
    gp = payload.get("grid_power")
    if gp is not None:
        metrics_lines.append(f"⚡ Grid Power    : {gp} W")
    
    ac = payload.get("ac_output_power")
    if ac is not None:
        metrics_lines.append(f"🔌 AC Load      : {ac} W")
    
    bv = payload.get("battery_voltage")
    if bv is not None:
        metrics_lines.append(f"🔋 Batt Volt    : {bv} V")
    
    bc = payload.get("battery_current")
    if bc is not None:
        metrics_lines.append(f"🔄 Batt Curr    : {bc} A")
    
    metrics_block = "<pre>" + "\n".join(metrics_lines) + "</pre>" if metrics_lines else ""
    
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
                parts.append(tuya_status)

    # Electricity schedule
    schedule_text = get_electricity_schedule()
    if schedule_text:
        parts.append(schedule_text)
    
    # Timestamp
    parts.append(f"<i>Останнє оновлення: {ts or 'невідомо'}</i>")

    return "\n".join(parts)


def build_battery_text() -> str:
    """Текст для /battery — детальний статус батареї."""
    payload, error, ts = get_latest_reading(DB_PATH)

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
            queues = day_data.get('queues', {}).get(QUEUE_NUMBER, [])
            created_at = day_data.get('createdAt', '')
            
            parts.append(f"📆 {event_date}")
            
            if len(queues) == 0:
                parts.append("  ✅ Відключень не заплановано\n")
            else:
                for slot in queues:
                    shutdown_hours = slot.get('shutdownHours', '')
                    from_time = slot.get('from', '')
                    to_time = slot.get('to', '')
                    
                    # Check if outage is active now
                    try:
                        from_hour, from_min = map(int, from_time.split(':'))
                        to_hour, to_min = map(int, to_time.split(':'))
                        from_dt = datetime(now.year, now.month, now.day, from_hour, from_min)
                        to_dt = datetime(now.year, now.month, now.day, to_hour, to_min)
                        
                        duration = (to_hour * 60 + to_min) - (from_hour * 60 + from_min)
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
                            edit_message_text(callback_chat_id, msg_id, status_text, buttons=get_status_buttons())
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
            payload, error, ts = get_latest_reading(DB_PATH)
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

        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
