from __future__ import annotations

import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from data_store import get_latest_reading

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "inverter.db")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # optional, куди слати алерти
POLL_INTERVAL = int(os.getenv("BOT_POLL_INTERVAL", "10"))

if BOT_TOKEN:
    API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
else:
    API_URL = ""

# WARNING: вимикає перевірку TLS (як у твоєму середовищі на Windows)
UNVERIFIED_CTX = ssl._create_unverified_context()


# ------------- Helpers -------------


def send_message(chat_id: int | str, text: str) -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
        }
    ).encode("utf-8")

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
            "Зв'язок з інвертором втрачено.\n"
            "Дані з інвертора зараз недоступні (усі основні показники N/A).\n"
            f"Остання спроба оновлення: {ts or 'невідомо'}"
        )

    net_state = (
        "⚡ Мережа: Є (ONLINE)" if is_grid_up(payload) else "🚨 Мережі немає (OFFLINE)"
    )

    parts: List[str] = [
        net_state,
        f"Останнє оновлення: {ts or 'невідомо'}",
        "",
    ]

    # Батарея
    soc = get_battery_soc(payload)
    if soc is not None:
        parts.append(f"Заряд батареї: {soc:.0f}% {battery_emoji(soc)}")

    mapping = {
        "grid_voltage": "Напруга мережі",
        "grid_power": "Потужність мережі",
        "ac_output_power": "Споживання (AC Load)",
        "battery_voltage": "Напруга батареї",
        "battery_current": "Струм батареї",
    }

    for key, label in mapping.items():
        if key in payload:
            parts.append(f"{label}: {payload[key]}")

    # Інтелектуальне попередження по батареї
    if soc is not None and soc < 20:
        parts.append(
            "\n‼️ Увага: заряд батареї < 20%.\n"
            "Рекомендація: максимально обмежити споживання, "
            "не вмикати потужні прилади."
        )

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
                    send_message(chat_id, build_status_text())
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed to send status: {exc}")

            elif cmd == "/battery":
                last_command_chat_id = chat_id
                try:
                    send_message(chat_id, build_battery_text())
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed to send battery status: {exc}")

            elif cmd == "/chatid":
                last_command_chat_id = chat_id
                try:
                    send_message(chat_id, f"Ваш chat_id: {chat_id}")
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed to send chat_id: {exc}")

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
                    if grid_up:
                        header = "✅ Мережу відновлено"
                    else:
                        header = (
                            "⚠️ Мережа зникла!\n"
                            "‼️ Увага: будь ласка, не користуйтеся духовкою, "
                            "пральною машиною, електрочайником та іншими потужними приладами."
                        )

                    # повний статус, той самий, що й на /status
                    status_text = build_status_text()
                    alert_text = f"{header}\n\n{status_text}"

                    # Куди слати:
                    # 1) TELEGRAM_CHAT_ID з env, якщо задано
                    # 2) або останній чат, звідки приходила команда
                    target_chat = CHAT_ID or last_command_chat_id
                    if target_chat is None:
                        print(
                            "Стан мережі змінився, але немає TELEGRAM_CHAT_ID "
                            "і ще жодного чату з командами – нікуди слати алерт."
                        )
                    else:
                        try:
                            send_message(target_chat, alert_text)
                        except Exception as exc:  # noqa: BLE001
                            print(f"Failed to send grid alert: {exc}")

                    previous_state = grid_up

            last_grid_check = now

        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
