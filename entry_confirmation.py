import pandas as pd

from indicators import build_tech_data
from scoring import get_decision_score


ENTRY_ACTIONS = {"BUY_LONG", "SELL_SHORT"}


def evaluate_5m_confirmation(
    action,
    five_minute_bars,
    signal_bar_time,
    long_confirm_score=50,
    short_confirm_score=50,
):
    result = {
        "required": True,
        "confirmed": False,
        "status": "等待 5 分確認",
        "score": 50,
        "label": "資料不足",
        "bar_time": "",
        "reasons": [],
    }
    if action not in ENTRY_ACTIONS:
        result.update({"confirmed": True, "status": "非進場訊號"})
        return result
    if five_minute_bars is None or five_minute_bars.empty or "ts" not in five_minute_bars.columns:
        result["reasons"] = ["尚無完整 5 分 K。"]
        return result

    signal_start = pd.to_datetime(signal_bar_time, errors="coerce")
    if pd.isna(signal_start):
        result["reasons"] = ["15 分訊號時間無效。"]
        return result

    bars = five_minute_bars.copy()
    bars["ts"] = pd.to_datetime(bars["ts"], errors="coerce")
    bars = bars.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    final_five_start = signal_start + pd.Timedelta(minutes=10)
    candidates = bars[(bars["ts"] >= final_five_start) & (bars["ts"] < signal_start + pd.Timedelta(minutes=15))]
    if candidates.empty:
        result["reasons"] = ["15 分訊號棒內最後一根 5 分 K 尚未完成。"]
        return result

    candidate_index = int(candidates.index[-1])
    history = bars.iloc[max(0, candidate_index - 399) : candidate_index + 1].copy()
    if len(history) < 60:
        result["reasons"] = ["5 分 K 歷史不足 60 根。"]
        return result

    close = float(history["Close"].iloc[-1])
    previous_close = float(history["Close"].iloc[-2])
    realtime = {
        "current_price": close,
        "volume": float(history["Volume"].iloc[-1]),
        "vwap": close,
    }
    tech = build_tech_data(history, realtime)
    score, label, _, _ = get_decision_score(tech, inst_data={}, with_reason=True)
    trend = int(float(tech.get("15分趨勢") or 0))
    macd = float(tech.get("MACD柱") or 0)
    previous_macd = float(tech.get("前日MACD柱") or 0)
    ma20 = float(tech.get("MA20") or 0)

    if action == "BUY_LONG":
        required_checks = [
            (score >= int(long_confirm_score), f"5 分評分 {score} 需至少 {int(long_confirm_score)}"),
            (trend >= 0, "5 分趨勢不可偏空"),
            (close >= ma20, "5 分收盤需站上 MA20"),
        ]
        momentum_ok = macd >= previous_macd or close >= previous_close
        momentum_message = "5 分 MACD 或收盤動能至少一項需轉強"
    else:
        required_checks = [
            (score <= int(short_confirm_score), f"5 分評分 {score} 需不高於 {int(short_confirm_score)}"),
            (trend <= 0, "5 分趨勢不可偏多"),
            (close <= ma20, "5 分收盤需跌破 MA20"),
        ]
        momentum_ok = macd <= previous_macd or close <= previous_close
        momentum_message = "5 分 MACD 或收盤動能至少一項需轉弱"

    failed = [message for passed, message in required_checks if not passed]
    if not momentum_ok:
        failed.append(momentum_message)
    confirmed = not failed
    result.update(
        {
            "confirmed": confirmed,
            "status": "5 分確認通過" if confirmed else "15 分成立，5 分未確認",
            "score": int(score),
            "label": label,
            "bar_time": pd.to_datetime(history["ts"].iloc[-1]).strftime("%Y/%m/%d %H:%M"),
            "reasons": failed,
            "close": close,
            "ma20": ma20,
            "trend": trend,
        }
    )
    return result
