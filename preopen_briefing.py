import pandas as pd


SESSION_LABELS = {
    "day": ("日盤", "08:45"),
    "night": ("夜盤", "15:00"),
}


def _number(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


def _optional(value, digits=0, suffix=""):
    if value in (None, ""):
        return "無資料"
    return f"{_number(value):,.{digits}f}{suffix}"


def _latest_bar_time(bars):
    if bars is None or bars.empty or "ts" not in bars.columns:
        return "無資料"
    timestamp = pd.to_datetime(bars["ts"].iloc[-1], errors="coerce")
    return "無資料" if pd.isna(timestamp) else timestamp.strftime("%Y/%m/%d %H:%M")


def build_preopen_briefing(
    session,
    session_key,
    realtime,
    bars,
    tech_data,
    score,
    label,
    reasons,
    action,
    message,
    public_data,
    broker,
    stop_loss_points,
    take_profit_points,
    commission_per_side=20,
    allow_long=True,
    allow_short=False,
    quality_reasons=None,
):
    session_label, open_time = SESSION_LABELS.get(session, (session, ""))
    price = _number(realtime.get("current_price"))
    bid = _number(realtime.get("bid_price"))
    ask = _number(realtime.get("ask_price"))
    stop_loss_points = _number(stop_loss_points)
    take_profit_points = _number(take_profit_points)

    if broker.position > 0:
        direction = "持有模擬多單｜注意平倉" if action == "CLOSE_LONG" else "持有模擬多單｜續抱觀察"
        entry_price = _number(broker.entry_price)
        stop_price = _number(broker.stop_loss_price)
        take_price = _number(broker.take_profit_price)
    elif broker.position < 0:
        direction = "持有模擬空單｜注意回補" if action == "CLOSE_SHORT" else "持有模擬空單｜續抱觀察"
        entry_price = _number(broker.entry_price)
        stop_price = _number(broker.stop_loss_price)
        take_price = _number(broker.take_profit_price)
    elif action == "BUY_LONG":
        direction = "偏多觀察" if allow_long else "偏多，但多單警報停用"
        entry_price = ask or price
        stop_price = entry_price - stop_loss_points
        take_price = entry_price + take_profit_points
    elif action == "SELL_SHORT":
        direction = "偏空觀察" if allow_short else "偏空，但空單警報停用"
        entry_price = bid or price
        stop_price = entry_price + stop_loss_points
        take_price = entry_price - take_profit_points
    else:
        direction = "觀望"
        entry_price = 0.0
        stop_price = 0.0
        take_price = 0.0

    risk_points = abs(entry_price - stop_price) if entry_price and stop_price else 0.0
    estimated_risk = risk_points * 10 + _number(commission_per_side) * 2
    mtx = public_data.get("mtx_net", {})
    pc_ratio = public_data.get("pc_ratio", {})
    option_levels = public_data.get("option_levels", {})
    institutional = public_data.get("txf_institutional", {})

    return {
        "session": session,
        "session_key": session_key,
        "session_label": session_label,
        "open_time": open_time,
        "contract_code": realtime.get("contract_code", ""),
        "delivery_date": realtime.get("delivery_date", ""),
        "last_bar_time": _latest_bar_time(bars),
        "last_price": price,
        "bid_price": bid,
        "ask_price": ask,
        "score": int(score),
        "label": label,
        "action": action,
        "direction": direction,
        "message": message,
        "entry_price": entry_price,
        "stop_loss_price": stop_price,
        "take_profit_price": take_price,
        "risk_points": risk_points,
        "estimated_risk": estimated_risk,
        "trend_15m": tech_data.get("15分趨勢文字", "資料不足"),
        "trend_60m": tech_data.get("60分趨勢文字", "資料不足"),
        "adx": _number(tech_data.get("ADX")),
        "volume_ratio": _number(tech_data.get("量比")),
        "risk_environment": tech_data.get("風險環境", "資料不足"),
        "reasons": list(reasons or [])[:3],
        "quality_reasons": list(quality_reasons or [])[:3],
        "mtx_net_oi": mtx.get("net_oi"),
        "mtx_long_short_ratio": mtx.get("long_short_ratio"),
        "mtx_source": mtx.get("source", "TAIFEX"),
        "pc_oi_ratio": pc_ratio.get("oi_ratio"),
        "pc_volume_ratio": pc_ratio.get("volume_ratio"),
        "pc_date": pc_ratio.get("date"),
        "call_pressure": option_levels.get("call_pressure"),
        "put_support": option_levels.get("put_support"),
        "option_expiry": option_levels.get("expiry"),
        "foreign_oi": institutional.get("外資"),
        "public_errors": list(public_data.get("errors") or []),
    }


def format_preopen_briefing(briefing):
    reasons = briefing.get("reasons") or []
    quality = briefing.get("quality_reasons") or []
    reason_text = "\n".join(f"{index + 1}. {reason}" for index, reason in enumerate(reasons)) or "無明確方向原因"
    quality_text = "；".join(quality) if quality else "目前未發現額外技術阻擋條件"
    operation_prices = "開盤後重新計算"
    if briefing.get("entry_price"):
        operation_prices = (
            f"參考進場：{briefing['entry_price']:,.0f}\n"
            f"參考停損：{briefing['stop_loss_price']:,.0f}\n"
            f"參考停利：{briefing['take_profit_price']:,.0f}\n"
            f"每口預估風險：約 NT$ {briefing['estimated_risk']:,.0f}"
        )

    data_warning = ""
    if briefing.get("public_errors"):
        data_warning = "\n資料提醒：部分 TAIFEX 公開資料讀取失敗，請以頁面最新值為準。"

    return (
        f"【微型臺指{briefing.get('session_label')}開盤前簡報】\n"
        f"預計開盤：{briefing.get('open_time')}\n"
        f"契約：{briefing.get('contract_code') or '無資料'}｜到期：{briefing.get('delivery_date') or '無資料'}\n"
        f"最後完成 K：{briefing.get('last_bar_time')}\n"
        f"最後價格：{briefing.get('last_price', 0):,.0f}｜買一 {briefing.get('bid_price', 0):,.0f}｜賣一 {briefing.get('ask_price', 0):,.0f}\n\n"
        "操作卡\n"
        f"方向：{briefing.get('direction')}\n"
        f"評分：{briefing.get('score')}｜{briefing.get('label')}\n"
        f"15分趨勢：{briefing.get('trend_15m')}｜60分趨勢：{briefing.get('trend_60m')}\n"
        f"ADX：{briefing.get('adx', 0):.1f}｜量比：{briefing.get('volume_ratio', 0):.2f}｜"
        f"風險環境：{briefing.get('risk_environment')}\n"
        f"{operation_prices}\n"
        f"進場檢查：{quality_text}\n\n"
        "多空與籌碼背景\n"
        f"小台三大法人淨部位：{_optional(briefing.get('mtx_net_oi'), 0, ' 口')}\n"
        f"小台法人多空比：{_optional(briefing.get('mtx_long_short_ratio'), 2, '%')}\n"
        f"外資臺指期淨部位：{_optional(briefing.get('foreign_oi'), 0, ' 口')}\n"
        f"選擇權未平倉 P/C：{_optional(briefing.get('pc_oi_ratio'), 2, '%')}｜"
        f"成交 P/C：{_optional(briefing.get('pc_volume_ratio'), 2, '%')}\n"
        f"Call 壓力：{_optional(briefing.get('call_pressure'))}｜Put 支撐：{_optional(briefing.get('put_support'))}\n\n"
        f"主要原因\n{reason_text}"
        f"{data_warning}\n\n"
        "重要：這是開盤前預備計畫，不是直接下單指令。開盤跳空可能使價位失效，"
        "請等待開盤後第一根完整 15 分 K 再確認；系統不會自動送出真實委託。"
    )
