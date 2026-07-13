from datetime import datetime

from whale_monitor import derive_downside_levels


def _number(value, default=0.0):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return float(default)


def _product(flow_summary, root):
    return dict((flow_summary.get("products") or {}).get(root) or {})


def _flow_label(delta_ratio):
    ratio = float(delta_ratio or 0)
    if ratio <= -0.15:
        return "強賣方"
    if ratio <= -0.05:
        return "賣方"
    if ratio >= 0.15:
        return "強買方"
    if ratio >= 0.05:
        return "買方"
    return "中性"


def build_hourly_analysis(
    hour_key,
    market_status,
    realtime,
    bars,
    tech_data,
    flow_summary,
):
    realtime = dict(realtime or {})
    tech_data = dict(tech_data or {})
    flow_summary = dict(flow_summary or {})
    snapshot = dict(flow_summary.get("snapshot") or {})
    tx = _product(flow_summary, "TXF")
    mx = _product(flow_summary, "MXF")
    tm = _product(flow_summary, "TMF")
    events = dict(flow_summary.get("event_counts") or {})
    current_price = _number(realtime.get("current_price"))
    vwap = _number(snapshot.get("session_vwap")) or _number(realtime.get("vwap"))
    atr = _number(tech_data.get("ATR"))
    levels = derive_downside_levels(bars, max(current_price, vwap), atr)

    tx_ratio = _number(tx.get("delta_ratio"))
    small_ratio = _number(mx.get("delta_ratio")) if mx.get("classified_volume") else _number(tm.get("delta_ratio"))
    if events.get(3, 0) or (tx_ratio <= -0.08 and small_ratio < 0):
        direction = "賣方主導"
        judgement = "大台與小型商品量流同步偏空，優先防守，不追多。"
    elif events.get(2, 0) or tx_ratio <= -0.04:
        direction = "賣壓升溫"
        judgement = "主動賣量增加，但仍需配合 VWAP 與關鍵支撐確認。"
    elif tx_ratio >= 0.08 and small_ratio >= 0:
        direction = "買方主導"
        judgement = "大台與小型商品量流同步偏多，回測支撐時再觀察承接。"
    else:
        direction = "多空拉鋸"
        judgement = "量流沒有明確同向優勢，暫不因單一逐筆訊號追價。"

    coverage = int(flow_summary.get("coverage_minutes") or 0)
    data_status = "完整" if coverage >= 50 else f"部分資料（{coverage}/60 分鐘）"
    return {
        "report_mode": "hourly_flow",
        "report_title": "微型臺指｜過去一小時量流統計",
        "hour_key": hour_key,
        "market_status": market_status.label,
        "period_start": flow_summary.get("period_start", ""),
        "period_end": flow_summary.get("period_end", ""),
        "coverage_minutes": coverage,
        "data_status": data_status,
        "contract_code": realtime.get("contract_code") or "TMF近月",
        "current_price": current_price,
        "session_vwap": vwap,
        "products": {"TXF": tx, "MXF": mx, "TMF": tm},
        "event_counts": {int(key): int(value or 0) for key, value in events.items()},
        "direction": direction,
        "judgement": judgement,
        "first_support": levels["first_support"],
        "second_support": levels["second_support"],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _period_text(value):
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M")
    except (TypeError, ValueError):
        return "--:--"


def _format_product(root, values):
    buy = _number(values.get("buy_volume"))
    sell = _number(values.get("sell_volume"))
    delta = _number(values.get("delta"))
    ratio = _number(values.get("delta_ratio")) * 100
    completeness = values.get("completeness_ratio")
    classification = values.get("classification_ratio")
    quality_text = (
        f"完整率 {float(completeness) * 100:.1f}%｜可分類率 {float(classification or 0) * 100:.1f}%"
        if completeness is not None
        else "完整率尚無交易所對帳值"
    )
    return (
        f"{root}｜買 {buy:,.0f}／賣 {sell:,.0f} 口\n"
        f"Delta {delta:+,.0f}（{ratio:+.1f}%｜{_flow_label(ratio / 100)}）\n"
        f"買方占優 {int(values.get('buy_dominant_minutes') or 0)} 分｜"
        f"賣方占優 {int(values.get('sell_dominant_minutes') or 0)} 分｜"
        f"最長連賣 {int(values.get('max_sell_streak') or 0)} 分\n"
        f"{quality_text}"
    )


def format_hourly_analysis(analysis):
    products = analysis.get("products") or {}
    events = analysis.get("event_counts") or {}
    start = _period_text(analysis.get("period_start"))
    end = _period_text(analysis.get("period_end"))
    return (
        "【微型臺指｜過去一小時量流統計】\n"
        f"統計區間：{start}－{end}\n"
        f"市場：{analysis.get('market_status') or '--'}\n"
        f"資料狀態：{analysis.get('data_status') or '--'}\n"
        f"目前價格：{_number(analysis.get('current_price')):,.0f}\n"
        f"盤中 VWAP：{_number(analysis.get('session_vwap')):,.0f}\n\n"
        f"{_format_product('大台 TXF', products.get('TXF') or {})}\n\n"
        f"{_format_product('小台 MXF', products.get('MXF') or {})}\n\n"
        f"{_format_product('微台 TMF', products.get('TMF') or {})}\n\n"
        "倒貨警報統計\n"
        f"一級 {int(events.get(1, 0))} 次｜二級 {int(events.get(2, 0))} 次｜"
        f"三級 {int(events.get(3, 0))} 次\n\n"
        f"第一關：{_number(analysis.get('first_support')):,.0f}\n"
        f"第二關：{_number(analysis.get('second_support')):,.0f}\n"
        f"一小時結論：{analysis.get('direction') or '資料不足'}\n"
        f"判斷：{analysis.get('judgement') or '等待更多逐筆資料。'}\n\n"
        "提醒性質：主動買賣量為逐筆推估，僅供風險觀察，不代表特定大戶身分。"
    )
