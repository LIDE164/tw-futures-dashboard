import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

try:
    import streamlit as st
except Exception:  # pragma: no cover
    class _NoStreamlit:
        secrets = {}

        @staticmethod
        def cache_resource(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

    st = _NoStreamlit()

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

try:
    import shioaji as sj
except Exception:  # pragma: no cover
    sj = None


BASE_DIR = Path(__file__).resolve().parent
_FLOW_CALLBACKS = {}


def _load_env_file(dotenv_path):
    try:
        text = Path(dotenv_path).read_text(encoding="utf-8-sig")
    except OSError:
        return False

    loaded = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        if key:
            os.environ[key] = value
            loaded = True
    return loaded


def load_environment():
    dotenv_paths = [BASE_DIR / ".env", Path.cwd() / ".env"]
    loaded = False
    for dotenv_path in dotenv_paths:
        if dotenv_path.exists():
            if load_dotenv is not None:
                load_dotenv(dotenv_path=dotenv_path, override=True, encoding="utf-8-sig")
            _load_env_file(dotenv_path)
            loaded = True
    if not loaded and load_dotenv is not None:
        load_dotenv(override=True, encoding="utf-8-sig")


load_environment()


def _get_secret(name, default=""):
    value = os.getenv(name)
    if value:
        return value

    bom_value = os.getenv(f"\ufeff{name}")
    if bom_value:
        return bom_value

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


DEFAULT_FUTURES_ROOT = "TMF"


def _get_futures_group(api, product_root=DEFAULT_FUTURES_ROOT):
    if api is None:
        raise RuntimeError("尚未登入永豐 API。")

    futures = getattr(api.Contracts, "Futures", None)
    group = getattr(futures, product_root, None)
    if group is None:
        raise RuntimeError(f"找不到永豐商品檔中的 {product_root} 期貨契約。")
    return group


def get_futures_contract(api, product_root=DEFAULT_FUTURES_ROOT, contract_code=None):
    group = _get_futures_group(api, product_root)

    if contract_code:
        contract = getattr(group, contract_code, None)
        if contract:
            return contract

    continuous_codes = (f"{product_root}R1", f"{product_root}R2")
    for fallback_code in continuous_codes:
        contract = getattr(group, fallback_code, None)
        if contract:
            return contract

    return get_near_month_futures_contract(api, product_root)


def get_txf_contract(api, contract_code="TXFR1"):
    return get_futures_contract(api, "TXF", contract_code)


def get_micro_txf_contract(api, contract_code=None):
    return get_futures_contract(api, DEFAULT_FUTURES_ROOT, contract_code)


def get_near_month_futures_contract(api, product_root=DEFAULT_FUTURES_ROOT):
    if api is None:
        raise RuntimeError("尚未登入永豐 API。")

    now = datetime.now()
    today = now.strftime("%Y/%m/%d")
    settlement_finished = (now.hour, now.minute) >= (13, 45)
    contracts = []
    for contract in _iter_contracts(_get_futures_group(api, product_root)):
        code = getattr(contract, "code", "")
        delivery_date = getattr(contract, "delivery_date", "") or "9999/99/99"

        if not code.startswith(product_root) or code.startswith(f"{product_root}R"):
            continue
        if delivery_date and (
            delivery_date < today
            or (delivery_date == today and settlement_finished)
        ):
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

    group = _get_futures_group(api, product_root)
    for fallback_code in (f"{product_root}R1", f"{product_root}R2"):
        contract = getattr(group, fallback_code, None)
        if contract:
            return contract

    raise RuntimeError(f"找不到 {product_root} 近月契約或連續契約。")


def get_txf_kbar_contract(api):
    return get_near_month_futures_contract(api, "TXF")


def get_micro_txf_kbar_contract(api):
    return get_near_month_futures_contract(api, DEFAULT_FUTURES_ROOT)


def _legacy_get_txf_contract(api, contract_code="TXFR1"):
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


def _contract_label(contract):
    code = getattr(contract, "code", "") or "UNKNOWN"
    delivery = getattr(contract, "delivery_date", "") or ""
    return f"{code} {delivery}".strip()


def _snapshot_timestamp(snapshot):
    for field_name in ("ts", "datetime", "time", "date"):
        value = getattr(snapshot, field_name, None)
        if value:
            return str(value)
    return ""


def get_realtime_data_from_sinopac(api, product_root=DEFAULT_FUTURES_ROOT, contract_code=None):
    if api is None:
        return {
            "current_price": 0.0,
            "last_price": 0.0,
            "bid_price": 0.0,
            "ask_price": 0.0,
            "bid_volume": 0,
            "ask_volume": 0,
            "spread": 0.0,
            "volume": 0,
            "vwap": 0.0,
            "vix": 0.0,
            "source": "Sinopac",
            "updated_at": None,
            "quote_received_at": None,
            "exchange_timestamp": "",
            "error": "尚未登入永豐 API。",
        }

    try:
        contract = get_futures_contract(api, product_root, contract_code)
        snapshots = api.snapshots([contract])

        if not snapshots:
            return {
                "current_price": 0.0,
                "last_price": 0.0,
                "bid_price": 0.0,
                "ask_price": 0.0,
                "bid_volume": 0,
                "ask_volume": 0,
                "spread": 0.0,
                "volume": 0,
                "vwap": 0.0,
                "vix": 0.0,
                "source": f"Sinopac {product_root} snapshot",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "quote_received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "exchange_timestamp": "",
                "error": "永豐 Shioaji snapshot 無資料，可能非交易時段或尚未登入成功。",
                "contract_code": getattr(contract, "code", ""),
                "delivery_date": getattr(contract, "delivery_date", ""),
            }

        snapshot = snapshots[0]
        bid_price = _first_price(getattr(snapshot, "buy_price", 0), getattr(snapshot, "bid_price", 0))
        ask_price = _first_price(getattr(snapshot, "sell_price", 0), getattr(snapshot, "ask_price", 0))
        bid_volume = int(_first_price(getattr(snapshot, "buy_volume", 0), getattr(snapshot, "bid_volume", 0)))
        ask_volume = int(_first_price(getattr(snapshot, "sell_volume", 0), getattr(snapshot, "ask_volume", 0)))
        close = _first_price(
            getattr(snapshot, "close", 0),
            bid_price,
            ask_price,
        )
        spread = ask_price - bid_price if ask_price > 0 and bid_price > 0 else 0.0

        return {
            "current_price": close,
            "last_price": close,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "bid_volume": bid_volume,
            "ask_volume": ask_volume,
            "spread": spread,
            "volume": int(_first_price(getattr(snapshot, "total_volume", 0), getattr(snapshot, "volume", 0))),
            "vwap": _first_price(getattr(snapshot, "average_price", 0), close),
            "vix": 0.0,
            "source": f"Sinopac {_contract_label(contract)} snapshot",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "quote_received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "exchange_timestamp": _snapshot_timestamp(snapshot),
            "error": None if close > 0 else "永豐 snapshot 未提供有效成交價。",
            "contract_code": getattr(contract, "code", ""),
            "delivery_date": getattr(contract, "delivery_date", ""),
        }
    except Exception as exc:
        return {
            "current_price": 0.0,
            "last_price": 0.0,
            "bid_price": 0.0,
            "ask_price": 0.0,
            "bid_volume": 0,
            "ask_volume": 0,
            "spread": 0.0,
            "volume": 0,
            "vwap": 0.0,
            "vix": 0.0,
            "source": "Sinopac snapshot",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "quote_received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "exchange_timestamp": "",
            "error": f"永豐行情讀取失敗：{exc}",
            "contract_code": "",
            "delivery_date": "",
        }


def get_recent_futures_kbars(api, days=60, product_root=DEFAULT_FUTURES_ROOT):
    if api is None:
        return pd.DataFrame(), "尚未登入永豐 API。"

    try:
        contract = get_near_month_futures_contract(api, product_root)
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
        df.attrs["delivery_date"] = getattr(contract, "delivery_date", "")
        df.attrs["product_root"] = product_root
        df.attrs["source"] = "Sinopac Shioaji kbars"
        df.attrs["days"] = days
        return df, None
    except Exception as exc:
        return pd.DataFrame(), f"永豐 kbars 讀取失敗：{exc}"


def get_recent_txf_kbars(api, days=60):
    return get_recent_futures_kbars(api, days=days, product_root="TXF")


def get_recent_micro_txf_kbars(api, days=60):
    return get_recent_futures_kbars(api, days=days, product_root=DEFAULT_FUTURES_ROOT)


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


def _quote_enum(name):
    direct = getattr(sj, name, None) if sj is not None else None
    if direct is not None:
        return direct
    constants = getattr(sj, "constant", None) if sj is not None else None
    return getattr(constants, name, None)


def subscribe_futures_order_flow(
    api,
    tick_callback,
    bidask_callback,
    product_roots=("TXF", "MXF", "TMF"),
):
    """Subscribe to futures Tick/BidAsk streams without enabling order entry."""
    if api is None or sj is None:
        return {"subscribed": [], "errors": ["Shioaji API 尚未連線"]}

    errors = []
    subscribed = []

    try:
        tick_decorator = getattr(api, "on_tick_fop_v1", None)
        bidask_decorator = getattr(api, "on_bidask_fop_v1", None)
        if not callable(tick_decorator) or not callable(bidask_decorator):
            raise RuntimeError("目前 Shioaji 版本不支援期貨 v1 串流 callback")

        @tick_decorator()
        def _on_tick(tick):
            tick_callback(None, tick)

        @bidask_decorator()
        def _on_bidask(quote):
            bidask_callback(None, quote)

        # Shioaji is a slots-based extension type, so references live here.
        _FLOW_CALLBACKS[id(api)] = (_on_tick, _on_bidask)
    except Exception as exc:
        return {"subscribed": [], "errors": [f"逐筆 callback 設定失敗：{exc}"]}

    quote_type = _quote_enum("QuoteType")
    quote_version = _quote_enum("QuoteVersion")
    if quote_type is None:
        return {"subscribed": [], "errors": ["找不到 Shioaji QuoteType"]}

    subscriber = getattr(api, "subscribe", None)
    if not callable(subscriber):
        subscriber = getattr(getattr(api, "quote", None), "subscribe", None)
    if not callable(subscriber):
        return {"subscribed": [], "errors": ["目前 Shioaji 版本沒有行情訂閱介面"]}

    for root in product_roots:
        try:
            contract = get_futures_contract(api, root)
            for kind in ("Tick", "BidAsk"):
                kwargs = {"quote_type": getattr(quote_type, kind)}
                if quote_version is not None and hasattr(quote_version, "v1"):
                    kwargs["version"] = quote_version.v1
                try:
                    subscriber(contract, **kwargs)
                except TypeError:
                    kwargs.pop("version", None)
                    subscriber(contract, **kwargs)
            subscribed.append(
                {
                    "product_root": root,
                    "contract": contract,
                    "code": getattr(contract, "code", ""),
                    "target_code": getattr(contract, "target_code", ""),
                }
            )
        except Exception as exc:
            errors.append(f"{root} 串流訂閱失敗：{exc}")

    return {"subscribed": subscribed, "errors": errors}


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
