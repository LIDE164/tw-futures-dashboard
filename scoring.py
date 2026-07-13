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


def get_directional_strengths(data, with_reason=False):
    """Return independent long/short strengths instead of treating short as low long score."""
    if not data.get("可評分", True):
        result = (0, 0, ["技術資料不足"], ["技術資料不足"])
        return result if with_reason else result[:2]

    trend_15m = int(_to_float(data.get("15分趨勢")))
    trend_60m = int(_to_float(data.get("60分趨勢")))
    adx = _to_float(data.get("ADX"))
    volume_ratio = _to_float(data.get("量比"))
    macd = _to_float(data.get("MACD柱"))
    previous_macd = _to_float(data.get("前日MACD柱"))
    choppy = bool(data.get("盤整", False))
    long_points = 50.0
    short_points = 50.0
    long_reasons = []
    short_reasons = []

    if bool(data.get("多方趨勢一致")):
        long_points += 16
        long_reasons.append("15／60 分多方一致 +16")
    else:
        if trend_15m > 0:
            long_points += 6
            long_reasons.append("15 分偏多 +6")
        if trend_60m > 0:
            long_points += 8
            long_reasons.append("60 分偏多 +8")
        elif trend_60m < 0:
            long_points -= 10

    if bool(data.get("空方趨勢一致")):
        short_points += 16
        short_reasons.append("15／60 分空方一致 +16")
    else:
        if trend_15m < 0:
            short_points += 6
            short_reasons.append("15 分偏空 +6")
        if trend_60m < 0:
            short_points += 8
            short_reasons.append("60 分偏空 +8")
        elif trend_60m > 0:
            short_points -= 10

    if bool(data.get("價格站上MA20")):
        long_points += 8
        short_points -= 5
        long_reasons.append("價格站上 MA20 +8")
    if bool(data.get("價格跌破MA20")):
        short_points += 8
        long_points -= 5
        short_reasons.append("價格跌破 MA20 +8")

    if bool(data.get("MACD多方")):
        long_points += 10
        long_reasons.append("MACD 多方擴張 +10")
    elif macd > previous_macd:
        long_points += 4
        long_reasons.append("MACD 動能改善 +4")
    if bool(data.get("MACD空方")):
        short_points += 10
        short_reasons.append("MACD 空方擴張 +10")
    elif macd < previous_macd:
        short_points += 4
        short_reasons.append("MACD 動能轉弱 +4")

    if adx >= 22:
        if trend_15m > 0:
            long_points += 5
            long_reasons.append(f"ADX {adx:.1f} 確認多方 +5")
        elif trend_15m < 0:
            short_points += 5
            short_reasons.append(f"ADX {adx:.1f} 確認空方 +5")
    if volume_ratio >= 1.0:
        if trend_15m > 0:
            long_points += 4
            long_reasons.append(f"量比 {volume_ratio:.2f} 確認多方 +4")
        elif trend_15m < 0:
            short_points += 4
            short_reasons.append(f"量比 {volume_ratio:.2f} 確認空方 +4")
    if bool(data.get("回測有撐")):
        long_points += 5
        long_reasons.append("支撐回測成立 +5")
    if choppy:
        long_points = 50 + (long_points - 50) * 0.55
        short_points = 50 + (short_points - 50) * 0.55
        long_reasons.append("盤整降權")
        short_reasons.append("盤整降權")

    long_strength = max(0, min(100, int(round(long_points))))
    short_strength = max(0, min(100, int(round(short_points))))
    result = (long_strength, short_strength, long_reasons, short_reasons)
    return result if with_reason else result[:2]


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
    trend_60m = int(_to_float(data.get("60分趨勢")))
    atr_pct = _to_float(data.get("ATR%"))
    risk_environment = data.get("風險環境", "一般")
    is_choppy = bool(data.get("盤整", False))

    is_trending = adx >= 25

    if data.get("多方趨勢一致", False):
        score_delta += 3
        _add_reason(reasons, with_reason, "15分與60分趨勢偏多 +3")
    elif data.get("空方趨勢一致", False):
        score_delta -= 3
        _add_reason(reasons, with_reason, "15分與60分趨勢偏空 -3")
    elif trend_60m > 0:
        score_delta += 1
        _add_reason(reasons, with_reason, "60分大方向偏多 +1")
    elif trend_60m < 0:
        score_delta -= 1
        _add_reason(reasons, with_reason, "60分大方向偏空 -1")

    if data.get("價格站上MA20", False):
        score_delta += 1
        _add_reason(reasons, with_reason, "價格站上MA20 +1")
    elif data.get("價格跌破MA20", False):
        score_delta -= 1
        _add_reason(reasons, with_reason, "價格跌破MA20 -1")

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

    if data.get("MACD多方", False):
        score_delta += 1
        _add_reason(reasons, with_reason, "MACD多方擴張 +1")
    elif data.get("MACD空方", False):
        score_delta -= 1
        _add_reason(reasons, with_reason, "MACD空方擴張 -1")

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

    raw_score = 50 + score_delta * 3
    if is_choppy:
        raw_score = 50 + (raw_score - 50) * 0.55
        _add_reason(reasons, with_reason, "盤整盤，訊號信心降權")
    if risk_environment == "高波動":
        raw_score = 50 + (raw_score - 50) * 0.85
        _add_reason(reasons, with_reason, f"高波動 ATR={atr_pct:.2f}%，降低追價權重")
    elif risk_environment == "低波動":
        raw_score = 50 + (raw_score - 50) * 0.75
        _add_reason(reasons, with_reason, f"低波動 ATR={atr_pct:.2f}%，降低假突破權重")

    final_score = max(5, min(99, int(raw_score)))

    if final_score >= 60:
        label = "強勢買進"
    elif final_score >= 45:
        label = "偏多觀察"
    elif final_score <= 40:
        label = "偏空警戒"
    else:
        label = "忽略"

    feature = "一般狀態"
    if is_choppy:
        feature = "盤整降權"
    elif data.get("多方趨勢一致", False):
        feature = "多方趨勢一致"
    elif data.get("空方趨勢一致", False):
        feature = "空方趨勢一致"
    elif data.get("紅吞", False):
        feature = "紅吞表態"
    elif data.get("回測有撐", False):
        feature = "回檔有撐"

    return (final_score, label, reasons, feature) if with_reason else (final_score, label, feature)
