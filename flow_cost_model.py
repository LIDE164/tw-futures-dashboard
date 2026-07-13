from datetime import timedelta

import pandas as pd


def _session_key(value):
    ts = pd.Timestamp(value)
    minutes = ts.hour * 60 + ts.minute
    if 8 * 60 + 45 <= minutes <= 13 * 60 + 45:
        return f"{ts.date().isoformat()}:day"
    if minutes >= 15 * 60:
        return f"{ts.date().isoformat()}:night"
    if minutes <= 5 * 60:
        return f"{(ts.date() - timedelta(days=1)).isoformat()}:night"
    return f"{ts.date().isoformat()}:closed"


def merge_true_order_flow(kbars, flow_minutes):
    """Attach persisted TXF and small-futures minute flow to one-minute K bars."""
    if kbars is None or kbars.empty or flow_minutes is None or flow_minutes.empty:
        return kbars.copy() if kbars is not None else pd.DataFrame()
    bars = kbars.copy()
    bars["ts"] = pd.to_datetime(bars["ts"], errors="coerce")
    flow = flow_minutes.copy()
    flow["ts"] = pd.to_datetime(flow["ts"], errors="coerce")
    flow = flow.dropna(subset=["ts"])

    def product_frame(root, prefix):
        selected = flow[flow["product_root"] == root].copy()
        if selected.empty:
            return pd.DataFrame(columns=["ts"])
        return selected.rename(
            columns={
                "buy_volume": f"{prefix}Buy",
                "sell_volume": f"{prefix}Sell",
                "completeness_ratio": f"{prefix}Completeness",
                "classification_ratio": f"{prefix}Classification",
            }
        )[["ts", f"{prefix}Buy", f"{prefix}Sell", f"{prefix}Completeness", f"{prefix}Classification"]]

    tx = product_frame("TXF", "Flow")
    small_root = "MXF" if (flow["product_root"] == "MXF").any() else "TMF"
    small = product_frame(small_root, "SmallFlow")
    out = bars.merge(tx, on="ts", how="left").merge(small, on="ts", how="left")
    out.attrs.update(getattr(kbars, "attrs", {}))
    return out


def add_flow_cost_features(df, lookback_bars=4, min_true_completeness=0.95, min_true_classification=0.80):
    """Create causal K-line flow proxy and session VWAP cost features."""
    out = df.copy()
    if out.empty:
        return out
    out["ts"] = pd.to_datetime(out.get("ts"), errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume", "Amount"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")

    out["FlowSession"] = out["ts"].map(_session_key)
    typical = (out["High"] + out["Low"] + out["Close"]) / 3
    if "Amount" in out.columns:
        amount = out["Amount"].where(out["Amount"] > 0, typical * out["Volume"])
    else:
        amount = typical * out["Volume"]
    out["FlowAmount"] = amount.fillna(0)
    out["SessionCumAmount"] = out.groupby("FlowSession", sort=False)["FlowAmount"].cumsum()
    out["SessionCumVolume"] = out.groupby("FlowSession", sort=False)["Volume"].cumsum()
    out["SessionVWAP"] = out["SessionCumAmount"] / out["SessionCumVolume"].replace(0, pd.NA)
    out["CostDistance"] = out["Close"] - out["SessionVWAP"]
    out["CostSlope"] = out.groupby("FlowSession", sort=False)["SessionVWAP"].diff(
        max(1, int(lookback_bars))
    )

    bar_range = (out["High"] - out["Low"]).replace(0, pd.NA)
    close_location = ((out["Close"] - out["Low"]) - (out["High"] - out["Close"])) / bar_range
    fallback_direction = (out["Close"] - out["Open"]).apply(lambda value: 0.25 if value > 0 else (-0.25 if value < 0 else 0.0))
    out["KCloseLocation"] = close_location.fillna(fallback_direction).clip(-1, 1)
    out["ProxyDelta"] = out["Volume"] * out["KCloseLocation"]

    true_columns = {"FlowBuy", "FlowSell", "FlowCompleteness", "FlowClassification"}
    has_true = true_columns.issubset(out.columns)
    if has_true:
        for column in true_columns | {"SmallFlowBuy", "SmallFlowSell", "SmallFlowCompleteness", "SmallFlowClassification"}:
            if column in out.columns:
                out[column] = pd.to_numeric(out[column], errors="coerce")
        true_ready = (
            out["FlowCompleteness"].ge(float(min_true_completeness))
            & out["FlowClassification"].ge(float(min_true_classification))
            & out["FlowBuy"].notna()
            & out["FlowSell"].notna()
        )
        true_delta = out["FlowBuy"].fillna(0) - out["FlowSell"].fillna(0)
        true_volume = out["FlowBuy"].fillna(0) + out["FlowSell"].fillna(0)
        out["FlowDelta"] = out["ProxyDelta"].where(~true_ready, true_delta)
        out["FlowClassifiedVolume"] = out["Volume"].where(~true_ready, true_volume)
        out["FlowSource"] = true_ready.map({True: "true_tick", False: "kbar_proxy"})
    else:
        out["FlowDelta"] = out["ProxyDelta"]
        out["FlowClassifiedVolume"] = out["Volume"]
        out["FlowSource"] = "kbar_proxy"

    lookback = max(2, int(lookback_bars))
    out["FlowDeltaRolling"] = out.groupby("FlowSession", sort=False)["FlowDelta"].transform(
        lambda series: series.rolling(lookback, min_periods=lookback).sum()
    )
    out["FlowVolumeRolling"] = out.groupby("FlowSession", sort=False)["FlowClassifiedVolume"].transform(
        lambda series: series.rolling(lookback, min_periods=lookback).sum()
    )
    out["FlowRatio"] = out["FlowDeltaRolling"] / out["FlowVolumeRolling"].replace(0, pd.NA)
    out["VolumeMedian20"] = out["Volume"].rolling(20, min_periods=5).median()
    out["FlowVolumeIntensity"] = out["Volume"] / out["VolumeMedian20"].replace(0, pd.NA)
    out.attrs.update(getattr(df, "attrs", {}))
    out.attrs["flow_model"] = "true_tick_when_complete_else_kbar_proxy"
    return out


def evaluate_flow_cost_entry(
    action,
    row,
    atr_points,
    min_flow_ratio=0.05,
    min_volume_intensity=0.8,
    max_cost_distance_atr=1.2,
    require_cost_slope=True,
    min_close_location=0.0,
):
    row = dict(row) if row is not None else {}
    flow_ratio = float(row.get("FlowRatio") or 0)
    volume_intensity = float(row.get("FlowVolumeIntensity") or 0)
    close = float(row.get("Close") or 0)
    session_vwap = float(row.get("SessionVWAP") or 0)
    cost_slope = float(row.get("CostSlope") or 0)
    close_location = float(row.get("KCloseLocation") or 0)
    atr_points = float(atr_points or 0)
    distance_atr = abs(close - session_vwap) / atr_points if atr_points > 0 and session_vwap > 0 else 0.0
    reasons = []

    if action == "BUY_LONG":
        if flow_ratio < float(min_flow_ratio):
            reasons.append(f"量流代理 {flow_ratio:+.3f} 未達多方門檻 {float(min_flow_ratio):+.3f}")
        if session_vwap > 0 and close < session_vwap:
            reasons.append("價格仍低於盤中平均成本")
        if require_cost_slope and cost_slope < 0:
            reasons.append("盤中平均成本仍下彎")
        if close_location < float(min_close_location):
            reasons.append("K 棒收盤位置未確認買方")
    elif action == "SELL_SHORT":
        if flow_ratio > -float(min_flow_ratio):
            reasons.append(f"量流代理 {flow_ratio:+.3f} 未達空方門檻 {-float(min_flow_ratio):+.3f}")
        if session_vwap > 0 and close > session_vwap:
            reasons.append("價格仍高於盤中平均成本")
        if require_cost_slope and cost_slope > 0:
            reasons.append("盤中平均成本仍上彎")
        if close_location > -float(min_close_location):
            reasons.append("K 棒收盤位置未確認賣方")

    if volume_intensity and volume_intensity < float(min_volume_intensity):
        reasons.append(f"成交量強度 {volume_intensity:.2f} 不足")
    if max_cost_distance_atr and distance_atr > float(max_cost_distance_atr):
        reasons.append(f"價格距平均成本 {distance_atr:.2f} ATR，追價風險過高")
    return reasons, {
        "flow_ratio": flow_ratio,
        "volume_intensity": volume_intensity,
        "session_vwap": session_vwap,
        "cost_slope": cost_slope,
        "cost_distance_atr": distance_atr,
        "flow_source": row.get("FlowSource") or "kbar_proxy",
    }
