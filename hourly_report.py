from preopen_briefing import build_preopen_briefing


def build_hourly_analysis(
    hour_key,
    market_status,
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
    briefing = build_preopen_briefing(
        session=market_status.session,
        session_key=hour_key,
        realtime=realtime,
        bars=bars,
        tech_data=tech_data,
        score=score,
        label=label,
        reasons=reasons,
        action=action,
        message=message,
        public_data=public_data,
        broker=broker,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
        commission_per_side=commission_per_side,
        allow_long=allow_long,
        allow_short=allow_short,
        quality_reasons=quality_reasons,
    )
    briefing.update(
        {
            "report_mode": "hourly",
            "report_title": f"微型臺指 每小時盤中分析｜{market_status.label}",
            "hour_key": hour_key,
            "market_status": market_status.label,
        }
    )
    if action == "BUY_LONG" and quality_reasons:
        briefing["direction"] = "偏多訊號，進場條件未全數通過"
    elif action == "SELL_SHORT" and quality_reasons:
        briefing["direction"] = "偏空訊號，進場條件未全數通過"
    return briefing


def format_hourly_analysis(analysis):
    model = analysis.get("scenario_model") or {}
    probabilities = model.get("probabilities") or {}
    quality = analysis.get("quality_reasons") or []
    reasons = analysis.get("reasons") or []
    reason_text = "\n".join(f"- {item}" for item in reasons[:3]) or "- 技術理由資料不足"
    quality_text = "\n".join(f"- {item}" for item in quality[:3]) or "- 未發現額外阻擋條件"
    return (
        "【微型臺指每小時盤中分析】\n"
        f"時段：{analysis.get('market_status') or analysis.get('session_label')}\n"
        f"資料截止：{analysis.get('last_bar_time')}\n"
        f"契約：{analysis.get('contract_code') or '--'}｜價格 {analysis.get('last_price', 0):,.0f}\n"
        f"買一 {analysis.get('bid_price', 0):,.0f}｜賣一 {analysis.get('ask_price', 0):,.0f}\n\n"
        f"目前狀態：{analysis.get('direction')}\n"
        f"策略分數：{analysis.get('score')}｜{analysis.get('label')}\n"
        f"15 分：{analysis.get('trend_15m')}｜60 分：{analysis.get('trend_60m')}\n"
        f"ADX {analysis.get('adx', 0):.1f}｜量比 {analysis.get('volume_ratio', 0):.2f}\n\n"
        "三劇本機率\n"
        f"偏多 {probabilities.get('bull', 0)}%｜震盪 {probabilities.get('range', 0)}%｜"
        f"轉弱 {probabilities.get('bear', 0)}%\n"
        f"歷史樣本 {model.get('sample_size', 0)}｜Walk-forward 命中 "
        f"{model.get('walk_forward_accuracy', 0):.1f}%｜信心 {model.get('confidence', '低')}\n\n"
        f"主要理由\n{reason_text}\n\n"
        f"進場檢查\n{quality_text}\n\n"
        "提醒性質：每小時策略摘要，不是自動下單指令；價格觸發停損／停利仍由即時警報處理。"
    )
