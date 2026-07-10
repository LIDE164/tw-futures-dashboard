import pandas as pd


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


def build_tech_data(df, realtime=None):
    realtime = realtime or {}

    if df is None or df.empty:
        return fallback_tech_data(realtime, reason="永豐 kbars 尚無資料")

    close = _column(df, "Close", "close")
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

    return {
        "收盤價": latest_close,
        "BB_DN": latest_bb_dn,
        "MACD柱": latest_hist,
        "前日MACD柱": prev_hist,
        "成交量": latest_volume,
        "5日均量": avg_volume,
        "訊號": latest_hist > prev_hist,
        "ADX": _adx(high, low, close),
        "回測有撐": bool(latest_bb_dn > 0 and latest_close > latest_bb_dn),
        "資料狀態": "永豐 kbars",
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
        "回測有撐": False,
        "資料狀態": reason,
    }
