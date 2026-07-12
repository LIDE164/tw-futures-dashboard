import os
import inspect
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import charting
from indicators import build_tech_data
from market_session import TAIPEI, format_datetime, get_market_status
from market_data import get_public_market_data
from paper_broker import PaperBroker
from risk_manager import evaluate_entry_risk
from scoring import get_decision_score
from strategy import StrategyManager
import sinopac_api

try:
    import storage
except Exception:
    storage = None

try:
    import backtester
except Exception as exc:
    backtester = None
    BACKTESTER_IMPORT_ERROR = str(exc)
else:
    BACKTESTER_IMPORT_ERROR = ""


def _backtester_unavailable(*args, **kwargs):
    return pd.DataFrame(), pd.DataFrame(), {"error": f"回測模組尚未載入：{BACKTESTER_IMPORT_ERROR or '版本不完整'}"}


run_backtest = getattr(backtester, "run_backtest", _backtester_unavailable)
optimize_backtest_parameters = getattr(backtester, "optimize_backtest_parameters", lambda *args, **kwargs: pd.DataFrame())
optimize_then_validate = getattr(backtester, "optimize_then_validate", lambda *args, **kwargs: pd.DataFrame())
walk_forward_validate = getattr(backtester, "walk_forward_validate", lambda *args, **kwargs: pd.DataFrame())


st.set_page_config(
    page_title="期權戰情室",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed",
)


PRODUCT_NAME = "微型臺指近月"
PRODUCT_ROOT = getattr(sinopac_api, "DEFAULT_FUTURES_ROOT", "TMF")
CONTRACT_MULTIPLIER = 10
DEFAULT_QUANTITY = 1
SIGNAL_TIMEFRAME = "15min"
PLOT_CONFIG = getattr(
    charting,
    "PLOT_CONFIG",
    {
        "displayModeBar": False,
        "scrollZoom": False,
        "doubleClick": False,
        "responsive": True,
    },
)


def format_contracts(value):
    return f"{value:,} 口" if value else "無資料"


def metric_delta(value):
    if value > 0:
        return "淨多"
    if value < 0:
        return "淨空"
    return "待更新"


def format_optional_number(value, suffix=""):
    if value in (None, ""):
        return "無資料"
    return f"{value:,.0f}{suffix}"


def format_price(value):
    return f"{float(value):,.0f}" if value else "無資料"


def format_money(value):
    return f"NT$ {float(value):,.0f}" if value not in (None, "") else "無資料"


def is_streamlit_cloud_runtime():
    cwd = Path.cwd().as_posix().lower()
    home = str(Path.home()).lower()
    cloud_markers = (
        os.getenv("STREAMLIT_CLOUD"),
        os.getenv("STREAMLIT_SHARING"),
        os.getenv("STREAMLIT_SERVER_HEADLESS") if cwd.startswith("/mount/src") else "",
    )
    return any(cloud_markers) or cwd.startswith("/mount/src") or "adminuser" in home


def round_to_tick(value, tick=5):
    if value <= 0:
        return 0.0
    return round(float(value) / tick) * tick


def effective_risk_points(tech_data, fixed_stop, fixed_take, enabled=True, atr_multiplier=1.2, rr_ratio=2.0):
    fixed_stop = float(fixed_stop)
    fixed_take = float(fixed_take)
    if not enabled:
        return fixed_stop, fixed_take, "固定點數"

    atr_points = float(tech_data.get("ATR") or 0)
    if atr_points <= 0:
        return fixed_stop, fixed_take, "ATR資料不足，使用固定點數"

    stop_points = round_to_tick(max(20, min(180, atr_points * float(atr_multiplier))))
    take_points = round_to_tick(max(stop_points, min(360, stop_points * float(rr_ratio))))
    return stop_points, take_points, f"ATR動態：ATR {atr_points:.0f} 點"


def resample_signal_kbars(df, rule=SIGNAL_TIMEFRAME):
    if df is None or df.empty or "ts" not in df.columns:
        return df

    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in df.columns for column in required):
        return df

    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
    out = (
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
    current_bucket = pd.Timestamp.now(tz=TAIPEI).tz_localize(None).floor(rule)
    out = out[out["ts"] < current_bucket].copy()
    out.attrs.update(df.attrs)
    out.attrs["signal_timeframe"] = rule
    return out


def latest_completed_bar_text(df):
    if df is None or df.empty or "ts" not in df.columns:
        return "無資料"
    latest_ts = pd.to_datetime(df["ts"], errors="coerce").dropna()
    if latest_ts.empty:
        return "無資料"
    return latest_ts.iloc[-1].strftime("%Y/%m/%d %H:%M")


def data_age_seconds(updated_at):
    if not updated_at:
        return None
    try:
        updated = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
        return max(0, int((datetime.now() - updated).total_seconds()))
    except ValueError:
        return None


def data_freshness_label(age_seconds):
    if age_seconds is None:
        return "未知"
    if age_seconds <= 30:
        return f"即時，{age_seconds} 秒前"
    return f"可能過期，{age_seconds} 秒前"


def empty_institutional_data(error=None):
    return {"外資": 0, "投信": 0, "自營商": 0, "date": None, "error": error}


def empty_pc_ratio():
    return {"oi_ratio": None, "volume_ratio": None, "source": "TAIFEX", "date": None, "error": None}


def empty_option_levels():
    return {
        "call_pressure": None,
        "call_oi": None,
        "put_support": None,
        "put_oi": None,
        "source": "TAIFEX",
        "date": None,
        "expiry": None,
        "error": None,
    }


def empty_mtx_net():
    return {"net_oi": None, "long_short_ratio": None, "source": "TAIFEX", "date": None, "error": None}


def get_simulation_default_safe():
    getter = getattr(sinopac_api, "get_simulation_default", None)
    return getter() if getter else True


def has_credentials_safe(api_key="", secret_key=""):
    checker = getattr(sinopac_api, "has_credentials", None)
    return checker(api_key, secret_key) if checker else bool(api_key and secret_key)


def restore_paper_broker_state_safe(broker):
    func = getattr(storage, "restore_paper_broker_state", None)
    return func(broker) if func else broker


def save_paper_broker_state_safe(broker):
    func = getattr(storage, "save_paper_broker_state", None)
    if func:
        func(broker)


def clear_paper_broker_state_safe():
    func = getattr(storage, "clear_paper_broker_state", None)
    if func:
        func()


def get_worker_heartbeat_safe():
    func = getattr(storage, "get_worker_heartbeat", None)
    heartbeat = func() if func else {}
    if heartbeat:
        return heartbeat

    heartbeat_path = Path("data/signal_worker_heartbeat.txt")
    if not heartbeat_path.exists():
        return {}

    result = {}
    for line in heartbeat_path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    if result:
        return {
            "worker_name": "signal_worker",
            "updated_at": result.get("updated_at", ""),
            "status": result.get("status", ""),
            "detail": result.get("detail", ""),
        }
    return {}


def evaluate_entry_risk_compatible(action, broker, market_status, **kwargs):
    try:
        signature = inspect.signature(evaluate_entry_risk)
        params = signature.parameters
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if not accepts_kwargs:
            kwargs = {key: value for key, value in kwargs.items() if key in params}
    except (TypeError, ValueError):
        pass

    try:
        return evaluate_entry_risk(action, broker, market_status, **kwargs)
    except TypeError as exc:
        unsupported_kwarg = "unexpected keyword argument" in str(exc)
        if not unsupported_kwarg:
            raise
        safe_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key
            in {
                "now",
                "max_daily_trades",
                "max_daily_loss",
                "max_consecutive_losses",
                "no_new_entry_before_close_minutes",
            }
        }
        return evaluate_entry_risk(action, broker, market_status, **safe_kwargs)


def get_recent_signals_safe(limit=20):
    func = getattr(storage, "get_recent_signals", None)
    return func(limit) if func else []


def get_recent_alerts_safe(limit=20):
    func = getattr(storage, "get_recent_alerts", None)
    return func(limit) if func else []


def get_realtime_data_safe(api, product_root=PRODUCT_ROOT):
    default_data = {
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
        "contract_code": "",
        "delivery_date": "",
        "error": None,
    }

    if api is None:
        default_data["error"] = "尚未登入永豐 API。"
        return default_data

    getter = getattr(sinopac_api, "get_realtime_data_from_sinopac", None)
    if getter is None:
        default_data["error"] = "sinopac_api.py 缺少 get_realtime_data_from_sinopac。"
        return default_data

    try:
        return getter(api, product_root=product_root)
    except TypeError:
        return getter(api)


def get_micro_kbars_safe(api):
    getter = getattr(sinopac_api, "get_recent_micro_txf_kbars", None)
    if getter:
        return getter(api)

    fallback = getattr(sinopac_api, "get_recent_txf_kbars", None)
    if fallback:
        df, error = fallback(api)
        warning = "目前 sinopac_api.py 尚未提供微型臺指 K 線函式，暫時退回舊版 K 線來源。"
        return df, f"{warning} {error or ''}".strip()

    return pd.DataFrame(), "sinopac_api.py 缺少 K 線讀取函式。"


def prepare_daily_chart_safe(kbars):
    func = getattr(charting, "prepare_daily_chart", None)
    if func:
        return func(kbars)

    if kbars is None or kbars.empty or "ts" not in kbars.columns:
        return pd.DataFrame()
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(kbars.columns)):
        return pd.DataFrame()

    df = kbars.copy()
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    daily = (
        df.dropna(subset=["ts"])
        .set_index("ts")
        .resample("1D")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna()
    )
    daily["MA5"] = daily["Close"].rolling(5).mean()
    daily["MA10"] = daily["Close"].rolling(10).mean()
    daily["MA20"] = daily["Close"].rolling(20).mean()
    return daily.tail(35).reset_index()


def build_daily_chart_safe(kbars):
    func = getattr(charting, "build_daily_chart", None)
    return func(kbars) if func else None


def build_signal_chart_safe(kbars, trade_plan):
    func = getattr(charting, "build_signal_chart", None)
    return func(kbars, trade_plan) if func else None


def action_to_text(action, paper_position=0):
    if action == "BUY_LONG":
        return "建議做多"
    if action == "SELL_SHORT":
        return "建議做空"
    if action == "CLOSE_LONG":
        return "建議平多"
    if action == "CLOSE_SHORT":
        return "建議回補"
    if paper_position > 0:
        return "多單續抱"
    if paper_position < 0:
        return "空單續抱"
    return "先觀望"


def signal_state(action, paper_position=0):
    if action in {"BUY_LONG", "CLOSE_SHORT"} or paper_position > 0:
        return "success"
    if action in {"SELL_SHORT", "CLOSE_LONG"} or paper_position < 0:
        return "error"
    return "info"


def friendly_reason(reason):
    mapping = {
        "技術資料不足": "技術資料還不完整，先不要急著判斷方向",
        "訊號成立": "短線訊號轉強，盤勢有發動跡象",
        "布林下軌支撐": "價格靠近支撐區，有機會止跌反彈",
        "MACD好轉": "短線動能轉強",
        "MACD轉弱": "短線動能轉弱，操作上要保守",
        "量增": "成交量放大，代表這波走勢較有力",
        "回測支撐": "回檔後有支撐，買盤承接尚可",
        "外資期貨淨多單偏強": "外資期貨偏多，對多方有利",
        "外資期貨淨空單偏強": "外資期貨偏空，操作上要保守",
        "外資期貨淨多單": "外資略偏多",
        "外資期貨淨空單": "外資略偏空",
    }
    for key, value in mapping.items():
        if key in reason:
            return value
    return reason


def summarize_reasons(reasons, fallback_message, limit=3):
    plain_reasons = [friendly_reason(reason) for reason in reasons if reason]
    if not plain_reasons:
        plain_reasons = [fallback_message]
    return plain_reasons[:limit]


def estimated_fill_price(action, price, slippage_points, realtime=None):
    realtime = realtime or {}
    if action == "BUY_LONG":
        return float(realtime.get("ask_price") or 0) or price + slippage_points
    if action == "SELL_SHORT":
        return float(realtime.get("bid_price") or 0) or price - slippage_points
    return price


def build_trade_plan(
    action,
    current_price,
    strategy,
    paper_broker,
    system_mode,
    market_status,
    realtime=None,
    slippage_points=0.0,
):
    stop_gap = float(strategy.stop_loss_points)
    reward_gap = float(strategy.take_profit_points)
    has_paper_position = system_mode in {"模擬盤模式", "實盤觀察模式"} and paper_broker.position != 0
    position_source = "模擬帳本" if system_mode == "模擬盤模式" else "手動同步部位"
    entry_price = paper_broker.entry_price if has_paper_position else current_price

    plan = {
        "title": action_to_text(action, paper_broker.position if has_paper_position else 0),
        "summary": "目前沒有明確進場條件，先觀望。",
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "close_rule": "等待分數突破進場門檻後再規劃。",
    }

    if not market_status.is_open:
        if has_paper_position:
            direction = "多單" if paper_broker.position > 0 else "空單"
            plan.update(
                {
                    "title": "休市｜持倉風險",
                    "summary": f"市場目前為{market_status.label}，{position_source}仍持有{direction}。休市期間無法保證按停損或停利價成交。",
                    "entry_price": paper_broker.entry_price,
                    "stop_loss": paper_broker.stop_loss_price or None,
                    "take_profit": paper_broker.take_profit_price or None,
                    "close_rule": "下次開盤若跳空，實際損益可能大於原本停損設定；開盤後請重新檢查部位。",
                }
            )
            return plan

        if action == "BUY_LONG":
            next_bias = "偏多"
        elif action == "SELL_SHORT":
            next_bias = "偏空"
        else:
            next_bias = "觀望"

        plan.update(
            {
                "title": f"{market_status.label}｜下次開盤觀察{next_bias}",
                "summary": "目前不是交易時段，不提供可直接成交的進場價。開盤後需等待第一根完整 15 分 K 再重新確認。",
                "entry_price": None,
                "stop_loss": None,
                "take_profit": None,
                "close_rule": "開盤後重新計算進場、停損與停利；若跳空過大，原本訊號視為失效。",
            }
        )
        return plan

    if action == "BUY_LONG":
        fill_price = estimated_fill_price(action, current_price, slippage_points, realtime)
        plan.update(
            {
                "summary": "目前偏多，若要進場，請用做多計畫控管風險。",
                "entry_price": fill_price,
                "stop_loss": fill_price - stop_gap,
                "take_profit": fill_price + reward_gap,
                "close_rule": f"跌破 {fill_price - stop_gap:,.0f}、碰到 {fill_price + reward_gap:,.0f}，或評分跌破 {strategy.long_exit_score} 分就先平倉。",
            }
        )
    elif action == "SELL_SHORT":
        fill_price = estimated_fill_price(action, current_price, slippage_points, realtime)
        plan.update(
            {
                "summary": "目前偏空，若要進場，請用做空計畫控管風險。",
                "entry_price": fill_price,
                "stop_loss": fill_price + stop_gap,
                "take_profit": fill_price - reward_gap,
                "close_rule": f"漲破 {fill_price + stop_gap:,.0f}、碰到 {fill_price - reward_gap:,.0f}，或評分升破 {strategy.short_exit_score} 分就先回補。",
            }
        )
    elif action == "CLOSE_LONG":
        plan.update(
            {
                "summary": "目前建議優先處理既有多單，不建議再追價。",
                "entry_price": entry_price if entry_price else None,
                "take_profit": current_price,
                "close_rule": "以目前價格附近作為平倉參考，等待下一次有效進場訊號。",
            }
        )
    elif action == "CLOSE_SHORT":
        plan.update(
            {
                "summary": "目前建議優先處理既有空單，不建議再追空。",
                "entry_price": entry_price if entry_price else None,
                "take_profit": current_price,
                "close_rule": "以目前價格附近作為回補參考，等待下一次有效進場訊號。",
            }
        )
    elif has_paper_position and paper_broker.position > 0:
        plan.update(
            {
                "summary": f"{position_source}目前持有多單，先照原計畫觀察。",
                "entry_price": paper_broker.entry_price,
                "stop_loss": paper_broker.stop_loss_price or paper_broker.entry_price - stop_gap,
                "take_profit": paper_broker.take_profit_price or paper_broker.entry_price + reward_gap,
                "close_rule": f"跌破 {paper_broker.stop_loss_price or paper_broker.entry_price - stop_gap:,.0f}、碰到 {paper_broker.take_profit_price or paper_broker.entry_price + reward_gap:,.0f}，或評分跌破 {strategy.long_exit_score} 分就先平倉。",
            }
        )
    elif has_paper_position and paper_broker.position < 0:
        plan.update(
            {
                "summary": f"{position_source}目前持有空單，先照原計畫觀察。",
                "entry_price": paper_broker.entry_price,
                "stop_loss": paper_broker.stop_loss_price or paper_broker.entry_price + stop_gap,
                "take_profit": paper_broker.take_profit_price or paper_broker.entry_price - reward_gap,
                "close_rule": f"漲破 {paper_broker.stop_loss_price or paper_broker.entry_price + stop_gap:,.0f}、碰到 {paper_broker.take_profit_price or paper_broker.entry_price - reward_gap:,.0f}，或評分升破 {strategy.short_exit_score} 分就先回補。",
            }
        )

    return plan


@st.cache_data(ttl=300, show_spinner=False)
def load_public_market_data():
    return get_public_market_data()


def configure_strategy(
    strategy,
    long_entry_score,
    short_entry_score,
    stop_loss_points,
    take_profit_points,
    score_exit_requires_profit=False,
    min_score_exit_profit_points=0,
):
    strategy.update_config(
        long_entry_score=long_entry_score,
        short_entry_score=short_entry_score,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
        score_exit_requires_profit=score_exit_requires_profit,
        min_score_exit_profit_points=min_score_exit_profit_points,
    )


with st.sidebar:
    st.subheader("系統模式")
    system_mode = st.radio(
        "選擇用途",
        ["實盤觀察模式", "模擬盤模式", "回測模式"],
        help="本系統只做策略研究、模擬交易與手動下單輔助，不會送出真實委託。",
    )
    st.caption("永豐 API 僅用於行情、K 線與帳務參考；實際下單請自行在券商軟體操作。")

    st.divider()
    st.subheader("永豐 Shioaji")
    simulation_mode = st.checkbox("使用模擬模式", value=get_simulation_default_safe())
    st.caption("API Key 會自動讀取 `.env`、環境變數或 Streamlit Secrets。")
    sj_api_key = ""
    sj_secret_key = ""
    with st.expander("開發者設定", expanded=False):
        st.caption("只有需要臨時覆寫 `.env` 或 Secrets 時才填。")
        sj_api_key = st.text_input("SJ API Key 覆寫", type="password")
        sj_secret_key = st.text_input("SJ Secret Key 覆寫", type="password")
    if simulation_mode:
        st.info("若使用正式永豐 API Key 仍收不到行情或帳務資料，請先取消「使用模擬模式」再重新整理。")

    st.divider()
    st.subheader("策略參數")
    st.caption(f"商品固定：{PRODUCT_NAME}｜訊號週期：15 分鐘｜模擬口數固定 1 口")
    long_entry_score = st.slider("多單進場分數", 50, 80, 62)
    short_entry_score = st.slider("空單進場分數", 20, 50, 35)
    stop_loss_points = st.number_input("停損點數", min_value=10, max_value=300, value=50, step=10)
    take_profit_points = st.number_input("停利點數", min_value=10, max_value=600, value=100, step=10)
    adaptive_risk_mode = st.checkbox("使用 ATR 動態停損 / 停利", value=True)
    atr_stop_multiplier = st.slider("ATR 停損倍數", 0.8, 2.5, 1.2, 0.1)
    reward_risk_ratio = st.slider("停利風險倍數", 1.0, 3.0, 2.2, 0.1)
    min_entry_rr = st.slider("最低進場風險報酬比", 1.0, 3.0, 1.5, 0.1)
    reject_choppy_entry = st.checkbox("盤整盤禁止新進場", value=True)
    require_60m_alignment = st.checkbox("進場需符合 60 分趨勢", value=True)
    min_entry_adx = st.slider("最低 ADX 趨勢強度", 10, 35, 22)
    min_entry_volume_ratio = st.slider("最低量比", 0.5, 1.5, 1.0, 0.05)
    max_chase_atr = st.slider("最大追價距離 ATR", 0.5, 3.0, 1.0, 0.1)
    confirmation_bars = st.slider("進場連續確認 K 數", 1, 3, 2)
    cooldown_bars = st.slider("平倉後冷卻 K 數", 0, 5, 2)
    allow_long = st.checkbox("允許做多", value=True)
    allow_short = st.checkbox("允許做空", value=False)
    breakeven_trigger_r = st.slider("保本觸發 R", 0.0, 2.0, 1.0, 0.1)
    breakeven_buffer_points = st.number_input("保本加點", min_value=0.0, max_value=50.0, value=0.0, step=1.0)
    max_holding_bars = st.slider("最長持倉 K 數", 0, 60, 24)
    score_exit_requires_profit = st.checkbox("評分反轉出場需先浮盈", value=True)
    min_score_exit_profit_points = st.number_input("評分出場最低浮盈點", min_value=0.0, max_value=200.0, value=0.0, step=5.0)

    st.divider()
    st.subheader("模擬 / 回測成本")
    contract_multiplier = CONTRACT_MULTIPLIER
    paper_quantity = DEFAULT_QUANTITY
    st.caption(f"微型臺指固定乘數：每點 {CONTRACT_MULTIPLIER} 元｜口數：{DEFAULT_QUANTITY} 口")
    slippage_points = st.number_input("滑價點數", min_value=1.0, max_value=20.0, value=2.0, step=0.5)
    commission_per_side = st.number_input("單邊手續費", min_value=1.0, max_value=500.0, value=20.0, step=1.0)

    manual_position = 0
    manual_entry_price = 0.0
    manual_stop_loss_price = 0.0
    manual_take_profit_price = 0.0
    if system_mode == "實盤觀察模式":
        st.divider()
        st.subheader("手動部位同步")
        st.caption("只用來提醒與顯示，不會送出任何委託。")
        manual_side = st.selectbox("目前手動部位", ["空手", "多單 1 口", "空單 1 口"])
        manual_position = 1 if manual_side == "多單 1 口" else -1 if manual_side == "空單 1 口" else 0
        if manual_position:
            manual_entry_price = st.number_input("實際成交價", min_value=0.0, value=0.0, step=1.0)
            manual_stop_loss_price = st.number_input("手動停損價", min_value=0.0, value=0.0, step=1.0)
            manual_take_profit_price = st.number_input("手動停利價", min_value=0.0, value=0.0, step=1.0)
            if manual_entry_price <= 0:
                st.warning("請填入實際成交價，系統才會啟用手動部位提醒。")

    if st.button("重新整理資料", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


api, api_error = sinopac_api.get_api(
    simulation=simulation_mode,
    api_key=sj_api_key,
    secret_key=sj_secret_key,
)

with st.sidebar:
    if api_error:
        st.error("永豐行情尚未連線")
    else:
        st.success("永豐行情已連線")

with st.spinner("更新市場資料中..."):
    public_data = load_public_market_data()
    public_data = {
        "pc_ratio": public_data.get("pc_ratio") or empty_pc_ratio(),
        "option_levels": public_data.get("option_levels") or empty_option_levels(),
        "mtx_net": public_data.get("mtx_net") or empty_mtx_net(),
        "txf_institutional": public_data.get("txf_institutional")
        or empty_institutional_data("TAIFEX 法人籌碼資料尚未載入，請重新整理後再試。"),
        "errors": public_data.get("errors", []),
    }
    oi_data = public_data.get(
        "txf_institutional",
        empty_institutional_data("TAIFEX 法人籌碼資料尚未載入，請重新部署或清除快取後再試。"),
    )
    realtime = get_realtime_data_safe(api, product_root=PRODUCT_ROOT)
    raw_kbars, kbars_error = get_micro_kbars_safe(api)
    kbars = resample_signal_kbars(raw_kbars, SIGNAL_TIMEFRAME)


data_warnings = [
    api_error,
    realtime.get("error"),
    kbars_error,
    oi_data.get("error"),
    *public_data.get("errors", []),
]

market_status = get_market_status()

current_price = realtime["current_price"]
tech_data = build_tech_data(kbars, realtime)
volatility_30d = float(tech_data.get("30日年化波動率") or 0)
effective_stop_loss_points, effective_take_profit_points, risk_model_label = effective_risk_points(
    tech_data,
    stop_loss_points,
    take_profit_points,
    adaptive_risk_mode,
    atr_stop_multiplier,
    reward_risk_ratio,
)
score, label, reasons, feature = get_decision_score(tech_data, inst_data=oi_data, with_reason=True)

if "strategy" not in st.session_state:
    st.session_state.strategy = StrategyManager()

configure_strategy(
    st.session_state.strategy,
    long_entry_score,
    short_entry_score,
    effective_stop_loss_points,
    effective_take_profit_points,
    score_exit_requires_profit,
    min_score_exit_profit_points,
)

if "paper_broker" not in st.session_state:
    st.session_state.paper_broker = PaperBroker(
        multiplier=contract_multiplier,
        commission_per_side=commission_per_side,
        slippage_points=slippage_points,
    )
    restore_paper_broker_state_safe(st.session_state.paper_broker)

paper_broker = st.session_state.paper_broker
paper_broker.multiplier = contract_multiplier
paper_broker.commission_per_side = commission_per_side
paper_broker.slippage_points = slippage_points

manual_position_enabled = system_mode == "實盤觀察模式" and manual_position and manual_entry_price > 0
active_broker = paper_broker
if manual_position_enabled:
    active_broker = PaperBroker(
        multiplier=contract_multiplier,
        commission_per_side=commission_per_side,
        slippage_points=slippage_points,
        position=manual_position,
        entry_price=manual_entry_price,
        stop_loss_price=manual_stop_loss_price,
        take_profit_price=manual_take_profit_price,
    )

if system_mode == "模擬盤模式":
    st.session_state.strategy.sync_position(
        paper_broker.position,
        paper_broker.entry_price,
        paper_broker.stop_loss_price,
        paper_broker.take_profit_price,
    )
elif manual_position_enabled:
    st.session_state.strategy.sync_position(
        active_broker.position,
        active_broker.entry_price,
        active_broker.stop_loss_price,
        active_broker.take_profit_price,
    )
else:
    st.session_state.strategy.sync_position(0, 0.0)
action, msg = st.session_state.strategy.decide_action(score, current_price)
reference_stop = None
if action == "BUY_LONG":
    reference_stop = current_price - effective_stop_loss_points
elif action == "SELL_SHORT":
    reference_stop = current_price + effective_stop_loss_points
elif paper_broker.position > 0 and system_mode == "模擬盤模式":
    reference_stop = paper_broker.entry_price - effective_stop_loss_points
elif paper_broker.position < 0 and system_mode == "模擬盤模式":
    reference_stop = paper_broker.entry_price + effective_stop_loss_points


plain_reasons = summarize_reasons(reasons, msg, limit=3)
trade_plan = build_trade_plan(
    action,
    current_price,
    st.session_state.strategy,
    active_broker,
    system_mode,
    market_status,
    realtime,
    slippage_points,
)
risk_decision = evaluate_entry_risk_compatible(
    action,
    paper_broker,
    market_status,
    min_reward_risk_ratio=min_entry_rr,
    entry_price=trade_plan["entry_price"] or 0,
    stop_loss_price=trade_plan["stop_loss"] or 0,
    take_profit_price=trade_plan["take_profit"] or 0,
    nearest_resistance=tech_data.get("上方壓力") or 0,
    nearest_support=tech_data.get("下方支撐") or 0,
    tech_data=tech_data,
    reject_choppy=reject_choppy_entry,
    require_60m_alignment=require_60m_alignment,
    min_adx=min_entry_adx,
    min_volume_ratio=min_entry_volume_ratio,
    max_chase_atr=max_chase_atr,
)
if action in {"BUY_LONG", "SELL_SHORT"} and not getattr(risk_decision, "allowed", False):
    trade_plan["title"] = "訊號成立｜風控禁止進場"
    trade_plan["summary"] = "策略訊號成立，但目前新手風控規則禁止進場。"
    trade_plan["entry_price"] = None
    trade_plan["stop_loss"] = None
    trade_plan["take_profit"] = None
    trade_plan["close_rule"] = "請先遵守風控，等待下一根完整 15 分 K 或隔日重新評估。"
tone = "info" if not market_status.is_open and active_broker.position == 0 else signal_state(
    action,
    active_broker.position if system_mode in {"模擬盤模式", "實盤觀察模式"} else 0,
)
age_seconds = data_age_seconds(realtime.get("updated_at"))
max_loss_per_contract = effective_stop_loss_points * CONTRACT_MULTIPLIER
estimated_cost = commission_per_side * 2 + slippage_points * 2 * CONTRACT_MULTIPLIER
previous_price = st.session_state.get("previous_price")
price_delta = current_price - previous_price if previous_price and current_price else 0
st.session_state.previous_price = current_price

entry_actions = {"BUY_LONG", "SELL_SHORT"}
data_is_fresh = age_seconds is not None and age_seconds <= 30
entry_signal = action in entry_actions
risk_allowed = bool(getattr(risk_decision, "allowed", False))
risk_reasons = getattr(risk_decision, "reasons", [])
risk_daily_trades = getattr(risk_decision, "daily_trades", 0)
risk_daily_pnl = getattr(risk_decision, "daily_pnl", 0)
risk_consecutive_losses = getattr(risk_decision, "consecutive_losses", 0)
risk_rr = float(getattr(risk_decision, "reward_risk_ratio", 0.0) or 0.0)
can_consider_entry = entry_signal and market_status.is_open and data_is_fresh and risk_allowed
if can_consider_entry:
    execution_status = "可考慮進場"
elif action in {"CLOSE_LONG", "CLOSE_SHORT"}:
    execution_status = "優先處理平倉"
elif entry_signal:
    execution_status = "方向成立，但目前不建議進場"
else:
    execution_status = "先觀望"

contract_text = realtime.get("contract_code") or (kbars.attrs.get("contract_code", "") if hasattr(kbars, "attrs") else "")
delivery_text = realtime.get("delivery_date") or (kbars.attrs.get("delivery_date", "") if hasattr(kbars, "attrs") else "")
st.title("期權戰情室")

with st.container(border=True):
    st.caption(f"{PRODUCT_NAME}｜{contract_text or '無資料'}｜到期 {delivery_text or '無資料'}")
    st.caption(f"{market_status.label}｜行情價格每次刷新更新｜策略訊號只用完整 15 分 K")
    st.metric(
        "最新成交" if market_status.is_open else "最近價格",
        format_price(current_price),
        f"{price_delta:+,.0f} 點" if price_delta else None,
    )
    quote_col1, quote_col2, quote_col3 = st.columns(3)
    quote_col1.metric(
        "立即賣出參考",
        format_price(realtime.get("bid_price")),
        f"{int(realtime.get('bid_volume') or 0)} 口" if realtime.get("bid_volume") else None,
    )
    quote_col2.metric(
        "立即買進參考",
        format_price(realtime.get("ask_price")),
        f"{int(realtime.get('ask_volume') or 0)} 口" if realtime.get("ask_volume") else None,
    )
    quote_col3.metric("買賣價差", f"{float(realtime.get('spread') or 0):,.0f} 點" if realtime.get("spread") else "無資料")
    st.caption(
        f"最後成交：{realtime.get('exchange_timestamp') or 'snapshot 未提供交易所時間'}｜"
        f"API 更新：{data_freshness_label(age_seconds)}｜資料來源：{realtime.get('source', 'fallback')}"
    )
    st.caption("做多看賣一，做空看買一；單一檔掛量只供流動性參考。")

st.caption(
    f"下一次開盤：{format_datetime(market_status.next_open)}｜"
    f"最後有效訊號：{latest_completed_bar_text(kbars)}｜模式：{system_mode}"
)

if age_seconds is None or age_seconds > 30:
    st.error("API 查詢時間可能已過期，請先重新整理，不要直接依照舊畫面操作。")

if not market_status.is_open:
    st.warning("目前不是交易時段；首頁只顯示下次開盤預備計畫，不提供可直接成交的進場價。")

with st.container(border=True):
    st.subheader("操作卡")
    signal_col, status_col = st.columns(2)
    signal_col.metric("目前策略", trade_plan["title"])
    status_col.metric("狀態", execution_status)

    entry_col, stop_col, target_col = st.columns(3)
    entry_col.metric("進場", format_price(trade_plan["entry_price"]))
    stop_col.metric("停損", format_price(trade_plan["stop_loss"]))
    target_col.metric("停利", format_price(trade_plan["take_profit"]))
    risk_col, cost_col = st.columns(2)
    risk_col.metric("1口最大風險", format_money(max_loss_per_contract))
    cost_col.metric("預估來回成本", format_money(estimated_cost))
    st.caption(
        f"風控模型：{risk_model_label}｜停損 {effective_stop_loss_points:.0f} 點｜"
        f"停利 {effective_take_profit_points:.0f} 點｜"
        f"RR {risk_rr:.2f}R｜"
        f"15分趨勢：{tech_data.get('15分趨勢文字', '未知')}｜"
        f"60分趨勢：{tech_data.get('60分趨勢文字', '未知')}"
    )

    st.write("平倉條件")
    st.write(trade_plan["close_rule"])
    signal_message = f"{trade_plan['summary']}\n\n補充：{msg}"
    if tone == "success":
        st.success(signal_message)
    elif tone == "error":
        st.error(signal_message)
    else:
        st.info(signal_message)

    st.write("為什麼這樣建議")
    for item in plain_reasons:
        st.write(f"- {item}")
    if risk_reasons:
        st.write("風控原因")
        for item in risk_reasons:
            st.write(f"- {item}")

st.caption(
    f"風控：今日 {risk_daily_trades}/3 筆｜"
    f"已實現 {format_money(risk_daily_pnl)}｜"
    f"連虧 {risk_consecutive_losses}/2"
)

with st.container(border=True):
    chart_mode = st.radio(
        "圖表",
        ["近一月趨勢", "15 分交易圖"],
        horizontal=True,
        label_visibility="collapsed",
    )
    fig = build_daily_chart_safe(raw_kbars) if chart_mode == "近一月趨勢" else build_signal_chart_safe(kbars, trade_plan)
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True, config=PLOT_CONFIG)
    elif chart_mode == "近一月趨勢" and raw_kbars is not None and not raw_kbars.empty:
        daily = prepare_daily_chart_safe(raw_kbars)
        chart_df = daily.set_index("ts")[["Close", "MA5", "MA10", "MA20"]].dropna(how="all") if not daily.empty else pd.DataFrame()
        if chart_df.empty:
            st.info("尚無足夠日 K 資料可繪圖。")
        else:
            st.line_chart(chart_df)
            st.caption("目前環境未安裝 Plotly，先以收盤線替代 K 線圖。")
    elif kbars is not None and not kbars.empty and "Close" in kbars.columns:
        if chart_mode == "近一月趨勢":
            daily = prepare_daily_chart_safe(raw_kbars)
            chart_df = daily.set_index("ts")[["Close", "MA5", "MA10", "MA20"]].dropna(how="all") if not daily.empty else pd.DataFrame()
        else:
            chart_df = kbars.tail(80).set_index("ts")[["Close"]]
        st.line_chart(chart_df)
        st.caption("目前環境未安裝 Plotly，先以收盤線替代 K 線圖。")
    else:
        st.info("尚無足夠 K 線資料可繪圖。")

with st.expander("進階摘要", expanded=False):
    sum1, sum2 = st.columns(2)
    sum1.metric("綜合評分", score, label)
    sum2.metric("型態特徵", feature)
    sum3, sum4 = st.columns(2)
    sum3.metric("盤中均價線", format_price(realtime.get("vwap")))
    sum4.metric("近30根波動", f"{volatility_30d:.1f}%" if volatility_30d else "無資料")
    sum5, sum6 = st.columns(2)
    sum5.metric("ATR", f"{float(tech_data.get('ATR') or 0):.0f} 點")
    sum6.metric("風險環境", tech_data.get("風險環境", "未知"))
    st.caption(
        f"15分趨勢：{tech_data.get('15分趨勢文字', '未知')}｜"
        f"60分趨勢：{tech_data.get('60分趨勢文字', '未知')}｜"
        f"盤整降權：{'是' if tech_data.get('盤整') else '否'}"
    )
    st.caption(
        f"上方壓力：{format_price(tech_data.get('上方壓力'))}｜"
        f"下方支撐：{format_price(tech_data.get('下方支撐'))}｜"
        f"最低進場 RR：{min_entry_rr:.2f}R"
    )
    st.caption(
        f"進場過濾：ADX≥{min_entry_adx}｜量比≥{min_entry_volume_ratio:.2f}｜"
        f"追價≤{max_chase_atr:.1f}ATR｜"
        f"{'避開盤整' if reject_choppy_entry else '允許盤整'}｜"
        f"{'需順60分' if require_60m_alignment else '不檢查60分'}｜"
        f"確認{confirmation_bars}根｜冷卻{cooldown_bars}根｜"
        f"{'做多' if allow_long else '停多'} / {'做空' if allow_short else '停空'}｜"
        f"保本{breakeven_trigger_r:.1f}R+{breakeven_buffer_points:.0f}｜"
        f"最長{max_holding_bars if max_holding_bars else '不限'}K｜"
        f"{'評分出場需浮盈' if score_exit_requires_profit else '評分反轉即出'}"
    )

page = st.selectbox(
    "查看更多資訊",
    ["新手首頁", "警報服務", "法人籌碼", "選擇權區間", "模擬部位", "回測系統", "帳務參考", "進階診斷"],
)

if page == "新手首頁":
    st.caption("上方已整理目前價格、操作方向、原因、進場參考、停損與目標價。")

elif page == "警報服務":
    st.subheader("背景警報服務")
    st.caption("signal_worker.py 需在另一個行程常駐執行；Streamlit 只顯示狀態與紀錄。")
    cloud_runtime = is_streamlit_cloud_runtime()
    if cloud_runtime:
        st.error(
            "你目前看的像是 Streamlit Cloud。雲端頁面讀不到你電腦本機的 worker 心跳與 SQLite 紀錄，"
            "所以這裡不能用來判斷本機自動發報是否正在跑。請以 Telegram、VS Code 的 "
            "`worker_status.cmd`，或本機 `streamlit run app.py` 為準。"
        )
    else:
        st.success("目前是本機頁面，可以讀取本機 worker 心跳與 SQLite 紀錄。")
    heartbeat = get_worker_heartbeat_safe()
    if heartbeat:
        h1, h2 = st.columns(2)
        h1.metric("Worker 狀態", heartbeat.get("status", "未知"))
        h2.metric("最後心跳", heartbeat.get("updated_at", "無資料"))
        st.write(heartbeat.get("detail", ""))
    else:
        if cloud_runtime:
            st.info("雲端頁面沒有本機 worker 心跳是正常的，請不要用這個狀態判斷本機發報是否停止。")
        else:
            st.warning("尚未收到本機 signal_worker 心跳。請先啟動 `start_worker.cmd`，或用 `worker_status.cmd` 查看。")

    st.write("啟動指令")
    st.code(".\\run_worker.cmd -Interval 30", language="powershell")
    st.write("Windows 背景管理")
    st.code(
        ".\\start_worker.cmd -Interval 30\n"
        ".\\worker_status.cmd\n"
        ".\\stop_worker.cmd",
        language="powershell",
    )
    st.write("測試發報")
    st.code(
        ".\\run_worker.cmd -TestSignal BUY_LONG\n"
        ".\\run_worker.cmd -TestSignal SELL_SHORT\n"
        ".\\run_worker.cmd -TestSignal CLOSE_LONG\n"
        ".\\run_worker.cmd -TestSignal CLOSE_SHORT\n"
        ".\\run_worker.cmd -TestSignal CLOSE_LONG -TestExit TARGET\n"
        ".\\run_worker.cmd -TestSignal CLOSE_SHORT -TestExit TARGET",
        language="powershell",
    )

    recent_signals = get_recent_signals_safe(20)
    st.subheader("最近訊號")
    if recent_signals:
        st.dataframe(pd.DataFrame(recent_signals), use_container_width=True)
    else:
        st.info("尚無訊號紀錄。")

    recent_alerts = get_recent_alerts_safe(20)
    st.subheader("最近警報")
    if recent_alerts:
        st.dataframe(pd.DataFrame(recent_alerts), use_container_width=True)
    else:
        st.info("尚無警報紀錄。")

elif page == "法人籌碼":
    st.subheader("三大法人期貨未平倉")
    st.caption(f"資料日期：{oi_data.get('date') or '待更新'}｜資料來源：TAIFEX")
    col_f, col_t, col_d = st.columns(3)
    col_f.metric("外資及陸資", format_contracts(oi_data.get("外資")), metric_delta(oi_data.get("外資", 0)))
    col_t.metric("投信", format_contracts(oi_data.get("投信")), metric_delta(oi_data.get("投信", 0)))
    col_d.metric("自營商", format_contracts(oi_data.get("自營商")), metric_delta(oi_data.get("自營商", 0)))

elif page == "選擇權區間":
    st.subheader("選擇權最大未平倉量")
    option_levels = public_data["option_levels"]
    st.caption(
        f"資料來源：{option_levels.get('source')}｜"
        f"資料日期：{option_levels.get('date') or '無資料'}｜"
        f"到期月份：{option_levels.get('expiry') or '無資料'}"
    )
    col_call, col_put = st.columns(2)
    col_call.metric(
        "Call 壓力",
        format_optional_number(option_levels.get("call_pressure")),
        f"OI {option_levels.get('call_oi'):,}" if option_levels.get("call_oi") else "無資料",
        delta_color="inverse",
    )
    col_put.metric(
        "Put 支撐",
        format_optional_number(option_levels.get("put_support")),
        f"OI {option_levels.get('put_oi'):,}" if option_levels.get("put_oi") else "無資料",
    )

elif page == "模擬部位":
    st.subheader("模擬帳本")
    st.caption("模擬下單只寫入本機 Streamlit session，不會送出任何真實委託。")
    if system_mode != "模擬盤模式":
        st.info("目前不是模擬盤模式；切換到模擬盤模式後才會執行模擬成交。")

    p1, p2 = st.columns(2)
    p1.metric("模擬部位", paper_broker.position)
    p2.metric("進場價", format_price(paper_broker.entry_price))
    p3, p4 = st.columns(2)
    p3.metric("已實現損益", f"{paper_broker.realized_pnl:,.0f}")
    p4.metric("未實現損益", f"{paper_broker.unrealized_pnl(current_price):,.0f}")

    can_paper_execute = (
        market_status.is_open
        and risk_allowed
        and action in {"BUY_LONG", "SELL_SHORT", "CLOSE_LONG", "CLOSE_SHORT"}
    )
    col_exec, col_reset = st.columns(2)
    with col_exec:
        if st.button(
            f"模擬執行訊號：{trade_plan['title']}",
            disabled=not can_paper_execute or system_mode != "模擬盤模式",
            use_container_width=True,
        ):
            filled, fill_msg = paper_broker.execute(
                action,
                current_price,
                quantity=paper_quantity,
                note=msg,
                stop_loss_price=trade_plan["stop_loss"] or 0,
                take_profit_price=trade_plan["take_profit"] or 0,
            )
            if filled:
                st.session_state.strategy.apply_fill(
                    action,
                    trade_plan["entry_price"] or current_price,
                    paper_quantity,
                    trade_plan["stop_loss"] or 0,
                    trade_plan["take_profit"] or 0,
                )
                save_paper_broker_state_safe(paper_broker)
                st.success(fill_msg)
                st.rerun()
            else:
                st.warning(fill_msg)
    with col_reset:
        if st.button("重置模擬帳本", use_container_width=True):
            paper_broker.reset()
            st.session_state.strategy.reset()
            clear_paper_broker_state_safe()
            st.rerun()

    trades_df = paper_broker.trades_df()
    if trades_df.empty:
        st.info("尚無模擬交易紀錄。")
    else:
        st.dataframe(trades_df, use_container_width=True)

elif page == "回測系統":
    st.subheader("回測系統")
    st.caption("固定使用 15 分鐘 K。法人籌碼暫不納入歷史回測，避免用今日資料回填過去。")
    kbar_source = raw_kbars.attrs.get("source", "未知來源") if hasattr(raw_kbars, "attrs") else "未知來源"
    kbar_contract = raw_kbars.attrs.get("contract_code", "") if hasattr(raw_kbars, "attrs") else ""
    kbar_delivery = raw_kbars.attrs.get("delivery_date", "") if hasattr(raw_kbars, "attrs") else ""
    if raw_kbars is not None and not raw_kbars.empty and "ts" in raw_kbars.columns:
        kbar_ts = pd.to_datetime(raw_kbars["ts"], errors="coerce").dropna()
        if not kbar_ts.empty:
            st.info(
                "回測資料來源："
                f"{kbar_source}｜契約：{kbar_contract or '未知'}"
                f"{f'｜到期：{kbar_delivery}' if kbar_delivery else ''}"
                f"｜原始K線：{len(raw_kbars):,} 根"
                f"｜區間：{kbar_ts.iloc[0].strftime('%Y/%m/%d %H:%M')} ~ {kbar_ts.iloc[-1].strftime('%Y/%m/%d %H:%M')}"
            )

    if raw_kbars.empty:
        st.warning("目前沒有足夠 K 線資料可回測。")
    else:
        trades, equity_curve, summary = run_backtest(
            raw_kbars,
            inst_data={},
            quantity=paper_quantity,
            multiplier=contract_multiplier,
            commission_per_side=commission_per_side,
            slippage_points=slippage_points,
            long_entry_score=long_entry_score,
            short_entry_score=short_entry_score,
            stop_loss_points=stop_loss_points,
            take_profit_points=take_profit_points,
            adaptive_risk=adaptive_risk_mode,
            atr_stop_multiplier=atr_stop_multiplier,
            reward_risk_ratio=reward_risk_ratio,
            min_entry_rr=min_entry_rr,
            reject_choppy=reject_choppy_entry,
            require_60m_alignment=require_60m_alignment,
            min_adx=min_entry_adx,
            min_volume_ratio=min_entry_volume_ratio,
            max_chase_atr=max_chase_atr,
            confirmation_bars=confirmation_bars,
            cooldown_bars=cooldown_bars,
            allow_long=allow_long,
            allow_short=allow_short,
            breakeven_trigger_r=breakeven_trigger_r,
            breakeven_buffer_points=breakeven_buffer_points,
            max_holding_bars=max_holding_bars,
            score_exit_requires_profit=score_exit_requires_profit,
            min_score_exit_profit_points=min_score_exit_profit_points,
            signal_timeframe=SIGNAL_TIMEFRAME,
            include_institutional=False,
        )

        if summary.get("error"):
            st.warning(summary["error"])
        else:
            r1, r2 = st.columns(2)
            r1.metric("總損益", f"{summary['總損益']:,.0f}")
            r2.metric("交易次數", summary["交易次數"])
            r3, r4 = st.columns(2)
            r3.metric("勝率", f"{summary['勝率']:.2f}%", f"90%下限 {summary.get('勝率可信下限90', 0):.2f}%")
            r4.metric("最大回撤", f"{summary['最大回撤']:,.0f}")
            r5, r6 = st.columns(2)
            r5.metric("期望值/筆", f"{summary.get('期望值', 0):,.0f}")
            r6.metric("Profit Factor", f"{summary.get('Profit Factor', 0):.2f}")
            if summary.get("勝率", 0) >= 60 and summary.get("期望值", 0) <= 0:
                st.warning("勝率雖然達 60%，但期望值不是正數，這不是可採用的改善。")
            elif summary.get("勝率", 0) >= 60 and summary.get("勝率可信下限90", 0) < 45:
                st.warning("勝率表面達 60%，但交易樣本仍不夠穩，90%可信下限偏低。")
            elif summary.get("勝率", 0) >= 60 and summary.get("期望值", 0) > 0 and summary.get("Profit Factor", 0) >= 1.2:
                st.success("目前參數在這段資料達到勝率、期望值與 Profit Factor 門檻，請再做樣本外驗證。")
            r7, r8 = st.columns(2)
            r7.metric("多單勝率", f"{summary.get('多單勝率', 0):.2f}%", f"{summary.get('多單交易次數', 0)} 筆")
            r8.metric("空單勝率", f"{summary.get('空單勝率', 0):.2f}%", f"{summary.get('空單交易次數', 0)} 筆")

            st.subheader("回測診斷")
            d1, d2, d3 = st.columns(3)
            d1.metric("停損", summary.get("停損次數", 0), f"{summary.get('停損損益', 0):,.0f}")
            d2.metric("停利", summary.get("停利次數", 0), f"{summary.get('停利損益', 0):,.0f}")
            d3.metric("策略平倉", summary.get("策略平倉次數", 0), f"{summary.get('策略平倉損益', 0):,.0f}")
            for item in summary.get("診斷", []):
                st.write(f"- {item}")
            st.write(summary)
            if not equity_curve.empty:
                st.line_chart(equity_curve.set_index("bar")["equity"])
                st.dataframe(equity_curve.tail(100), use_container_width=True)
            if not trades.empty:
                st.subheader("交易明細")
                st.dataframe(trades, use_container_width=True)

            st.subheader("參數掃描")
            st.caption(
                "掃描會比較期望值趨勢、方向分離、低回撤、高勝率與平衡型參數。"
                "請優先看「穩健正期望 / 每筆期望R / 回撤效率 / Profit Factor / 交易次數」。"
            )
            min_scan_trades = st.number_input("掃描至少交易筆數", min_value=1, max_value=50, value=10, step=1)
            st.caption("排名優先考慮正期望、Profit Factor、回撤效率與樣本可信度；同一段資料勝出仍需通過樣本外驗證。")
            if st.button("掃描提高期望值參數", use_container_width=True):
                backtest_base_kwargs = {
                    "inst_data": {},
                    "quantity": paper_quantity,
                    "multiplier": contract_multiplier,
                    "commission_per_side": commission_per_side,
                    "slippage_points": slippage_points,
                    "stop_loss_points": stop_loss_points,
                    "take_profit_points": take_profit_points,
                    "adaptive_risk": adaptive_risk_mode,
                    "atr_stop_multiplier": atr_stop_multiplier,
                    "reward_risk_ratio": reward_risk_ratio,
                    "min_volume_ratio": min_entry_volume_ratio,
                    "confirmation_bars": confirmation_bars,
                    "cooldown_bars": cooldown_bars,
                    "allow_long": allow_long,
                    "allow_short": allow_short,
                    "breakeven_trigger_r": breakeven_trigger_r,
                    "breakeven_buffer_points": breakeven_buffer_points,
                    "max_holding_bars": max_holding_bars,
                    "score_exit_requires_profit": score_exit_requires_profit,
                    "min_score_exit_profit_points": min_score_exit_profit_points,
                    "signal_timeframe": SIGNAL_TIMEFRAME,
                    "include_institutional": False,
                }
                optimized = optimize_backtest_parameters(
                    raw_kbars,
                    base_kwargs=backtest_base_kwargs,
                    min_trades=min_scan_trades,
                    top_n=10,
                )
                if optimized.empty:
                    st.warning("沒有找到符合最低交易筆數的參數組合。可降低最低交易筆數，或放寬 ADX/RR/追價限制。")
                else:
                    st.dataframe(optimized, use_container_width=True)
                    best = optimized.iloc[0]
                    best_message = (
                        f"第一候選：{best.get('設定類型', '未命名')}｜"
                        f"期望值 {float(best.get('期望值', 0)):,.0f}/筆｜"
                        f"PF {float(best.get('Profit Factor', 0)):.2f}｜"
                        f"最大回撤 {float(best.get('最大回撤', 0)):,.0f}｜"
                        f"交易 {int(best.get('交易次數', 0))} 筆"
                    )
                    if bool(best.get("穩健正期望", False)):
                        st.success(best_message + "。請再執行樣本外驗證。")
                    else:
                        st.warning(best_message + "。目前仍未達穩健正期望門檻，暫時不要套用到警報服務。")
                    hit_count = int(optimized["可信達標"].sum()) if "可信達標" in optimized.columns else 0
                    if hit_count:
                        st.success(f"找到 {hit_count} 組訓練段可信達標組合。下一步請按樣本外驗證確認是否沒有過度最佳化。")
                    else:
                        st.info("目前訓練段尚未找到可信達標組合，可降低最低交易筆數、等待更多 K 線樣本，或接受較低勝率但正期望值的趨勢型策略。")

            if st.button("訓練 / 樣本外驗證", use_container_width=True):
                validation = optimize_then_validate(
                    raw_kbars,
                    base_kwargs={
                        "inst_data": {},
                        "quantity": paper_quantity,
                        "multiplier": contract_multiplier,
                        "commission_per_side": commission_per_side,
                        "slippage_points": slippage_points,
                        "stop_loss_points": stop_loss_points,
                        "take_profit_points": take_profit_points,
                        "adaptive_risk": adaptive_risk_mode,
                        "atr_stop_multiplier": atr_stop_multiplier,
                        "reward_risk_ratio": reward_risk_ratio,
                        "min_volume_ratio": min_entry_volume_ratio,
                        "confirmation_bars": confirmation_bars,
                        "cooldown_bars": cooldown_bars,
                        "allow_long": allow_long,
                        "allow_short": allow_short,
                        "breakeven_trigger_r": breakeven_trigger_r,
                        "breakeven_buffer_points": breakeven_buffer_points,
                        "max_holding_bars": max_holding_bars,
                        "score_exit_requires_profit": score_exit_requires_profit,
                        "min_score_exit_profit_points": min_score_exit_profit_points,
                        "signal_timeframe": SIGNAL_TIMEFRAME,
                        "include_institutional": False,
                    },
                    min_trades=min_scan_trades,
                    top_n=5,
                )
                if validation.empty:
                    st.warning("樣本外資料或交易筆數不足，無法完成訓練/驗證。請增加 K 線天數或降低最低交易筆數。")
                else:
                    st.dataframe(validation, use_container_width=True)
                    oos_hit_count = int(validation["樣本外可信達標"].sum()) if "樣本外可信達標" in validation.columns else 0
                    if oos_hit_count:
                        st.success(f"有 {oos_hit_count} 組樣本外可信達標，才比較接近可用策略。")
                    else:
                        st.warning("目前沒有樣本外可信達標組合；不要只採用訓練段勝率漂亮的參數。")

            if st.button("滾動 Walk-forward 驗證", use_container_width=True):
                walk_forward = walk_forward_validate(
                    raw_kbars,
                    base_kwargs={
                        "inst_data": {},
                        "quantity": paper_quantity,
                        "multiplier": contract_multiplier,
                        "commission_per_side": commission_per_side,
                        "slippage_points": slippage_points,
                        "stop_loss_points": stop_loss_points,
                        "take_profit_points": take_profit_points,
                        "adaptive_risk": adaptive_risk_mode,
                        "atr_stop_multiplier": atr_stop_multiplier,
                        "reward_risk_ratio": reward_risk_ratio,
                        "min_volume_ratio": min_entry_volume_ratio,
                        "confirmation_bars": confirmation_bars,
                        "cooldown_bars": cooldown_bars,
                        "allow_long": allow_long,
                        "allow_short": allow_short,
                        "breakeven_trigger_r": breakeven_trigger_r,
                        "breakeven_buffer_points": breakeven_buffer_points,
                        "max_holding_bars": max_holding_bars,
                        "score_exit_requires_profit": score_exit_requires_profit,
                        "min_score_exit_profit_points": min_score_exit_profit_points,
                        "signal_timeframe": SIGNAL_TIMEFRAME,
                        "include_institutional": False,
                    },
                    folds=3,
                    min_trades=min_scan_trades,
                )
                if walk_forward.empty:
                    st.warning("K 線資料不足，暫時無法做 Walk-forward 驗證。請增加歷史資料天數。")
                else:
                    st.dataframe(walk_forward, use_container_width=True)
                    ok_rows = walk_forward[walk_forward.get("狀態", "") == "ok"] if "狀態" in walk_forward.columns else walk_forward
                    trusted = int(ok_rows["樣本外可信達標"].sum()) if "樣本外可信達標" in ok_rows.columns else 0
                    total = len(ok_rows)
                    if total and trusted == total:
                        st.success("所有 Walk-forward 樣本外切片都可信達標，這組參數才比較接近可用。")
                    else:
                        st.warning(f"Walk-forward 可信達標 {trusted}/{total} 段；目前仍不建議只為了高勝率直接套用。")

elif page == "帳務參考":
    st.subheader("永豐帳務參考")
    st.caption("這裡只查詢帳務與未實現損益，不送出任何委託。")

    if api is None:
        st.info("尚未登入永豐 API，請先設定 `.env`、環境變數或 Streamlit Secrets。")
    elif st.button("更新永豐部位", use_container_width=True):
        df_pos = sinopac_api.get_fut_positions(api)
        if df_pos.empty:
            st.info("目前沒有期貨/選擇權未平倉部位。")
        else:
            st.dataframe(df_pos, use_container_width=True)
            st.metric("合計未實現損益", f"{df_pos['未實現損益'].sum():,.0f}")

elif page == "進階診斷":
    st.subheader("評分明細")
    st.caption(f"技術資料狀態：{tech_data.get('資料狀態', '未知')}")
    if reasons:
        st.table({"因素": reasons})
    else:
        st.write("目前沒有明確加減分因素。")

    c1, c2 = st.columns(2)
    mtx_net = public_data["mtx_net"]
    c1.metric(
        "小台三大法人淨部位",
        format_optional_number(mtx_net.get("net_oi"), " 口"),
        f"多空比 {mtx_net.get('long_short_ratio'):.2f}%" if mtx_net.get("long_short_ratio") else "無資料",
    )
    c2.metric("成交量", f"{realtime.get('volume', 0):,.0f}")
    pc_ratio = public_data["pc_ratio"]
    p1, p2 = st.columns(2)
    p1.metric(
        "選擇權 P/C Ratio",
        f"{pc_ratio.get('oi_ratio'):.2f}%" if pc_ratio.get("oi_ratio") else "無資料",
    )
    p2.metric(
        "選擇權成交 P/C",
        f"{pc_ratio.get('volume_ratio'):.2f}%" if pc_ratio.get("volume_ratio") else "無資料",
    )

    with st.expander("資料提醒與永豐連線診斷", expanded=False):
        for warning in data_warnings:
            if warning:
                st.warning(warning)
        if not has_credentials_safe(sj_api_key, sj_secret_key):
            st.info("永豐功能可使用環境變數或 Streamlit secrets：SJ_API_KEY、SJ_SECRET_KEY、SJ_SIMULATION。")
        st.json(sinopac_api.get_connection_status(api, simulation_mode))
        st.write(
            {
                "snapshot_source": realtime.get("source"),
                "snapshot_updated_at": realtime.get("updated_at"),
                "snapshot_error": realtime.get("error"),
                "kbars_rows": len(kbars),
                "kbars_contract": kbars.attrs.get("contract_code", "") if hasattr(kbars, "attrs") else "",
                "kbars_error": kbars_error,
                "tech_data_keys": list(tech_data.keys()),
                "pc_ratio_source": public_data["pc_ratio"].get("source"),
                "option_source": public_data["option_levels"].get("source"),
                "mtx_source": public_data["mtx_net"].get("source"),
            }
        )
