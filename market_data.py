from io import StringIO
import re
from urllib.request import Request, urlopen

import pandas as pd

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

from storage import load_json_state, save_json_state


TAIFEX_HEADERS = {"User-Agent": "Mozilla/5.0"}
PC_RATIO_URL = "https://www.taifex.com.tw/cht/3/pcRatio"
OPTION_DAILY_URL = "https://www.taifex.com.tw/cht/3/optDailyMarketReport"
OPTION_LIQUIDITY_URL = "https://www.taifex.com.tw/cht/3/optDailyLi"
FUT_CONTRACTS_URL = "https://www.taifex.com.tw/cht/3/futContractsDateExcel"
LARGE_TRADER_URL = "https://www.taifex.com.tw/cht/3/largeTraderFutQryTbl"
PUBLIC_HISTORY_STATE_KEY = "public_market_history"


def _read_html_tables(url, **kwargs):
    request = Request(url, headers=TAIFEX_HEADERS)
    raw = urlopen(request, timeout=10).read()
    html = raw.decode("utf-8", errors="replace")
    return pd.read_html(StringIO(html), **kwargs)


def _to_number(value, default=0.0):
    try:
        if value in (None, "", "-"):
            return default
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _first_number(value, default=0.0):
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(value or ""))
    return _to_number(match.group(0), default) if match else default


def _table_date(table):
    if table is None or table.empty:
        return None
    text = " ".join(str(value) for value in table.astype(str).to_numpy().ravel())
    match = re.search(r"\d{4}/\d{2}/\d{2}", text)
    return match.group(0) if match else None


def _futures_contract_tables():
    # The Excel view contains a short metadata table and a three-row-header data
    # table. pandas already preserves the data table's HTML header as MultiIndex;
    # forcing header=[0,1,2] onto the metadata table raises before parsing data.
    tables = _read_html_tables(FUT_CONTRACTS_URL)
    if not tables:
        raise ValueError("TAIFEX 三大法人資料表為空")
    data = max(tables, key=lambda table: table.shape[1])
    meta = next((table for table in tables if table is not data), pd.DataFrame())
    return data, _table_date(meta) or _table_date(data)


def get_pc_ratio():
    try:
        df = _read_html_tables(PC_RATIO_URL)[0]
        latest = df.iloc[0]

        return {
            "date": str(latest["日期"]),
            "volume_ratio": _to_number(latest["買賣權成交量比率%"]),
            "oi_ratio": _to_number(latest["買賣權未平倉量比率%"]),
            "put_volume": int(_to_number(latest["賣權成交量"])),
            "call_volume": int(_to_number(latest["買權成交量"])),
            "source": "TAIFEX 買賣權比率",
            "error": None,
        }
    except Exception as exc:
        return {
            "date": None,
            "volume_ratio": None,
            "oi_ratio": None,
            "put_volume": 0,
            "call_volume": 0,
            "source": "TAIFEX 買賣權比率",
            "error": f"TAIFEX P/C Ratio 讀取失敗：{exc}",
        }


def get_option_pressure_support():
    try:
        tables = _read_html_tables(OPTION_LIQUIDITY_URL)
        df = tables[0]
        date_df = tables[1] if len(tables) > 1 else pd.DataFrame()
        df = df[df["商品"].astype(str).str.upper().eq("TXO")].copy()
        df["履約價_num"] = df["履約價格"].map(_to_number)
        df["oi_num"] = df["未沖銷部位(A)"].map(_to_number)
        df = df[df["oi_num"] > 0]

        if df.empty:
            raise ValueError("TAIFEX 選擇權流動性表沒有 TXO 未沖銷資料")

        expiry = sorted(df["到期月份(週別)"].dropna().unique())[0]
        front = df[df["到期月份(週別)"].eq(expiry)]

        calls = front[front["買賣權"].astype(str).eq("買權")]
        puts = front[front["買賣權"].astype(str).eq("賣權")]

        call_row = calls.sort_values("oi_num", ascending=False).iloc[0] if not calls.empty else None
        put_row = puts.sort_values("oi_num", ascending=False).iloc[0] if not puts.empty else None
        data_date = str(date_df.iloc[0]["資料日期"]) if not date_df.empty and "資料日期" in date_df.columns else str(expiry)

        return {
            "date": data_date,
            "expiry": str(expiry),
            "call_pressure": int(call_row["履約價_num"]) if call_row is not None else None,
            "call_oi": int(call_row["oi_num"]) if call_row is not None else 0,
            "put_support": int(put_row["履約價_num"]) if put_row is not None else None,
            "put_oi": int(put_row["oi_num"]) if put_row is not None else 0,
            "source": "TAIFEX 選擇權每日未沖銷",
            "error": None,
        }
    except Exception as exc:
        return {
            "date": None,
            "expiry": None,
            "call_pressure": None,
            "call_oi": 0,
            "put_support": None,
            "put_oi": 0,
            "source": "TAIFEX 選擇權每日未沖銷",
            "error": f"TAIFEX 選擇權壓力/支撐讀取失敗：{exc}",
        }


def get_mtx_institutional_net():
    try:
        df, data_date = _futures_contract_tables()
        rows = df[df.iloc[:, 1].astype(str).str.contains("小型臺指期貨", regex=False, na=False)]

        if rows.empty:
            raise ValueError("TAIFEX 找不到小型臺指期貨三大法人資料")

        long_oi = int(rows.iloc[:, 9].map(_to_number).sum())
        short_oi = int(rows.iloc[:, 11].map(_to_number).sum())
        net_oi = int(rows.iloc[:, 13].map(_to_number).sum())

        ratio = None
        if short_oi > 0:
            ratio = round((long_oi / short_oi) * 100, 2)

        return {
            "product": "小型臺指期貨",
            "long_oi": long_oi,
            "short_oi": short_oi,
            "net_oi": net_oi,
            "long_short_ratio": ratio,
            "date": data_date,
            "source": "TAIFEX 三大法人期貨未平倉",
            "error": None,
        }
    except Exception as exc:
        return {
            "product": "小型臺指期貨",
            "long_oi": 0,
            "short_oi": 0,
            "net_oi": 0,
            "long_short_ratio": None,
            "date": None,
            "source": "TAIFEX 三大法人期貨未平倉",
            "error": f"TAIFEX 小台法人資料讀取失敗：{exc}",
        }


def get_txf_institutional_oi():
    result = {
        "外資": 0,
        "投信": 0,
        "自營商": 0,
        "合計": 0,
        "date": None,
        "source": "TAIFEX 三大法人期貨未平倉",
        "error": None,
    }

    try:
        df, data_date = _futures_contract_tables()
        rows = df[df.iloc[:, 1].astype(str).str.contains("臺股期貨", regex=False, na=False)]

        if rows.empty:
            raise ValueError("TAIFEX 找不到臺股期貨三大法人資料")

        name_map = {
            "外資": "外資",
            "投信": "投信",
            "自營": "自營商",
        }

        for _, row in rows.iterrows():
            identity = str(row.iloc[2])
            net_oi = int(_to_number(row.iloc[13]))

            for keyword, output_name in name_map.items():
                if keyword in identity:
                    result[output_name] = net_oi
                    break

        result["合計"] = result["外資"] + result["投信"] + result["自營商"]
        result["date"] = data_date
        return result
    except Exception as exc:
        result["error"] = f"TAIFEX 臺股期貨法人資料讀取失敗：{exc}"
        return result


def get_large_trader_oi():
    """Return the official near-month top-10 TX-equivalent position structure."""
    try:
        tables = _read_html_tables(LARGE_TRADER_URL)
        if len(tables) < 2:
            raise ValueError("TAIFEX 大額交易人表格不足")
        data_date = _table_date(tables[0])
        df = max(tables, key=lambda table: table.shape[1])
        rows = df[df.iloc[:, 0].astype(str).str.contains("臺股期貨", regex=False, na=False)].copy()
        rows = rows[
            ~rows.iloc[:, 1].astype(str).str.contains("週契約|所有", regex=True, na=False)
        ]
        if rows.empty:
            raise ValueError("找不到臺股期貨近月大額交易人資料")
        row = rows.iloc[0]
        long_oi = int(_first_number(row.iloc[4]))
        short_oi = int(_first_number(row.iloc[8]))
        return {
            "date": data_date,
            "expiry": str(row.iloc[1]).replace(" ", ""),
            "long_oi": long_oi,
            "short_oi": short_oi,
            "net_oi": long_oi - short_oi,
            "market_oi": int(_first_number(row.iloc[10])),
            "source": "TAIFEX 期貨大額交易人未沖銷部位",
            "scope": "臺股期貨 TX+MTX/4+TMF/20 近月前十大",
            "error": None,
        }
    except Exception as exc:
        return {
            "date": None,
            "expiry": None,
            "long_oi": 0,
            "short_oi": 0,
            "net_oi": None,
            "market_oi": 0,
            "source": "TAIFEX 期貨大額交易人未沖銷部位",
            "scope": "臺股期貨 TX+MTX/4+TMF/20 近月前十大",
            "error": f"TAIFEX 大額交易人資料讀取失敗：{exc}",
        }


def get_international_market_data():
    tickers = {"^SOX": "SOX", "^IXIC": "NASDAQ", "NVDA": "NVIDIA", "TSM": "台積電 ADR"}
    result = {"items": [], "sox_history": [], "source": "Yahoo Finance", "error": None}
    if yf is None:
        result["error"] = "yfinance 尚未安裝"
        return result
    try:
        data = yf.download(
            list(tickers), period="3mo", interval="1d", group_by="ticker",
            auto_adjust=False, progress=False, threads=True,
        )
        for symbol, label in tickers.items():
            frame = data[symbol] if isinstance(data.columns, pd.MultiIndex) and symbol in data.columns.levels[0] else pd.DataFrame()
            close = frame.get("Close", pd.Series(dtype=float)).dropna()
            if len(close) < 1:
                result["items"].append({"symbol": symbol, "label": label, "last": None, "change_pct": None})
                continue
            last = float(close.iloc[-1])
            previous = float(close.iloc[-2]) if len(close) > 1 else last
            result["items"].append(
                {
                    "symbol": symbol,
                    "label": label,
                    "last": last,
                    "change_pct": (last / previous - 1) * 100 if previous else 0.0,
                    "date": pd.Timestamp(close.index[-1]).strftime("%Y/%m/%d"),
                }
            )
            if symbol == "^SOX":
                result["sox_history"] = [
                    {"date": pd.Timestamp(index).strftime("%Y/%m/%d"), "close": float(value)}
                    for index, value in close.tail(65).items()
                ]
        return result
    except Exception as exc:
        result["error"] = f"Yahoo Finance 國際行情讀取失敗：{exc}"
        return result


def _record_public_history(payload):
    history = load_json_state(PUBLIC_HISTORY_STATE_KEY, {"rows": []})
    rows = list(history.get("rows") or [])
    institutional = payload.get("txf_institutional") or {}
    mtx = payload.get("mtx_net") or {}
    large = payload.get("large_trader") or {}
    date = institutional.get("date") or mtx.get("date") or large.get("date")
    if not date:
        return rows[-20:]
    row = {
        "date": date,
        "mtx_ratio": mtx.get("long_short_ratio"),
        "mtx_net": mtx.get("net_oi"),
        "foreign": institutional.get("外資"),
        "trust": institutional.get("投信"),
        "dealer": institutional.get("自營商"),
        "institutional_total": institutional.get("合計"),
        "large_trader_net": large.get("net_oi"),
    }
    rows = [item for item in rows if item.get("date") != date]
    rows.append(row)
    rows = sorted(rows, key=lambda item: str(item.get("date"))) [-120:]
    save_json_state(PUBLIC_HISTORY_STATE_KEY, {"rows": rows})
    return rows[-20:]


def get_public_market_data():
    pc_ratio = get_pc_ratio()
    option_levels = get_option_pressure_support()
    mtx_net = get_mtx_institutional_net()
    txf_institutional = get_txf_institutional_oi()
    large_trader = get_large_trader_oi()
    international = get_international_market_data()

    errors = [
        item.get("error")
        for item in (pc_ratio, option_levels, mtx_net, txf_institutional, large_trader, international)
        if item.get("error")
    ]

    result = {
        "pc_ratio": pc_ratio,
        "option_levels": option_levels,
        "mtx_net": mtx_net,
        "txf_institutional": txf_institutional,
        "large_trader": large_trader,
        "international": international,
        "errors": errors,
    }
    try:
        result["history"] = _record_public_history(result)
    except Exception as exc:
        result["history"] = []
        result["errors"].append(f"公開資料歷史保存失敗：{exc}")
    return result
