import os
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

try:
    import shioaji as sj
except Exception:  # pragma: no cover
    sj = None


if load_dotenv:
    load_dotenv()


def _get_secret(name, default=""):
    value = os.getenv(name)
    if value:
        return value

    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def _env_bool(name, default=True):
    value = str(_get_secret(name, str(default))).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def get_simulation_default():
    return _env_bool("SJ_SIMULATION", True)


def has_credentials(api_key="", secret_key=""):
    return bool(api_key or _get_secret("SJ_API_KEY")) and bool(secret_key or _get_secret("SJ_SECRET_KEY"))


@st.cache_resource(show_spinner=False)
def get_api(simulation=True, api_key="", secret_key=""):
    if sj is None:
        return None, "尚未安裝 shioaji，請先在 requirements.txt 加入 shioaji 並重新部署。"

    api_key = api_key or _get_secret("SJ_API_KEY")
    secret_key = secret_key or _get_secret("SJ_SECRET_KEY")

    if not api_key or not secret_key:
        return None, "尚未設定永豐 API Key / Secret，已停用永豐行情、部位與下單功能。"

    try:
        api = sj.Shioaji(simulation=simulation)
        api.login(
            api_key=api_key,
            secret_key=secret_key,
            fetch_contract=True,
            contracts_timeout=10000,
        )
        return api, None
    except Exception as exc:
        return None, f"永豐 Shioaji 登入失敗：{exc}"


def activate_ca_from_env(api):
    if api is None or sj is None:
        return False, "尚未登入永豐 API。"

    ca_path = _get_secret("SJ_CA_PATH")
    ca_password = _get_secret("SJ_CA_PASSWORD")
    person_id = _get_secret("SJ_PERSON_ID")

    if not ca_path or not ca_password or not person_id:
        return False, "尚未設定 CA 憑證路徑、密碼或身分證字號，無法送出正式委託。"

    try:
        api.activate_ca(
            ca_path=ca_path,
            ca_passwd=ca_password,
            person_id=person_id,
        )
        return True, None
    except Exception as exc:
        return False, f"CA 憑證啟用失敗：{exc}"


def get_txf_contract(api, contract_code="TXFR1"):
    if api is None:
        raise RuntimeError("尚未登入永豐 API。")

    txf_contracts = api.Contracts.Futures.TXF
    contract = getattr(txf_contracts, contract_code, None)
    if contract:
        return contract

    for fallback_code in ("TXFR1", "TXFR2"):
        contract = getattr(txf_contracts, fallback_code, None)
        if contract:
            return contract

    raise RuntimeError("找不到台指期近月連續契約 TXFR1/TXFR2。")


def _iter_contracts(container):
    try:
        return list(container)
    except TypeError:
        pass

    contracts = []
    for value in getattr(container, "__dict__", {}).values():
        code = getattr(value, "code", "")
        if code:
            contracts.append(value)
    return contracts


def get_txf_kbar_contract(api):
    if api is None:
        raise RuntimeError("尚未登入永豐 API。")

    today = datetime.now().strftime("%Y/%m/%d")
    contracts = []
    for contract in _iter_contracts(api.Contracts.Futures.TXF):
        code = getattr(contract, "code", "")
        delivery_date = getattr(contract, "delivery_date", "") or "9999/99/99"

        if not code.startswith("TXF") or code.startswith("TXFR"):
            continue
        if delivery_date and delivery_date < today:
            continue

        contracts.append(contract)

    if contracts:
        return sorted(
            contracts,
            key=lambda item: (
                getattr(item, "delivery_date", "") or "9999/99/99",
                getattr(item, "code", ""),
            ),
        )[0]

    return get_txf_contract(api, "TXFR1")


def _first_price(*values):
    for value in values:
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            number = 0
        if number > 0:
            return number
    return 0.0


def _kbars_to_dataframe(kbars):
    if kbars is None:
        return pd.DataFrame()

    field_names = ("ts", "Open", "High", "Low", "Close", "Volume", "Amount")

    if isinstance(kbars, dict):
        data = kbars
    elif hasattr(kbars, "_asdict"):
        data = kbars._asdict()
    else:
        data = {
            key: value
            for key, value in vars(kbars).items()
            if not key.startswith("_")
        } if hasattr(kbars, "__dict__") else {}

        if not data:
            data = {
                field_name: getattr(kbars, field_name)
                for field_name in field_names
                if hasattr(kbars, field_name)
            }

    if not data:
        return pd.DataFrame()

    return pd.DataFrame(data)


def get_realtime_data_from_sinopac(api, contract_code="TXFR1"):
    if api is None:
        return {
            "current_price": 0.0,
            "volume": 0,
            "vwap": 0.0,
            "vix": 0.0,
            "source": "Sinopac",
            "updated_at": None,
            "error": "尚未登入永豐 API。",
        }

    try:
        contract = get_txf_contract(api, contract_code)
        snapshots = api.snapshots([contract])

        if not snapshots:
            return {
                "current_price": 0.0,
                "volume": 0,
                "vwap": 0.0,
                "vix": 0.0,
                "source": "Sinopac snapshot",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": "永豐 Shioaji snapshot 無資料，可能非交易時段或尚未登入成功。",
            }

        snapshot = snapshots[0]
        close = _first_price(
            getattr(snapshot, "close", 0),
            getattr(snapshot, "buy_price", 0),
            getattr(snapshot, "sell_price", 0),
        )

        return {
            "current_price": close,
            "volume": int(_first_price(getattr(snapshot, "total_volume", 0), getattr(snapshot, "volume", 0))),
            "vwap": _first_price(getattr(snapshot, "average_price", 0), close),
            "vix": 0.0,
            "source": f"Sinopac {contract_code} snapshot",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": None if close > 0 else "永豐 snapshot 未提供有效成交價。",
        }
    except Exception as exc:
        return {
            "current_price": 0.0,
            "volume": 0,
            "vwap": 0.0,
            "vix": 0.0,
            "source": "Sinopac snapshot",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": f"永豐行情讀取失敗：{exc}",
        }


def get_recent_txf_kbars(api, days=60):
    if api is None:
        return pd.DataFrame(), "尚未登入永豐 API。"

    try:
        contract = get_txf_kbar_contract(api)
        end = datetime.now().date()
        start = end - timedelta(days=days)
        kbars = api.kbars(contract, start=str(start), end=str(end))
        df = _kbars_to_dataframe(kbars)

        if df.empty:
            return df, (
                "永豐 kbars 無資料，"
                f"使用契約：{getattr(contract, 'code', 'UNKNOWN')}，"
                f"回傳型態：{type(kbars).__name__}。"
            )

        if "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"])
            df = df.sort_values("ts")

        df.attrs["contract_code"] = getattr(contract, "code", "")
        return df, None
    except Exception as exc:
        return pd.DataFrame(), f"永豐 kbars 讀取失敗：{exc}"


def get_connection_status(api, simulation=True):
    if api is None:
        return {
            "登入": "未登入",
            "模式": "模擬" if simulation else "正式",
            "期貨帳號": "無",
            "商品檔": "無",
        }

    futopt_account = getattr(api, "futopt_account", None)
    contracts_status = getattr(getattr(api, "Contracts", None), "status", None)

    return {
        "登入": "成功",
        "模式": "模擬" if simulation else "正式",
        "期貨帳號": "有" if futopt_account else "無",
        "商品檔": str(contracts_status) if contracts_status is not None else "未知",
    }


def get_fut_positions(api):
    columns = ["商品", "方向", "口數", "均價", "現價", "未實現損益"]
    if api is None:
        return pd.DataFrame(columns=columns)

    try:
        positions = api.list_positions(account=api.futopt_account)
    except Exception:
        return pd.DataFrame(columns=columns)

    rows = []
    for position in positions:
        rows.append(
            {
                "商品": getattr(position, "code", ""),
                "方向": str(getattr(position, "direction", "")).replace("Action.", ""),
                "口數": getattr(position, "quantity", 0),
                "均價": getattr(position, "price", 0),
                "現價": getattr(position, "last_price", 0),
                "未實現損益": getattr(position, "pnl", 0),
            }
        )

    return pd.DataFrame(rows, columns=columns)


def place_futures_order(api, action, quantity=1, price=0, market=True, contract_code="TXFR1"):
    if api is None or sj is None:
        raise RuntimeError("尚未登入永豐 API，無法送單。")

    if action not in {"BUY_LONG", "SELL_SHORT", "CLOSE_LONG", "CLOSE_SHORT"}:
        raise ValueError(f"不支援的交易動作：{action}")

    contract = get_txf_contract(api, contract_code)
    order_action = sj.Action.Buy if action in {"BUY_LONG", "CLOSE_SHORT"} else sj.Action.Sell

    order = sj.FuturesOrder(
        action=order_action,
        price=price,
        quantity=int(quantity),
        price_type=sj.FuturesPriceType.MKT if market else sj.FuturesPriceType.LMT,
        order_type=sj.OrderType.IOC if market else sj.OrderType.ROD,
        octype=sj.FuturesOCType.Auto,
        account=api.futopt_account,
    )

    return api.place_order(contract, order)
