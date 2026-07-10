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


st.set_page_config(page_title="期權戰情室", page_icon="📊", layout="wide")


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


def empty_institutional_data(error=None):
    return {"外資": 0, "投信": 0, "自營商": 0, "date": None, "error": error}


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
    oi_data = public_data.get(
        "txf_institutional",
        empty_institutional_data("TAIFEX 法人籌碼資料尚未載入，請重新部署或清除快取後再試。"),
    )
    realtime = get_realtime_data(api)
    kbars, kbars_error = get_recent_txf_kbars(api)


for warning in (
    api_error,
    realtime.get("error"),
    kbars_error,
    oi_data.get("error"),
    *public_data.get("errors", []),
):
    if warning:
        st.warning(warning)


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


st.title(f"期權戰情室 - 台指期 {current_price:,.0f}")
st.caption(f"資料來源：{realtime.get('source', 'fallback')}｜更新時間：{realtime.get('updated_at') or '備援資料'}")
st.divider()

top1, top2, top3, top4 = st.columns(4)
top1.metric("綜合評分", score, label)
top2.metric("型態特徵", feature)
top3.metric("盤中均價線", f"{realtime['vwap']:,.0f}" if realtime["vwap"] else "無資料")
top4.metric("30日年化波動", f"{volatility_30d:.1f}%" if volatility_30d else "無資料")

if action == "HOLD":
    st.info(f"策略訊號：{action}\n\n{msg}")
elif "BUY" in action or "CLOSE_SHORT" in action:
    st.success(f"策略訊號：{action}\n\n{msg}")
else:
    st.error(f"策略訊號：{action}\n\n{msg}")

if reference_stop:
    st.caption(f"參考停損價：{reference_stop:,.0f}｜目前模式：{system_mode}")

tabs = st.tabs(["綜合診斷", "法人籌碼", "莊家區間", "模擬帳本", "回測系統", "帳務參考"])

with tabs[0]:
    st.subheader("評分明細")
    st.caption(f"技術資料狀態：{tech_data.get('資料狀態', '未知')}")
    with st.expander("永豐連線診斷", expanded=False):
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

    if reasons:
        st.table({"因素": reasons})
    else:
        st.write("目前沒有明確加減分因素。")

    c1, c2, c3 = st.columns(3)
    mtx_net = public_data["mtx_net"]
    c1.metric(
        "小台三大法人淨部位",
        format_optional_number(mtx_net["net_oi"], " 口"),
        f"多空比 {mtx_net['long_short_ratio']:.2f}%" if mtx_net["long_short_ratio"] else "無資料",
    )
    c2.metric("成交量", f"{realtime['volume']:,.0f}")
    pc_ratio = public_data["pc_ratio"]
    c3.metric(
        "選擇權 P/C Ratio",
        f"{pc_ratio['oi_ratio']:.2f}%" if pc_ratio["oi_ratio"] else "無資料",
        f"成交 {pc_ratio['volume_ratio']:.2f}%" if pc_ratio["volume_ratio"] else None,
    )

with tabs[1]:
    st.subheader("三大法人期貨未平倉")
    st.caption(f"資料日期：{oi_data.get('date') or '待更新'}｜資料來源：TAIFEX")
    col_f, col_t, col_d = st.columns(3)
    col_f.metric("外資及陸資", format_contracts(oi_data["外資"]), metric_delta(oi_data["外資"]))
    col_t.metric("投信", format_contracts(oi_data["投信"]), metric_delta(oi_data["投信"]))
    col_d.metric("自營商", format_contracts(oi_data["自營商"]), metric_delta(oi_data["自營商"]))

with tabs[2]:
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
        format_optional_number(option_levels["call_pressure"]),
        f"OI {option_levels['call_oi']:,}" if option_levels["call_oi"] else "無資料",
        delta_color="inverse",
    )
    col_put.metric(
        "Put 支撐",
        format_optional_number(option_levels["put_support"]),
        f"OI {option_levels['put_oi']:,}" if option_levels["put_oi"] else "無資料",
    )

with tabs[3]:
    st.subheader("模擬帳本")
    st.caption("模擬下單只寫入本機 Streamlit session，不會送出任何真實委託。")
    if system_mode != "模擬盤模式":
        st.info("目前不是模擬盤模式；切換到模擬盤模式後才會執行模擬成交。")

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("模擬部位", paper_broker.position)
    p2.metric("進場價", f"{paper_broker.entry_price:,.0f}" if paper_broker.entry_price else "無")
    p3.metric("已實現損益", f"{paper_broker.realized_pnl:,.0f}")
    p4.metric("未實現損益", f"{paper_broker.unrealized_pnl(current_price):,.0f}")

    can_paper_execute = action in {"BUY_LONG", "SELL_SHORT", "CLOSE_LONG", "CLOSE_SHORT"}
    col_exec, col_reset = st.columns(2)
    with col_exec:
        if st.button(
            f"模擬執行訊號：{action}",
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

with tabs[4]:
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
            cols = st.columns(5)
            cols[0].metric("總損益", f"{summary['總損益']:,.0f}")
            cols[1].metric("交易次數", summary["交易次數"])
            cols[2].metric("勝率", f"{summary['勝率']:.2f}%")
            cols[3].metric("盈虧比", summary["盈虧比"])
            cols[4].metric("最大回撤", f"{summary['最大回撤']:,.0f}")

            st.write(summary)
            if not equity_curve.empty:
                st.line_chart(equity_curve.set_index("bar")["equity"])
                st.dataframe(equity_curve.tail(100), use_container_width=True)
            if not trades.empty:
                st.subheader("交易明細")
                st.dataframe(trades, use_container_width=True)

with tabs[5]:
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

if not has_credentials(sj_api_key, sj_secret_key):
    st.info("永豐功能可使用環境變數或 Streamlit secrets：SJ_API_KEY、SJ_SECRET_KEY、SJ_SIMULATION。")
