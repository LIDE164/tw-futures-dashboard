import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtester import _normalise_kbars, _resample_kbars, _trading_session_key
from flow_cost_model import add_flow_cost_features, merge_true_order_flow
from historical_data import load_continuous_kbars, load_order_flow_minutes


PARAMETERS = {
    "multiplier": 10,
    "commission_round_trip": 40,
    "slippage_points_per_side": 2,
    "min_adx": 20,
    "min_volume_ratio": 0.85,
    "min_flow_ratio": 0.04,
    "min_flow_volume_intensity": 0.85,
    "min_close_location": 0.10,
    "atr_stop_multiplier": 1.2,
    "min_stop_points": 30,
    "max_stop_points": 120,
    "reward_risk_ratio": 2.0,
}


def _add_indicators(frame, suffix=""):
    out = frame.copy()
    close = pd.to_numeric(out["Close"], errors="coerce")
    high = pd.to_numeric(out["High"], errors="coerce")
    low = pd.to_numeric(out["Low"], errors="coerce")
    volume = pd.to_numeric(out["Volume"], errors="coerce")
    for period in (5, 10, 20):
        out[f"MA{period}{suffix}"] = close.rolling(period, min_periods=period).mean()
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    out[f"MACDHist{suffix}"] = macd - signal
    previous_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    atr = true_range.rolling(14, min_periods=14).mean()
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    plus_di = 100 * plus_dm.rolling(14, min_periods=14).sum() / atr.replace(0, pd.NA)
    minus_di = 100 * minus_dm.rolling(14, min_periods=14).sum() / atr.replace(0, pd.NA)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    out[f"ATR{suffix}"] = atr
    out[f"ADX{suffix}"] = dx.rolling(14, min_periods=14).mean()
    out[f"VolumeRatio{suffix}"] = volume / volume.rolling(20, min_periods=20).mean().replace(0, pd.NA)
    return out


def build_intraday_features(kbars, flow_minutes):
    mixed = merge_true_order_flow(kbars, flow_minutes)
    normal = _normalise_kbars(mixed)
    bars15 = _resample_kbars(normal, "15min")
    bars5 = _resample_kbars(normal, "5min")
    bars15 = add_flow_cost_features(bars15, lookback_bars=2)
    bars15 = _add_indicators(bars15)
    bars5 = _add_indicators(bars5, "5")

    # A 20-period 60-minute MA equals 80 completed 15-minute bars. Using the
    # 15-minute series keeps the feature causal across irregular session gaps.
    bars15["MA20_60"] = bars15["Close"].rolling(80, min_periods=80).mean()
    bars15["MA20_60_Slope"] = bars15["MA20_60"].diff(4)
    bars15["Trend60"] = 0
    bars15.loc[(bars15["Close"] > bars15["MA20_60"]) & (bars15["MA20_60_Slope"] > 0), "Trend60"] = 1
    bars15.loc[(bars15["Close"] < bars15["MA20_60"]) & (bars15["MA20_60_Slope"] < 0), "Trend60"] = -1

    # The 5-minute confirmation is the final completed 5-minute bar inside the
    # same 15-minute signal bar, so it is known exactly when that bar closes.
    minute_mod = bars5["ts"].dt.minute % 15
    confirm = bars5[minute_mod.eq(10)].copy()
    confirm["signal_ts"] = confirm["ts"] - pd.Timedelta(minutes=10)
    confirm = confirm[
        ["signal_ts", "Close", "MA55", "MA105", "MA205", "MACDHist5"]
    ].rename(
        columns={
            "Close": "Close5", "MA55": "MA5Confirm", "MA105": "MA10Confirm",
            "MA205": "MA20Confirm", "MACDHist5": "MACDConfirm",
        }
    )
    confirm["MACDConfirmPrev"] = confirm["MACDConfirm"].shift(1)
    out = bars15.merge(confirm, left_on="ts", right_on="signal_ts", how="left")
    out["SignalDecisionAt"] = out["ts"] + pd.Timedelta(minutes=15)

    p = PARAMETERS
    common = (
        out["ADX"].ge(p["min_adx"])
        & out["VolumeRatio"].ge(p["min_volume_ratio"])
        & out["FlowVolumeIntensity"].ge(p["min_flow_volume_intensity"])
    )
    out["LongStructure"] = common & (
        out["Trend60"].eq(1)
        & out["Close"].gt(out["MA20"])
        & out["MA5"].gt(out["MA10"])
        & out["MACDHist"].gt(out["MACDHist"].shift(1))
        & out["Close5"].gt(out["MA20Confirm"])
        & out["MA5Confirm"].ge(out["MA10Confirm"])
        & out["MACDConfirm"].ge(out["MACDConfirmPrev"])
    )
    out["LongSignal"] = out["LongStructure"] & (
        out["FlowRatio"].ge(p["min_flow_ratio"])
        & out["Close"].ge(out["SessionVWAP"])
        & out["CostSlope"].ge(0)
        & out["KCloseLocation"].ge(p["min_close_location"])
    )
    out["ShortStructure"] = common & (
        out["Trend60"].eq(-1)
        & out["Close"].lt(out["MA20"])
        & out["MA5"].lt(out["MA10"])
        & out["MACDHist"].lt(out["MACDHist"].shift(1))
        & out["Close5"].lt(out["MA20Confirm"])
        & out["MA5Confirm"].le(out["MA10Confirm"])
        & out["MACDConfirm"].le(out["MACDConfirmPrev"])
    )
    out["ShortSignal"] = out["ShortStructure"] & (
        out["FlowRatio"].le(-p["min_flow_ratio"])
        & out["Close"].le(out["SessionVWAP"])
        & out["CostSlope"].le(0)
        & out["KCloseLocation"].le(-p["min_close_location"])
    )
    out["SessionKey"] = out["ts"].map(_trading_session_key)
    return out.reset_index(drop=True)


def _close_trade(position, exit_time, exit_price, reason):
    p = PARAMETERS
    if position["side"] == "做多":
        pnl_points = exit_price - position["entry_price"]
    else:
        pnl_points = position["entry_price"] - exit_price
    return {
        **position,
        "exit_time": str(exit_time),
        "exit_price": round(float(exit_price), 2),
        "exit_reason": reason,
        "pnl_points": round(float(pnl_points), 2),
        "pnl": round(float(pnl_points * p["multiplier"] - p["commission_round_trip"]), 2),
    }


def simulate_intraday(features, start_index=0, use_flow=True):
    p = PARAMETERS
    trades, position = [], None
    start_index = max(100, int(start_index))
    for i in range(start_index, len(features) - 1):
        bar, next_bar = features.iloc[i], features.iloc[i + 1]
        if position is not None and i >= position["entry_bar"]:
            if position["side"] == "做多":
                stop_hit = float(bar["Low"]) <= position["stop_price"]
                target_hit = float(bar["High"]) >= position["target_price"]
                if stop_hit:
                    raw_exit = min(float(bar["Open"]), position["stop_price"])
                    trades.append(_close_trade(position, bar["ts"], raw_exit - p["slippage_points_per_side"], "停損"))
                    position = None
                elif target_hit:
                    trades.append(_close_trade(position, bar["ts"], position["target_price"] - p["slippage_points_per_side"], "停利"))
                    position = None
            else:
                stop_hit = float(bar["High"]) >= position["stop_price"]
                target_hit = float(bar["Low"]) <= position["target_price"]
                if stop_hit:
                    raw_exit = max(float(bar["Open"]), position["stop_price"])
                    trades.append(_close_trade(position, bar["ts"], raw_exit + p["slippage_points_per_side"], "停損"))
                    position = None
                elif target_hit:
                    trades.append(_close_trade(position, bar["ts"], position["target_price"] + p["slippage_points_per_side"], "停利"))
                    position = None

        boundary = bar["SessionKey"] != next_bar["SessionKey"]
        if position is not None and boundary:
            exit_price = float(bar["Close"]) - p["slippage_points_per_side"] if position["side"] == "做多" else float(bar["Close"]) + p["slippage_points_per_side"]
            trades.append(_close_trade(position, bar["ts"], exit_price, "時段收盤"))
            position = None
        if position is not None or boundary:
            continue

        long_column = "LongSignal" if use_flow else "LongStructure"
        short_column = "ShortSignal" if use_flow else "ShortStructure"
        side = "做多" if bool(bar[long_column]) else "做空" if bool(bar[short_column]) else None
        if side is None:
            continue
        stop_points = max(
            p["min_stop_points"],
            min(p["max_stop_points"], float(bar["ATR"] or 0) * p["atr_stop_multiplier"]),
        )
        if side == "做多":
            entry = float(next_bar["Open"]) + p["slippage_points_per_side"]
            stop, target = entry - stop_points, entry + stop_points * p["reward_risk_ratio"]
        else:
            entry = float(next_bar["Open"]) - p["slippage_points_per_side"]
            stop, target = entry + stop_points, entry - stop_points * p["reward_risk_ratio"]
        position = {
            "side": side,
            "signal_time": str(bar["ts"]),
            "entry_time": str(next_bar["ts"]),
            "entry_bar": i + 1,
            "entry_price": round(entry, 2),
            "stop_price": round(stop, 2),
            "target_price": round(target, 2),
            "flow_ratio": round(float(bar["FlowRatio"]), 4),
            "flow_source": str(bar["FlowSource"]),
            "session": str(bar["SessionKey"]),
        }
    if position is not None:
        last = features.iloc[-1]
        exit_price = float(last["Close"]) - p["slippage_points_per_side"] if position["side"] == "做多" else float(last["Close"]) + p["slippage_points_per_side"]
        trades.append(_close_trade(position, last["ts"], exit_price, "資料結束"))
    return pd.DataFrame(trades)


def _summary(trades):
    if trades is None or trades.empty:
        return {"交易次數": 0, "總損益": 0, "勝率": 0, "期望值": 0, "平均獲利": 0, "平均虧損": 0, "盈虧比": 0, "Profit Factor": 0, "最大回撤": 0}
    pnl = pd.to_numeric(trades["pnl"], errors="coerce").fillna(0)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    avg_win = float(wins.mean()) if not wins.empty else 0.0
    avg_loss = float(losses.mean()) if not losses.empty else 0.0
    gross_profit = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses.sum())) if not losses.empty else 0.0
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax()
    return {
        "交易次數": int(len(pnl)), "總損益": round(float(pnl.sum()), 0),
        "勝率": round(float((pnl > 0).mean() * 100), 2), "期望值": round(float(pnl.mean()), 0),
        "平均獲利": round(avg_win, 0), "平均虧損": round(avg_loss, 0),
        "盈虧比": round(abs(avg_win / avg_loss), 2) if avg_loss else 0,
        "Profit Factor": round(gross_profit / gross_loss, 2) if gross_loss else (999 if gross_profit else 0),
        "最大回撤": round(float(drawdown.min()), 0) if not drawdown.empty else 0,
    }


def _result_rows(trades, segment):
    rows = []
    for side in ("做多", "做空", "多空合併"):
        selected = trades if side == "多空合併" else trades[trades["side"].eq(side)]
        rows.append({
            "segment": segment,
            "side": side,
            "summary": _summary(selected),
            "true_tick_trades": int((selected.get("flow_source", pd.Series(dtype=str)) == "true_tick").sum()),
        })
    return rows


def run_intraday_flow_study(train_ratio=0.70):
    kbars = load_continuous_kbars("TMF")
    flow = load_order_flow_minutes()
    features = build_intraday_features(kbars, flow)
    split_index = int(len(features) * float(train_ratio))
    full_trades = simulate_intraday(features)
    oos_trades = simulate_intraday(features, start_index=split_index)
    baseline_full = simulate_intraday(features, use_flow=False)
    baseline_oos = simulate_intraday(features, start_index=split_index, use_flow=False)
    complete_flow = int(((flow["completeness_ratio"] >= .95) & (flow["classification_ratio"] >= .80)).sum()) if not flow.empty else 0
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": "60分方向＋15分結構＋5分確認＋量流＋Session VWAP",
        "parameters": PARAMETERS,
        "history": {
            "kbar_rows": int(len(kbars)), "feature_rows": int(len(features)),
            "first": str(kbars["ts"].min()), "last": str(kbars["ts"].max()),
            "flow_rows": int(len(flow)), "complete_flow_rows": complete_flow,
            "flow_sessions": int(flow["session_key"].nunique()) if not flow.empty else 0,
            "true_tick_feature_bars": int((features["FlowSource"] == "true_tick").sum()),
            "oos_start": str(features["ts"].iloc[split_index]),
        },
        "results": _result_rows(full_trades, "全期間") + _result_rows(oos_trades, "後30%樣本外"),
        "baseline_without_flow": _result_rows(baseline_full, "全期間") + _result_rows(baseline_oos, "後30%樣本外"),
        "limitations": [
            "真實逐筆量流目前僅覆蓋一個交易時段；其餘期間採因果 K 棒量流代理。",
            "訊號只使用完成 K 棒，下一根開盤成交，排除 13:45/05:00 收盤殘棒。",
            "每個交易時段結束前強制平倉，不把跨盤跳空列入盤中策略優勢。",
        ],
    }


def save_study(result, output="data/intraday_flow_backtest_latest.json"):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    result = run_intraday_flow_study()
    output = save_study(result)
    print(json.dumps({"output": output, "history": result["history"], "results": result["results"]}, ensure_ascii=False))
