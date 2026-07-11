import pandas as pd


TRADING_DAYS_PER_YEAR = 252


def _column(df, *names):
    for name in names:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(dtype="float64")


def _latest(series, default=0.0):
    if series.empty:
        return default
    value = series.iloc[-1]
    if pd.isna(value):
        return default
    return float(value)


def _adx(high, low, close, period=14):
    if len(close) < period + 2:
        return 0.0

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(period).mean()
    plus_di = 100 * plus_dm.rolling(period).mean() / atr
    minus_di = 100 * minus_dm.rolling(period).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di)) * 100
    return _latest(dx.rolling(period).mean())


def _true_range(high, low, close):
    return pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _atr(high, low, close, period=14):
    if len(close) < period + 1:
        return pd.Series(dtype="float64")
    return _true_range(high, low, close).rolling(period).mean()


def _resample_ohlcv(df, rule):
    if "ts" not in df.columns:
        return pd.DataFrame()

    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in out.columns for column in required):
        return pd.DataFrame()

    return (
        out.dropna(subset=["ts"])
        .set_index("ts")
        .resample(rule)
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        .dropna()
        .reset_index()
    )


def _trend_direction(close, fast=20, slow=60):
    if close.empty or len(close.dropna()) < fast + 5:
        return 0, "資料不足"

    fast_ma = close.rolling(fast).mean()
    slow_ma = close.rolling(slow).mean() if len(close.dropna()) >= slow else close.rolling(fast * 2).mean()
    latest_close = _latest(close)
    latest_fast = _latest(fast_ma)
    latest_slow = _latest(slow_ma, latest_fast)

    slope_window = min(5, max(1, len(fast_ma.dropna()) - 1))
    fast_valid = fast_ma.dropna()
    slope = 0.0
    if len(fast_valid) > slope_window:
        slope = float(fast_valid.iloc[-1] - fast_valid.iloc[-1 - slope_window])

    if latest_close > latest_fast > latest_slow and slope > 0:
        return 1, "偏多"
    if latest_close < latest_fast < latest_slow and slope < 0:
        return -1, "偏空"
    return 0, "盤整"


def build_tech_data(df, realtime=None):
    realtime = realtime or {}

    if df is None or df.empty:
        return fallback_tech_data(realtime, reason="永豐 kbars 尚無資料")

    close = _column(df, "Close", "close")
    open_ = _column(df, "Open", "open")
    high = _column(df, "High", "high")
    low = _column(df, "Low", "low")
    volume = _column(df, "Volume", "volume")

    if close.empty or len(close.dropna()) < 2:
        return fallback_tech_data(realtime, reason="kbars 歷史資料不足")

    ma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_dn = ma20 - 2 * std20

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal

    latest_close = _latest(close, realtime.get("current_price", 0.0))
    latest_bb_dn = _latest(bb_dn, latest_close * 0.98 if latest_close else 0.0)
    latest_hist = _latest(hist)
    prev_hist = float(hist.iloc[-2]) if len(hist) >= 2 and not pd.isna(hist.iloc[-2]) else 0.0
    latest_volume = _latest(volume, realtime.get("volume", 0.0))
    avg_volume = _latest(volume.rolling(5).mean(), latest_volume)
    ma20_latest = _latest(ma20, latest_close)
    ma60 = close.rolling(60).mean()
    ma60_latest = _latest(ma60, ma20_latest)
    atr_series = _atr(high, low, close)
    atr_points = _latest(atr_series)
    atr_pct = (atr_points / latest_close * 100) if latest_close else 0.0
    recent_resistance = _latest(high.shift(1).rolling(40).max(), _latest(high))
    recent_support = _latest(low.shift(1).rolling(40).min(), _latest(low))
    trend_15m, trend_15m_label = _trend_direction(close)

    df_60m = _resample_ohlcv(df, "60min")
    if not df_60m.empty:
        trend_60m, trend_60m_label = _trend_direction(pd.to_numeric(df_60m["Close"], errors="coerce"))
    else:
        trend_60m, trend_60m_label = trend_15m, trend_15m_label

    touched_support = False
    reclaimed_support = latest_bb_dn > 0 and latest_close > latest_bb_dn
    bullish_close = False
    if len(low) >= 2 and len(bb_dn) >= 2 and not pd.isna(bb_dn.iloc[-2]):
        touched_support = bool(low.iloc[-2] <= bb_dn.iloc[-2] * 1.003)
    if len(open_) >= 1 and not pd.isna(open_.iloc[-1]):
        bullish_close = bool(latest_close > open_.iloc[-1])
    support_retest = bool(touched_support and reclaimed_support and bullish_close)
    close_below_ma20 = latest_close > 0 and ma20_latest > 0 and latest_close < ma20_latest
    close_above_ma20 = latest_close > 0 and ma20_latest > 0 and latest_close > ma20_latest
    macd_improving = latest_hist > prev_hist
    macd_weakening = latest_hist < prev_hist
    macd_bullish = latest_hist > 0 and macd_improving
    macd_bearish = latest_hist < 0 and macd_weakening
    trend_aligned_long = trend_15m >= 0 and trend_60m > 0 and close_above_ma20
    trend_aligned_short = trend_15m <= 0 and trend_60m < 0 and close_below_ma20
    is_choppy = bool(_adx(high, low, close) < 18 or (0 < atr_pct < 0.08))
    risk_environment = "高波動" if atr_pct >= 0.45 else "低波動" if 0 < atr_pct <= 0.12 else "一般"
    volatility_30d = (
        close.pct_change()
        .rolling(30)
        .std()
        .mul(TRADING_DAYS_PER_YEAR ** 0.5)
        .mul(100)
    )

    return {
        "收盤價": latest_close,
        "BB_DN": latest_bb_dn,
        "MACD柱": latest_hist,
        "前日MACD柱": prev_hist,
        "成交量": latest_volume,
        "5日均量": avg_volume,
        "訊號": latest_hist > prev_hist,
        "ADX": _adx(high, low, close),
        "MA20": ma20_latest,
        "MA60": ma60_latest,
        "ATR": atr_points,
        "ATR%": atr_pct,
        "上方壓力": recent_resistance,
        "下方支撐": recent_support,
        "15分趨勢": trend_15m,
        "15分趨勢文字": trend_15m_label,
        "60分趨勢": trend_60m,
        "60分趨勢文字": trend_60m_label,
        "多方趨勢一致": bool(trend_aligned_long),
        "空方趨勢一致": bool(trend_aligned_short),
        "盤整": is_choppy,
        "風險環境": risk_environment,
        "MACD多方": bool(macd_bullish),
        "MACD空方": bool(macd_bearish),
        "價格站上MA20": bool(close_above_ma20),
        "價格跌破MA20": bool(close_below_ma20),
        "回測有撐": support_retest,
        "30日年化波動率": _latest(volatility_30d),
        "資料狀態": "永豐 kbars",
        "可評分": True,
    }


def fallback_tech_data(realtime=None, reason="尚未取得足夠歷史資料"):
    realtime = realtime or {}
    current_price = float(realtime.get("current_price") or 0)
    volume = float(realtime.get("volume") or 0)

    return {
        "收盤價": current_price,
        "BB_DN": current_price * 0.98 if current_price else 0.0,
        "MACD柱": 0.0,
        "前日MACD柱": 0.0,
        "成交量": volume,
        "5日均量": volume,
        "訊號": False,
        "ADX": 0.0,
        "MA20": current_price,
        "MA60": current_price,
        "ATR": 0.0,
        "ATR%": 0.0,
        "上方壓力": current_price,
        "下方支撐": current_price,
        "15分趨勢": 0,
        "15分趨勢文字": "資料不足",
        "60分趨勢": 0,
        "60分趨勢文字": "資料不足",
        "多方趨勢一致": False,
        "空方趨勢一致": False,
        "盤整": True,
        "風險環境": "資料不足",
        "MACD多方": False,
        "MACD空方": False,
        "價格站上MA20": False,
        "價格跌破MA20": False,
        "回測有撐": False,
        "30日年化波動率": 0.0,
        "資料狀態": reason,
        "可評分": False,
    }
