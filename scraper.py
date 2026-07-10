from datetime import datetime, timedelta

import requests


EMPTY_OI = {"外資": 0, "投信": 0, "自營商": 0, "date": None, "error": None}
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _to_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalise_status(status):
    try:
        return int(status)
    except (TypeError, ValueError):
        return 0


def get_taifex_institutional_oi(api_token=""):
    result = EMPTY_OI.copy()

    try:
        start_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        params = {
            "dataset": "TaiwanFuturesInstitutionalInvestors",
            "data_id": "TXF",
            "start_date": start_date,
        }
        if api_token:
            params["token"] = api_token

        response = requests.get(FINMIND_URL, params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()

        rows = payload.get("data") or []
        if _normalise_status(payload.get("status")) != 200 or not rows:
            result["error"] = "FinMind API 未傳回資料，請確認 Token 或稍後在盤後再試。"
            return result

        latest_date = max(row.get("date", "") for row in rows)
        latest_rows = [row for row in rows if row.get("date") == latest_date]

        for item in latest_rows:
            name = item.get("name", "")
            net_volume = _to_int(item.get("open_interest_net_volume"))

            if "外資" in name:
                result["外資"] = net_volume
            elif "投信" in name:
                result["投信"] = net_volume
            elif "自營" in name:
                result["自營商"] = net_volume

        result["date"] = latest_date
        return result

    except requests.RequestException as exc:
        result["error"] = f"籌碼 API 連線異常：{exc}"
        return result
    except ValueError as exc:
        result["error"] = f"籌碼 API 回傳格式異常：{exc}"
        return result
