import os
import argparse
from datetime import datetime

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

from storage import save_alert


def build_alert_key(event_type, contract_code, bar_time, action, entry_price=0):
    return f"{event_type}:{contract_code}:{bar_time}:{action}:{float(entry_price or 0):.0f}"


def format_signal_alert(signal, event_type="SIGNAL"):
    title = "微型臺指策略提醒"
    if event_type.startswith("EXIT"):
        title = "微型臺指平倉提醒"

    reasons = signal.get("reasons") or []
    reason_text = "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(reasons[:3])) or "無明確原因"
    body = (
        f"【{title}】\n"
        f"事件：{event_type}\n"
        f"契約：{signal.get('contract_code') or '無資料'}\n"
        f"訊號時間：{signal.get('bar_time') or '無資料'}\n"
        f"方向：{signal.get('action')}\n"
        f"分數：{signal.get('score')}｜{signal.get('label')}\n"
        f"目前價格：{float(signal.get('price') or 0):,.0f}\n"
        f"進場參考：{float(signal.get('entry_price') or 0):,.0f}\n"
        f"停損：{float(signal.get('stop_loss_price') or 0):,.0f}\n"
        f"停利：{float(signal.get('take_profit_price') or 0):,.0f}\n"
        f"主要原因：\n{reason_text}\n"
        f"提醒性質：模擬策略，請自行確認後手動操作。"
    )
    return title, body


def _send_telegram(body):
    if requests is None:
        return False, "requests 未安裝，略過 Telegram 發送"

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False, "未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": body},
        timeout=10,
    )
    if resp.ok:
        return True, "telegram sent"
    return False, f"telegram failed: {resp.status_code} {resp.text[:200]}"


def _send_webhook(title, body):
    if requests is None:
        return False, "requests 未安裝，略過 webhook 發送"

    webhook_url = os.getenv("ALERT_WEBHOOK_URL", "")
    if not webhook_url:
        return False, "未設定 ALERT_WEBHOOK_URL"

    resp = requests.post(webhook_url, json={"title": title, "text": body}, timeout=10)
    if resp.ok:
        return True, "webhook sent"
    return False, f"webhook failed: {resp.status_code} {resp.text[:200]}"


def _load_local_environment():
    try:
        import sinopac_api

        loader = getattr(sinopac_api, "load_environment", None)
        if loader:
            loader()
    except Exception:
        pass


def send_test_message():
    _load_local_environment()
    body = (
        "[TW Futures Dashboard]\n"
        "Telegram test message sent successfully.\n"
        f"Time: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        "If you see this, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are working."
    )
    return _send_telegram(body)


def dispatch_alert(signal, event_type="SIGNAL"):
    alert_key = build_alert_key(
        event_type,
        signal.get("contract_code", ""),
        signal.get("bar_time", ""),
        signal.get("action", ""),
        signal.get("entry_price", 0),
    )
    title, body = format_signal_alert(signal, event_type)

    inserted = save_alert(
        {
            "alert_key": alert_key,
            "event_type": event_type,
            "title": title,
            "body": body,
            "status": "created",
            "detail": "dedupe accepted",
        }
    )
    if not inserted:
        return False, "duplicate alert skipped"

    sent, detail = _send_telegram(body)
    if not sent:
        sent, detail = _send_webhook(title, body)

    status = "sent" if sent else "stored"
    save_alert(
        {
            "alert_key": f"{alert_key}:delivery",
            "event_type": f"{event_type}_DELIVERY",
            "title": title,
            "body": body,
            "status": status,
            "detail": detail,
        }
    )
    print(body)
    return True, detail


def main():
    parser = argparse.ArgumentParser(description="Alert delivery utilities")
    parser.add_argument("--test", action="store_true", help="Send a Telegram test message")
    args = parser.parse_args()

    if not args.test:
        parser.print_help()
        return 0

    sent, detail = send_test_message()
    print(detail)
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
