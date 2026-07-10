from datetime import datetime

import pandas as pd
import streamlit as st

import sinopac_api
from backtester import run_backtest
from indicators import build_tech_data
from market_session import TAIPEI, format_datetime, get_market_status
from market_data import get_public_market_data
from paper_broker import PaperBroker
from scoring import get_decision_score
from storage import clear_paper_broker_state, restore_paper_broker_state, save_paper_broker_state
from strategy import StrategyManager


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


def get_realtime_data_safe(api, product_root=PRODUCT_ROOT):
    default_data = {
        "current_price": 0.0,
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


def estimated_fill_price(action, price, slippage_points):
    if action == "BUY_LONG":
        return price + slippage_points
    if action == "SELL_SHORT":
        return price - slippage_points
    return price


def build_trade_plan(action, current_price, strategy, paper_broker, system_mode, market_status, slippage_points=0.0):
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
        fill_price = estimated_fill_price(action, current_price, slippage_points)
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
        fill_price = estimated_fill_price(action, current_price, slippage_points)
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


def configure_strategy(strategy, long_entry_score, short_entry_score, stop_loss_points, take_profit_points):
    strategy.update_config(
        long_entry_score=long_entry_score,
        short_entry_score=short_entry_score,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
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
    long_entry_score = st.slider("多單進場分數", 50, 80, 60)
    short_entry_score = st.slider("空單進場分數", 20, 50, 40)
    stop_loss_points = st.number_input("停損點數", min_value=10, max_value=300, value=50, step=10)
    take_profit_points = st.number_input("停利點數", min_value=10, max_value=600, value=100, step=10)

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
score, label, reasons, feature = get_decision_score(tech_data, inst_data=oi_data, with_reason=True)

if "strategy" not in st.session_state:
    st.session_state.strategy = StrategyManager()

configure_strategy(
    st.session_state.strategy,
    long_entry_score,
    short_entry_score,
    stop_loss_points,
    take_profit_points,
)

if "paper_broker" not in st.session_state:
    st.session_state.paper_broker = PaperBroker(
        multiplier=contract_multiplier,
        commission_per_side=commission_per_side,
        slippage_points=slippage_points,
    )
    restore_paper_broker_state(st.session_state.paper_broker)

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
    reference_stop = current_price - stop_loss_points
elif action == "SELL_SHORT":
    reference_stop = current_price + stop_loss_points
elif paper_broker.position > 0 and system_mode == "模擬盤模式":
    reference_stop = paper_broker.entry_price - stop_loss_points
elif paper_broker.position < 0 and system_mode == "模擬盤模式":
    reference_stop = paper_broker.entry_price + stop_loss_points


plain_reasons = summarize_reasons(reasons, msg, limit=3)
trade_plan = build_trade_plan(
    action,
    current_price,
    st.session_state.strategy,
    active_broker,
    system_mode,
    market_status,
    slippage_points,
)
tone = "info" if not market_status.is_open and active_broker.position == 0 else signal_state(
    action,
    active_broker.position if system_mode in {"模擬盤模式", "實盤觀察模式"} else 0,
)
age_seconds = data_age_seconds(realtime.get("updated_at"))
max_loss_per_contract = stop_loss_points * CONTRACT_MULTIPLIER
estimated_cost = commission_per_side * 2 + slippage_points * 2 * CONTRACT_MULTIPLIER

st.title("期權戰情室")
contract_text = realtime.get("contract_code") or (kbars.attrs.get("contract_code", "") if hasattr(kbars, "attrs") else "")
delivery_text = realtime.get("delivery_date") or (kbars.attrs.get("delivery_date", "") if hasattr(kbars, "attrs") else "")
st.caption(
    f"商品：{PRODUCT_NAME}｜契約：{contract_text or '無資料'}｜契約到期日：{delivery_text or '無資料'}"
)
st.caption(
    f"市場狀態：{market_status.label}｜"
    f"下一次開盤：{format_datetime(market_status.next_open)}｜"
    f"最後有效訊號：{latest_completed_bar_text(kbars)}"
)
st.caption(
    f"訊號週期：15 分鐘｜API 更新：{data_freshness_label(age_seconds)}｜"
    f"最後成交：{realtime.get('exchange_timestamp') or 'snapshot 未提供交易所時間'}"
)
st.caption(f"資料來源：{realtime.get('source', 'fallback')}｜模式：{system_mode}")

if age_seconds is None or age_seconds > 30:
    st.error("API 查詢時間可能已過期，請先重新整理，不要直接依照舊畫面操作。")

if not market_status.is_open:
    st.warning("目前不是交易時段；首頁只顯示下次開盤預備計畫，不提供可直接成交的進場價。")

with st.container(border=True):
    st.subheader("現在建議")
    price_col, action_col = st.columns(2)
    price_col.metric("目前價格" if market_status.is_open else "最近價格", format_price(current_price))
    action_col.metric("操作方向", trade_plan["title"])

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

with st.container(border=True):
    st.subheader("交易計畫")
    entry_col, stop_col = st.columns(2)
    target_col, close_col = st.columns(2)
    entry_col.metric("建議進場價", format_price(trade_plan["entry_price"]))
    stop_col.metric("停損價", format_price(trade_plan["stop_loss"]))
    target_col.metric("目標平倉價", format_price(trade_plan["take_profit"]))
    close_col.write("平倉條件")
    close_col.write(trade_plan["close_rule"])
    risk_col, cost_col = st.columns(2)
    risk_col.metric("每口最大預估虧損", format_money(max_loss_per_contract))
    cost_col.metric("預估來回成本", format_money(estimated_cost))

with st.expander("進階摘要", expanded=False):
    sum1, sum2 = st.columns(2)
    sum1.metric("綜合評分", score, label)
    sum2.metric("型態特徵", feature)
    sum3, sum4 = st.columns(2)
    sum3.metric("盤中均價線", format_price(realtime.get("vwap")))
    sum4.metric("近30根波動", f"{volatility_30d:.1f}%" if volatility_30d else "無資料")

page = st.selectbox(
    "查看更多資訊",
    ["新手首頁", "法人籌碼", "選擇權區間", "模擬部位", "回測系統", "帳務參考", "進階診斷"],
)

if page == "新手首頁":
    st.caption("上方已整理目前價格、操作方向、原因、進場參考、停損與目標價。")

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

    can_paper_execute = market_status.is_open and action in {"BUY_LONG", "SELL_SHORT", "CLOSE_LONG", "CLOSE_SHORT"}
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
                save_paper_broker_state(paper_broker)
                st.success(fill_msg)
                st.rerun()
            else:
                st.warning(fill_msg)
    with col_reset:
        if st.button("重置模擬帳本", use_container_width=True):
            paper_broker.reset()
            st.session_state.strategy.reset()
            clear_paper_broker_state()
            st.rerun()

    trades_df = paper_broker.trades_df()
    if trades_df.empty:
        st.info("尚無模擬交易紀錄。")
    else:
        st.dataframe(trades_df, use_container_width=True)

elif page == "回測系統":
    st.subheader("回測系統")
    st.caption("固定使用 15 分鐘 K。法人籌碼暫不納入歷史回測，避免用今日資料回填過去。")

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
            r3.metric("勝率", f"{summary['勝率']:.2f}%")
            r4.metric("最大回撤", f"{summary['最大回撤']:,.0f}")
            st.write(summary)
            if not equity_curve.empty:
                st.line_chart(equity_curve.set_index("bar")["equity"])
                st.dataframe(equity_curve.tail(100), use_container_width=True)
            if not trades.empty:
                st.subheader("交易明細")
                st.dataframe(trades, use_container_width=True)

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
