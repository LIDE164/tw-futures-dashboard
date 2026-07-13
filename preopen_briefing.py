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


def _prepare_15m_bars(bars):
    if bars is None or bars.empty or "ts" not in bars.columns:
        return pd.DataFrame()
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(bars.columns):
        return pd.DataFrame()
    frame = bars.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce")
    frame = frame.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts", keep="last")
    if frame.empty:
        return frame
    indexed = frame.set_index("ts")
    spacing = indexed.index.to_series().diff().median()
    if pd.isna(spacing) or spacing < pd.Timedelta(minutes=10):
        indexed = (
            indexed.resample("15min")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
            .dropna()
        )
    return indexed.reset_index()


def _feature_at(frame, index):
    if index < 20:
        return None
    history = frame.iloc[: index + 1]
    close = history["Close"].astype(float)
    high = history["High"].astype(float)
    low = history["Low"].astype(float)
    volume = history["Volume"].astype(float)
    current = float(close.iloc[-1])
    if current <= 0:
        return None
    previous = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - previous).abs(), (low - previous).abs()], axis=1
    ).max(axis=1)
    atr = float(true_range.tail(14).mean() or 0)
    volume_mean = float(volume.tail(20).mean() or 0)
    return {
        "ret_4": current / float(close.iloc[-5]) - 1,
        "ret_16": current / float(close.iloc[-17]) - 1,
        "ma_gap": current / float(close.tail(20).mean()) - 1,
        "atr_pct": atr / current,
        "volume_ratio": float(volume.iloc[-1]) / volume_mean if volume_mean > 0 else 1.0,
        "atr": atr,
        "price": current,
    }


def _session_records(frame, session):
    records = []
    if frame.empty:
        return records
    frame = frame.copy().reset_index(drop=True)
    dates = sorted(frame["ts"].dt.normalize().unique())
    for raw_date in dates:
        date = pd.Timestamp(raw_date)
        if session == "day":
            cutoff_start = date + pd.Timedelta(hours=4, minutes=30)
            cutoff_end = date + pd.Timedelta(hours=5, minutes=1)
            outcome_start = date + pd.Timedelta(hours=8, minutes=45)
            outcome_end = date + pd.Timedelta(hours=13, minutes=46)
        else:
            cutoff_start = date + pd.Timedelta(hours=13, minutes=15)
            cutoff_end = date + pd.Timedelta(hours=13, minutes=46)
            outcome_start = date + pd.Timedelta(hours=15)
            outcome_end = date + pd.Timedelta(days=1, hours=5, minutes=1)

        cutoff_rows = frame[(frame["ts"] >= cutoff_start) & (frame["ts"] < cutoff_end)]
        outcome = frame[(frame["ts"] >= outcome_start) & (frame["ts"] < outcome_end)]
        if cutoff_rows.empty or len(outcome) < 4:
            continue
        cutoff_index = int(cutoff_rows.index[-1])
        feature = _feature_at(frame, cutoff_index)
        if feature is None:
            continue
        session_close = float(outcome["Close"].iloc[-1])
        threshold = max(15.0, feature["atr"] * 0.35)
        move = session_close - feature["price"]
        outcome_name = "bull" if move >= threshold else "bear" if move <= -threshold else "range"
        records.append(
            {
                **feature,
                "date": date.strftime("%Y/%m/%d"),
                "outcome": outcome_name,
                "close_move": move,
                "up_move": float(outcome["High"].max()) - feature["price"],
                "down_move": feature["price"] - float(outcome["Low"].min()),
            }
        )
    return records


def _distance(left, right):
    scales = {
        "ret_4": 0.004,
        "ret_16": 0.010,
        "ma_gap": 0.008,
        "atr_pct": 0.004,
        "volume_ratio": 0.80,
    }
    total = 0.0
    for name, scale in scales.items():
        total += ((_number(left.get(name)) - _number(right.get(name))) / scale) ** 2
    return total ** 0.5


def _weighted_probabilities(neighbours):
    totals = {"bull": 1.0, "range": 1.0, "bear": 1.0}
    for item in neighbours:
        totals[item["outcome"]] += 1.0 / (0.35 + item["distance"])
    denominator = sum(totals.values()) or 1.0
    raw = {name: value / denominator * 100 for name, value in totals.items()}
    rounded = {name: int(round(value)) for name, value in raw.items()}
    rounded[max(raw, key=raw.get)] += 100 - sum(rounded.values())
    return rounded


def _walk_forward_accuracy(records):
    hits = 0
    tests = 0
    for index in range(12, len(records)):
        target = records[index]
        prior = []
        for candidate in records[:index]:
            prior.append({**candidate, "distance": _distance(target, candidate)})
        neighbours = sorted(prior, key=lambda item: item["distance"])[: min(12, len(prior))]
        if not neighbours:
            continue
        predicted = max(_weighted_probabilities(neighbours), key=_weighted_probabilities(neighbours).get)
        hits += int(predicted == target["outcome"])
        tests += 1
    return round(hits / tests * 100, 1) if tests else 0.0, tests


def build_scenario_model(bars, session, score=50):
    frame = _prepare_15m_bars(bars)
    current = _feature_at(frame, len(frame) - 1) if not frame.empty else None
    records = _session_records(frame, session)
    if current is None or len(records) < 8:
        bull = max(15, min(65, int(score)))
        bear = max(10, min(60, 100 - int(score)))
        range_probability = max(15, 100 - bull - bear)
        total = bull + range_probability + bear
        probabilities = {
            "bull": round(bull / total * 100),
            "range": round(range_probability / total * 100),
            "bear": 0,
        }
        probabilities["bear"] = 100 - probabilities["bull"] - probabilities["range"]
        return {
            "method": "資料不足，暫用策略分數先驗",
            "sample_size": len(records),
            "neighbour_count": 0,
            "walk_forward_accuracy": 0.0,
            "walk_forward_tests": 0,
            "confidence": "低",
            "probabilities": probabilities,
            "typical_up_move": 0.0,
            "typical_down_move": 0.0,
            "typical_range": 0.0,
        }

    ranked = [{**item, "distance": _distance(current, item)} for item in records]
    neighbour_count = min(24, max(10, int(len(records) ** 0.5 * 3)))
    neighbours = sorted(ranked, key=lambda item: item["distance"])[:neighbour_count]
    probabilities = _weighted_probabilities(neighbours)
    accuracy, tests = _walk_forward_accuracy(records)
    typical_up = pd.Series([max(0.0, item["up_move"]) for item in neighbours]).median()
    typical_down = pd.Series([max(0.0, item["down_move"]) for item in neighbours]).median()
    typical_range = pd.Series([abs(item["close_move"]) for item in neighbours]).median()
    confidence = "高" if len(records) >= 45 and tests >= 25 and accuracy >= 50 else "中" if len(records) >= 20 else "低"
    return {
        "method": "15分K歷史相似日 KNN（每次盤前重算）",
        "sample_size": len(records),
        "neighbour_count": neighbour_count,
        "walk_forward_accuracy": accuracy,
        "walk_forward_tests": tests,
        "confidence": confidence,
        "probabilities": probabilities,
        "typical_up_move": round(float(typical_up or 0), 0),
        "typical_down_move": round(float(typical_down or 0), 0),
        "typical_range": round(float(typical_range or 0), 0),
    }


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
    scenario_model = build_scenario_model(bars, session, score=score)

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
        "bid_volume": int(_number(realtime.get("bid_volume"))),
        "ask_volume": int(_number(realtime.get("ask_volume"))),
        "total_volume": int(_number(realtime.get("volume"))),
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
        "investment_trust_oi": institutional.get("投信"),
        "dealer_oi": institutional.get("自營商"),
        "institutional_date": institutional.get("date"),
        "scenario_model": scenario_model,
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
