from io import StringIO
from urllib.request import Request, urlopen

import pandas as pd


TAIFEX_HEADERS = {"User-Agent": "Mozilla/5.0"}
PC_RATIO_URL = "https://www.taifex.com.tw/cht/3/pcRatio"
OPTION_DAILY_URL = "https://www.taifex.com.tw/cht/3/optDailyMarketReport"
OPTION_LIQUIDITY_URL = "https://www.taifex.com.tw/cht/3/optDailyLi"
FUT_CONTRACTS_URL = "https://www.taifex.com.tw/cht/3/futContractsDate"


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
        df = _read_html_tables(FUT_CONTRACTS_URL, header=[0, 1, 2])[0]
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
            "source": "TAIFEX 三大法人期貨未平倉",
            "error": f"TAIFEX 小台法人資料讀取失敗：{exc}",
        }


def get_txf_institutional_oi():
    result = {"外資": 0, "投信": 0, "自營商": 0, "date": None, "error": None}

    try:
        df = _read_html_tables(FUT_CONTRACTS_URL, header=[0, 1, 2])[0]
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

        result["date"] = "TAIFEX 最新交易日"
        return result
    except Exception as exc:
        result["error"] = f"TAIFEX 臺股期貨法人資料讀取失敗：{exc}"
        return result


def get_public_market_data():
    pc_ratio = get_pc_ratio()
    option_levels = get_option_pressure_support()
    mtx_net = get_mtx_institutional_net()
    txf_institutional = get_txf_institutional_oi()

    errors = [
        item.get("error")
        for item in (pc_ratio, option_levels, mtx_net, txf_institutional)
        if item.get("error")
    ]

    return {
        "pc_ratio": pc_ratio,
        "option_levels": option_levels,
        "mtx_net": mtx_net,
        "txf_institutional": txf_institutional,
        "errors": errors,
    }
