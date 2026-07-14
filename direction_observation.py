def _number(value, default=0.0):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return float(default)


def _small_delta_ratio(flow):
    buy = _number(flow.get("small_buy_volume"))
    sell = _number(flow.get("small_sell_volume"))
    total = buy + sell
    return (buy - sell) / total if total > 0 else 0.0


def build_direction_observation(tech_data=None, flow=None, current_price=0, session_vwap=0):
    """Combine completed-bar direction and live flow without creating a trade signal."""
    from scoring import get_directional_strengths

    tech_data = dict(tech_data or {})
    flow = dict(flow or {})
    long_strength, short_strength, long_reasons, short_reasons = get_directional_strengths(
        tech_data,
        with_reason=True,
    )
    current_price = _number(current_price) or _number(tech_data.get("收盤價"))
    session_vwap = _number(session_vwap) or _number(flow.get("session_vwap"))
    tx_ratio = _number(flow.get("tx_delta_ratio"))
    small_ratio = _small_delta_ratio(flow)
    adjusted_long = float(long_strength)
    adjusted_short = float(short_strength)
    factors = [
        f"15 分 {tech_data.get('15分趨勢文字') or '資料不足'}／"
        f"60 分 {tech_data.get('60分趨勢文字') or '資料不足'}",
        f"量比 {_number(tech_data.get('量比')):.2f}／ADX {_number(tech_data.get('ADX')):.1f}",
    ]

    if session_vwap > 0 and current_price > 0:
        if current_price >= session_vwap:
            adjusted_long += 5
            factors.append(f"價格位於 VWAP {session_vwap:,.0f} 之上")
        else:
            adjusted_short += 5
            factors.append(f"價格位於 VWAP {session_vwap:,.0f} 之下")
    else:
        factors.append("VWAP 尚無完整資料")

    if flow.get("stream_ready"):
        flow_points = min(8.0, abs(tx_ratio) * 50)
        if tx_ratio > 0:
            adjusted_long += flow_points
        elif tx_ratio < 0:
            adjusted_short += flow_points
        if small_ratio > 0:
            adjusted_long += min(4.0, abs(small_ratio) * 25)
        elif small_ratio < 0:
            adjusted_short += min(4.0, abs(small_ratio) * 25)
        factors.append(f"大台 Delta {tx_ratio * 100:+.1f}%／小型商品 {small_ratio * 100:+.1f}%")
    else:
        factors.append("逐筆量流尚未就緒，方向信心降級")

    adjusted_long = max(0, min(100, int(round(adjusted_long))))
    adjusted_short = max(0, min(100, int(round(adjusted_short))))
    gap = adjusted_long - adjusted_short
    if gap >= 10 and adjusted_long >= 55:
        direction = "偏多觀察"
        leading_reasons = long_reasons
    elif gap <= -10 and adjusted_short >= 55:
        direction = "偏空觀察"
        leading_reasons = short_reasons
    else:
        direction = "多空拉鋸"
        leading_reasons = []
    if not tech_data.get("可評分", True):
        direction = "資料不足"

    quality_ready = bool(flow.get("data_quality_ready"))
    confidence = "高" if quality_ready and abs(gap) >= 18 else "中" if abs(gap) >= 10 else "低"
    return {
        "direction": direction,
        "long_strength": adjusted_long,
        "short_strength": adjusted_short,
        "confidence": confidence,
        "trend_15m": tech_data.get("15分趨勢文字") or "資料不足",
        "trend_60m": tech_data.get("60分趨勢文字") or "資料不足",
        "volume_ratio": _number(tech_data.get("量比")),
        "adx": _number(tech_data.get("ADX")),
        "tx_delta_ratio": tx_ratio,
        "small_delta_ratio": small_ratio,
        "flow_ready": bool(flow.get("stream_ready")),
        "flow_quality_ready": quality_ready,
        "factors": factors,
        "reasons": list(leading_reasons[:3]),
        "notice": "方向觀察不等於正式交易候選。",
    }


def format_direction_observation(observation):
    observation = dict(observation or {})
    return (
        "方向觀察\n"
        f"結論：{observation.get('direction') or '資料不足'}｜信心 {observation.get('confidence') or '低'}\n"
        f"多方強度 {int(observation.get('long_strength') or 0)}／100｜"
        f"空方強度 {int(observation.get('short_strength') or 0)}／100\n"
        f"15 分 {observation.get('trend_15m') or '--'}｜60 分 {observation.get('trend_60m') or '--'}｜"
        f"量比 {_number(observation.get('volume_ratio')):.2f}\n"
        "性質：僅為盤中方向觀察，尚未通過正式交易候選門檻。"
    )


def evaluate_formal_candidate(action, tech_data=None, flow=None, current_price=0, session_vwap=0):
    """Require trend, flow quality, delta direction and VWAP alignment for an entry candidate."""
    tech_data = dict(tech_data or {})
    flow = dict(flow or {})
    current_price = _number(current_price) or _number(tech_data.get("收盤價"))
    session_vwap = _number(session_vwap) or _number(flow.get("session_vwap"))
    tx_ratio = _number(flow.get("tx_delta_ratio"))
    small_ratio = _small_delta_ratio(flow)
    passed = []
    blocked = []

    if not flow.get("stream_ready"):
        blocked.append("逐筆行情尚未就緒")
    else:
        passed.append("逐筆行情連線正常")
    if not flow.get("data_quality_ready"):
        blocked.append("量流完整率或可分類率未達門檻")
    else:
        passed.append("量流資料品質通過")
    if session_vwap <= 0:
        blocked.append("盤中 VWAP 尚無資料")

    trend_15m = int(_number(tech_data.get("15分趨勢")))
    trend_60m = int(_number(tech_data.get("60分趨勢")))
    if action == "BUY_LONG":
        if trend_15m <= 0 or trend_60m <= 0:
            blocked.append("15／60 分趨勢尚未同步偏多")
        else:
            passed.append("15／60 分趨勢同步偏多")
        if session_vwap > 0 and current_price < session_vwap:
            blocked.append("價格仍在 VWAP 下方")
        elif session_vwap > 0:
            passed.append("價格位於 VWAP 上方")
        if tx_ratio < 0.02 or small_ratio < 0:
            blocked.append("大台與小型商品買方量流尚未同步")
        else:
            passed.append("大台與小型商品買方量流同步")
    elif action == "SELL_SHORT":
        if trend_15m >= 0 or trend_60m >= 0:
            blocked.append("15／60 分趨勢尚未同步偏空")
        else:
            passed.append("15／60 分趨勢同步偏空")
        if session_vwap > 0 and current_price > session_vwap:
            blocked.append("價格仍在 VWAP 上方")
        elif session_vwap > 0:
            passed.append("價格位於 VWAP 下方")
        if tx_ratio > -0.02 or small_ratio > 0:
            blocked.append("大台與小型商品賣方量流尚未同步")
        else:
            passed.append("大台與小型商品賣方量流同步")
    else:
        blocked.append("目前不是進場方向")

    return {
        "allowed": not blocked,
        "passed": passed,
        "blocked": blocked,
        "tx_delta_ratio": tx_ratio,
        "small_delta_ratio": small_ratio,
        "session_vwap": session_vwap,
    }
