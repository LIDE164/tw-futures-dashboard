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


def _send_telegram_photo(image_path, caption=""):
    if requests is None:
        return False, "requests 未安裝，略過 Telegram 圖片發送"
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False, "未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
    if not image_path or not os.path.exists(image_path):
        return False, "Telegram 圖片檔不存在"

    with open(image_path, "rb") as image_file:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": str(caption)[:1000]},
            files={"photo": (os.path.basename(image_path), image_file, "image/png")},
            timeout=20,
        )
    if resp.ok:
        return True, "telegram photo sent"
    return False, f"telegram photo failed: {resp.status_code} {resp.text[:200]}"


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


def format_whale_distribution_alert(event):
    targets = list(event.get("pullback_targets") or [])
    while len(targets) < 2:
        targets.append(0)
    delta_ratio = float(event.get("tx_delta_ratio") or 0) * 100
    level = max(1, min(3, int(event.get("level") or 1)))
    level_text = {1: "一級", 2: "二級", 3: "三級"}[level]
    delta_text = {1: "開始轉弱", 2: "明顯偏空", 3: "強烈偏空"}[level]
    first = float(event.get("first_support") or 0)
    second = float(event.get("second_support") or 0)
    tx_complete = float(event.get("tx_completeness_ratio") or 0) * 100
    tx_classified = float(event.get("tx_classification_ratio") or 0) * 100
    small_complete = float(event.get("small_completeness_ratio") or 0) * 100
    small_classified = float(event.get("small_classification_ratio") or 0) * 100
    body = (
        f"【疑似大戶倒貨｜{level_text}警報】\n"
        f"大台累積 Delta：{delta_text}（{float(event.get('tx_delta') or 0):+,.0f} 口／{delta_ratio:+.1f}%）\n"
        f"小台主動賣量：連續 {int(event.get('small_sell_streak') or 0)} 分鐘大於買量\n"
        f"目前價格：{float(event.get('current_price') or 0):,.0f}\n"
        f"盤中 VWAP：{float(event.get('session_vwap') or 0):,.0f}\n\n"
        f"資料完整率：大台 {tx_complete:.1f}%／小台 {small_complete:.1f}%\n"
        f"可分類率：大台 {tx_classified:.1f}%／小台 {small_classified:.1f}%\n\n"
        f"第一關（重要）：{first:,.0f}\n"
        "跌破後若下一根完整 15 分 K 站不回，空方動能提高。\n\n"
        f"第二關（更重要）：{second:,.0f}\n"
        f"若再失守，可能依序回測 {float(targets[0]):,.0f}、{float(targets[1]):,.0f}。\n\n"
        f"判斷：{event.get('judgement') or '賣壓正在增加。'}\n"
        f"建議：{event.get('suggestion') or '停止追多，等待關鍵價位確認。'}\n\n"
        f"逐筆更新：{event.get('last_tick_at') or '--'}\n"
        "提醒性質：逐筆量流推估，不代表已確認特定大戶交易。"
    )
    return f"疑似大戶倒貨｜{level_text}警報", body


def dispatch_whale_distribution(event):
    session_key = str(event.get("session_key") or datetime.now().date().isoformat())
    level = max(1, min(3, int(event.get("level") or 1)))
    episode = int(event.get("episode") or 1)
    event_type = f"WHALE_DISTRIBUTION_L{level}"
    alert_key = f"WHALE_DISTRIBUTION:{session_key}:E{episode}:L{level}"
    title, body = format_whale_distribution_alert(event)
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
    save_alert(
        {
            "alert_key": f"{alert_key}:delivery",
            "event_type": f"{event_type}_DELIVERY",
            "title": title,
            "body": body,
            "status": "sent" if sent else "stored",
            "detail": detail,
        }
    )
    print(body)
    return True, detail


def dispatch_research_report(report, body):
    report_date = str(report.get("report_date") or datetime.now().date().isoformat())
    alert_key = f"DAILY_RESEARCH:{report_date}"
    title = "微型臺指收盤研究報告"
    inserted = save_alert(
        {
            "alert_key": alert_key,
            "event_type": "DAILY_RESEARCH",
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
    save_alert(
        {
            "alert_key": f"{alert_key}:delivery",
            "event_type": "DAILY_RESEARCH_DELIVERY",
            "title": title,
            "body": body,
            "status": "sent" if sent else "stored",
            "detail": detail,
        }
    )
    print(body)
    return True, detail


def dispatch_preopen_briefing(briefing, body, image_path=None):
    session_key = str(briefing.get("session_key") or "")
    alert_key = f"PREOPEN:{session_key}"
    title = f"微型臺指{briefing.get('session_label', '')}開盤前簡報"
    inserted = save_alert(
        {
            "alert_key": alert_key,
            "event_type": "PREOPEN_BRIEFING",
            "title": title,
            "body": body,
            "status": "created",
            "detail": "dedupe accepted",
        }
    )
    if not inserted:
        return False, "duplicate alert skipped"

    if image_path:
        caption = (
            f"微型臺指{briefing.get('session_label', '')}開盤前交易地圖\n"
            f"方向：{briefing.get('direction')}｜評分：{briefing.get('score')} {briefing.get('label')}\n"
            "請看圖片中的關鍵價位與三種劇本；開盤後等待第一根完整 15 分 K 確認。"
        )
        sent, detail = _send_telegram_photo(image_path, caption)
    else:
        sent, detail = _send_telegram(body)
    if not sent:
        sent, detail = _send_telegram(body)
    if not sent:
        sent, detail = _send_webhook(title, body)
    save_alert(
        {
            "alert_key": f"{alert_key}:delivery",
            "event_type": "PREOPEN_BRIEFING_DELIVERY",
            "title": title,
            "body": body,
            "status": "sent" if sent else "stored",
            "detail": detail,
        }
    )
    print(body)
    return True, detail


def dispatch_hourly_analysis(analysis, body, image_path=None):
    hour_key = str(analysis.get("hour_key") or "")
    alert_key = f"HOURLY_ANALYSIS:{hour_key}"
    is_flow_report = analysis.get("report_mode") == "hourly_flow"
    title = "微型臺指｜過去一小時量流統計" if is_flow_report else "微型臺指每小時盤中分析"
    inserted = save_alert(
        {
            "alert_key": alert_key,
            "event_type": "HOURLY_ANALYSIS",
            "title": title,
            "body": body,
            "status": "created",
            "detail": "dedupe accepted",
        }
    )
    if not inserted:
        return False, "duplicate alert skipped"

    if image_path:
        model = analysis.get("scenario_model") or {}
        probabilities = model.get("probabilities") or {}
        caption = (
            "微型臺指每小時盤中分析\n"
            f"資料截止：{analysis.get('last_bar_time')}\n"
            f"狀態：{analysis.get('direction')}｜分數 {analysis.get('score')}\n"
            f"偏多 {probabilities.get('bull', 0)}%｜震盪 {probabilities.get('range', 0)}%｜"
            f"轉弱 {probabilities.get('bear', 0)}%\n"
            "圖片為策略研究摘要，不是自動下單指令。"
        )
        sent, detail = _send_telegram_photo(image_path, caption)
    else:
        sent, detail = _send_telegram(body)
    if not sent:
        sent, detail = _send_telegram(body)
    if not sent:
        sent, detail = _send_webhook(title, body)
    save_alert(
        {
            "alert_key": f"{alert_key}:delivery",
            "event_type": "HOURLY_ANALYSIS_DELIVERY",
            "title": title,
            "body": body,
            "status": "sent" if sent else "stored",
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
