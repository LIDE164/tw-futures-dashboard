import streamlit as st

from backtester import run_backtest
from indicators import build_tech_data
from market_data import get_public_market_data
from paper_broker import PaperBroker
from realtime_api import get_realtime_data
from scoring import get_decision_score
from sinopac_api import (
    get_api,
    get_connection_status,
    get_fut_positions,
    get_recent_txf_kbars,
    get_simulation_default,
    has_credentials,
)
from strategy import StrategyManager


st.set_page_config(
    page_title="期權戰情室",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed",
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


def build_trade_plan(action, current_price, strategy, paper_broker, system_mode):
    stop_gap = float(strategy.stop_loss_points)
    reward_gap = stop_gap * 2
    has_paper_position = system_mode == "模擬盤模式" and paper_broker.position != 0
    entry_price = paper_broker.entry_price if has_paper_position else current_price

    plan = {
        "title": action_to_text(action, paper_broker.position if has_paper_position else 0),
        "summary": "目前沒有明確進場條件，先觀望。",
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "close_rule": "等待分數突破進場門檻後再規劃。",
    }

    if action == "BUY_LONG":
        plan.update(
            {
                "summary": "目前偏多，若要進場，請用做多計畫控管風險。",
                "entry_price": current_price,
                "stop_loss": current_price - stop_gap,
                "take_profit": current_price + reward_gap,
                "close_rule": f"跌破 {current_price - stop_gap:,.0f} 或評分跌破 {strategy.long_exit_score} 分就先平倉。",
            }
        )
    elif action == "SELL_SHORT":
        plan.update(
            {
                "summary": "目前偏空，若要進場，請用做空計畫控管風險。",
                "entry_price": current_price,
                "stop_loss": current_price + stop_gap,
                "take_profit": current_price - reward_gap,
                "close_rule": f"漲破 {current_price + stop_gap:,.0f} 或評分升破 {strategy.short_exit_score} 分就先回補。",
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
                "summary": "模擬帳本目前持有多單，先照原計畫觀察。",
                "entry_price": paper_broker.entry_price,
                "stop_loss": paper_broker.entry_price - stop_gap,
                "take_profit": paper_broker.entry_price + reward_gap,
                "close_rule": f"跌破 {paper_broker.entry_price - stop_gap:,.0f} 或評分跌破 {strategy.long_exit_score} 分就先平倉。",
            }
        )
    elif has_paper_position and paper_broker.position < 0:
        plan.update(
            {
                "summary": "模擬帳本目前持有空單，先照原計畫觀察。",
                "entry_price": paper_broker.entry_price,
                "stop_loss": paper_broker.entry_price + stop_gap,
                "take_profit": paper_broker.entry_price - reward_gap,
                "close_rule": f"漲破 {paper_broker.entry_price + stop_gap:,.0f} 或評分升破 {strategy.short_exit_score} 分就先回補。",
            }
        )

    return plan


@st.cache_data(ttl=300, show_spinner=False)
def load_public_market_data():
    return get_public_market_data()


def configure_strategy(strategy, long_entry_score, short_entry_score, stop_loss_points):
    strategy.update_config(
        long_entry_score=long_entry_score,
        short_entry_score=short_entry_score,
        stop_loss_points=stop_loss_points,
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
    simulation_mode = st.checkbox("使用模擬模式", value=get_simulation_default())
    sj_api_key = st.text_input("SJ API Key", type="password", help="可改用 Streamlit secrets 或環境變數 SJ_API_KEY")
    sj_secret_key = st.text_input("SJ Secret Key", type="password", help="可改用 Streamlit secrets 或環境變數 SJ_SECRET_KEY")
    if simulation_mode:
        st.info("若使用正式永豐 API Key 仍收不到行情或帳務資料，請先取消「使用模擬模式」再重新整理。")

    st.divider()
    st.subheader("策略參數")
    long_entry_score = st.slider("多單進場分數", 50, 80, 60)
    short_entry_score = st.slider("空單進場分數", 20, 50, 40)
    stop_loss_points = st.number_input("停損點數", min_value=10, max_value=300, value=50, step=10)

    st.divider()
    st.subheader("模擬 / 回測成本")
    contract_multiplier = st.selectbox("契約乘數", [200, 50, 10], index=0, help="大台 200，小台 50，微台 10")
    paper_quantity = st.number_input("口數", min_value=1, max_value=20, value=1, step=1)
    slippage_points = st.number_input("滑價點數", min_value=0.0, max_value=20.0, value=1.0, step=0.5)
    commission_per_side = st.number_input("單邊手續費", min_value=0.0, max_value=500.0, value=0.0, step=1.0)

    if st.button("重新整理資料", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


api, api_error = get_api(
    simulation=simulation_mode,
    api_key=sj_api_key,
    secret_key=sj_secret_key,
)

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
    realtime = get_realtime_data(api)
    kbars, kbars_error = get_recent_txf_kbars(api)


data_warnings = [
    api_error,
    realtime.get("error"),
    kbars_error,
    oi_data.get("error"),
    *public_data.get("errors", []),
]


current_price = realtime["current_price"]
tech_data = build_tech_data(kbars, realtime)
volatility_30d = float(tech_data.get("30日年化波動率") or 0)
score, label, reasons, feature = get_decision_score(tech_data, inst_data=oi_data, with_reason=True)

if "strategy" not in st.session_state:
    st.session_state.strategy = StrategyManager()

configure_strategy(st.session_state.strategy, long_entry_score, short_entry_score, stop_loss_points)

if "paper_broker" not in st.session_state:
    st.session_state.paper_broker = PaperBroker(
        multiplier=contract_multiplier,
        commission_per_side=commission_per_side,
        slippage_points=slippage_points,
    )

paper_broker = st.session_state.paper_broker
paper_broker.multiplier = contract_multiplier
paper_broker.commission_per_side = commission_per_side
paper_broker.slippage_points = slippage_points

if system_mode == "模擬盤模式":
    st.session_state.strategy.sync_position(paper_broker.position, paper_broker.entry_price)
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
trade_plan = build_trade_plan(action, current_price, st.session_state.strategy, paper_broker, system_mode)
tone = signal_state(action, paper_broker.position if system_mode == "模擬盤模式" else 0)

st.title("期權戰情室")
st.caption(f"資料來源：{realtime.get('source', 'fallback')}｜更新時間：{realtime.get('updated_at') or '備援資料'}｜模式：{system_mode}")

with st.container(border=True):
    st.subheader("現在建議")
    price_col, action_col = st.columns(2)
    price_col.metric("目前價格", format_price(current_price))
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

with st.expander("進階摘要", expanded=False):
    sum1, sum2 = st.columns(2)
    sum1.metric("綜合評分", score, label)
    sum2.metric("型態特徵", feature)
    sum3, sum4 = st.columns(2)
    sum3.metric("盤中均價線", format_price(realtime.get("vwap")))
    sum4.metric("30日年化波動", f"{volatility_30d:.1f}%" if volatility_30d else "無資料")

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

    can_paper_execute = action in {"BUY_LONG", "SELL_SHORT", "CLOSE_LONG", "CLOSE_SHORT"}
    col_exec, col_reset = st.columns(2)
    with col_exec:
        if st.button(
            f"模擬執行訊號：{trade_plan['title']}",
            disabled=not can_paper_execute or system_mode != "模擬盤模式",
            use_container_width=True,
        ):
            filled, fill_msg = paper_broker.execute(action, current_price, quantity=paper_quantity, note=msg)
            if filled:
                st.session_state.strategy.apply_fill(action, current_price, paper_quantity)
                st.success(fill_msg)
                st.rerun()
            else:
                st.warning(fill_msg)
    with col_reset:
        if st.button("重置模擬帳本", use_container_width=True):
            paper_broker.reset()
            st.session_state.strategy.reset()
            st.rerun()

    trades_df = paper_broker.trades_df()
    if trades_df.empty:
        st.info("尚無模擬交易紀錄。")
    else:
        st.dataframe(trades_df, use_container_width=True)

elif page == "回測系統":
    st.subheader("回測系統")
    st.caption("訊號以第 N 根 K 棒收盤資料計算，成交用第 N+1 根開盤價，避免偷看未來。")

    if kbars.empty:
        st.warning("目前沒有足夠 K 線資料可回測。")
    else:
        trades, equity_curve, summary = run_backtest(
            kbars,
            inst_data=oi_data,
            quantity=paper_quantity,
            multiplier=contract_multiplier,
            commission_per_side=commission_per_side,
            slippage_points=slippage_points,
            long_entry_score=long_entry_score,
            short_entry_score=short_entry_score,
            stop_loss_points=stop_loss_points,
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
        st.info("尚未登入永豐 API，請先在側邊欄輸入 API Key / Secret 或設定環境變數。")
    elif st.button("更新永豐部位", use_container_width=True):
        df_pos = get_fut_positions(api)
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
        if not has_credentials(sj_api_key, sj_secret_key):
            st.info("永豐功能可使用環境變數或 Streamlit secrets：SJ_API_KEY、SJ_SECRET_KEY、SJ_SIMULATION。")
        st.json(get_connection_status(api, simulation_mode))
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
