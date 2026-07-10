def _to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _add_reason(reasons, enabled, text):
    if enabled:
        reasons.append(text)


def get_decision_score(data, fund_data=None, inst_data=None, with_reason=False):
    fund_data = fund_data or {}
    inst_data = inst_data or {}
    score_delta = 0
    reasons = []

    if not data.get("可評分", True):
        reasons.append("技術資料不足，暫不產生方向分數")
        result = (50, "資料不足", reasons, "等待資料")
        return result if with_reason else (50, "資料不足", "等待資料")

    adx = _to_float(data.get("ADX"))
    close = _to_float(data.get("收盤價"))
    bb_dn = _to_float(data.get("BB_DN"))
    macd = _to_float(data.get("MACD柱"))
    prev_macd = _to_float(data.get("前日MACD柱"), -999)
    volume = _to_float(data.get("成交量"))
    avg_volume = _to_float(data.get("5日均量"))
    foreign_oi = _to_float(inst_data.get("外資"))

    is_trending = adx >= 25

    if data.get("訊號", False):
        add_score = 3 if is_trending else 1
        score_delta += add_score
        _add_reason(reasons, with_reason, f"訊號成立 ADX={adx:.1f} +{add_score}")

    if close > 0 and bb_dn > 0 and close <= bb_dn * 1.02:
        score_delta += 2
        _add_reason(reasons, with_reason, "布林下軌支撐 +2")

    if macd > prev_macd:
        score_delta += 2
        _add_reason(reasons, with_reason, "MACD好轉 +2")
    else:
        score_delta -= 3
        _add_reason(reasons, with_reason, "MACD轉弱 -3")

    if volume > 0 and avg_volume > 0 and volume > avg_volume * 1.1:
        score_delta += 2
        _add_reason(reasons, with_reason, "量增 +2")

    if data.get("回測有撐", False):
        score_delta += 2
        _add_reason(reasons, with_reason, "回測支撐 +2")

    if foreign_oi >= 5000:
        score_delta += 2
        _add_reason(reasons, with_reason, "外資期貨淨多單偏強 +2")
    elif foreign_oi > 0:
        score_delta += 1
        _add_reason(reasons, with_reason, "外資期貨淨多單 +1")
    elif foreign_oi <= -5000:
        score_delta -= 2
        _add_reason(reasons, with_reason, "外資期貨淨空單偏強 -2")
    elif foreign_oi < 0:
        score_delta -= 1
        _add_reason(reasons, with_reason, "外資期貨淨空單 -1")

    final_score = max(5, min(99, int(50 + score_delta * 3)))

    if final_score >= 60:
        label = "強勢買進"
    elif final_score >= 45:
        label = "偏多觀察"
    elif final_score <= 40:
        label = "偏空警戒"
    else:
        label = "忽略"

    feature = "一般狀態"
    if data.get("紅吞", False):
        feature = "紅吞表態"
    elif data.get("回測有撐", False):
        feature = "回檔有撐"

    return (final_score, label, reasons, feature) if with_reason else (final_score, label, feature)
