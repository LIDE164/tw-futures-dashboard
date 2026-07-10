import streamlit as st

from indicators import build_tech_data
from market_data import get_public_market_data
from realtime_api import get_realtime_data
from scoring import get_decision_score
from scraper import get_taifex_institutional_oi
from sinopac_api import (
    activate_ca_from_env,
    get_api,
    get_connection_status,
    get_fut_positions,
    get_recent_txf_kbars,
    get_simulation_default,
    has_credentials,
    place_futures_order,
)
from strategy import StrategyManager


st.set_page_config(page_title="期權戰情室", page_icon="📊", layout="wide")


@st.cache_data(ttl=180, show_spinner=False)
def load_oi_data(api_token):
    return get_taifex_institutional_oi(api_token=api_token)


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


@st.cache_data(ttl=300, show_spinner=False)
def load_public_market_data():
    return get_public_market_data()


with st.sidebar:
    st.subheader("金鑰與風控設定")
    finmind_token = st.text_input("FinMind API Token", type="password", help="輸入 Token 可提高 FinMind API 穩定度")

    st.divider()
    st.subheader("永豐 Shioaji")
    simulation_mode = st.checkbox("使用模擬模式", value=get_simulation_default())
    sj_api_key = st.text_input("SJ API Key", type="password", help="可改用 Streamlit secrets 或環境變數 SJ_API_KEY")
    sj_secret_key = st.text_input("SJ Secret Key", type="password", help="可改用 Streamlit secrets 或環境變數 SJ_SECRET_KEY")
    if simulation_mode:
        st.info("若使用正式永豐 API Key 仍收不到行情或帳務資料，請先取消「使用模擬模式」再重新整理。")

    st.divider()
    st.subheader("策略風控")
    long_entry_score = st.slider("多單進場分數", 50, 80, 60)
    short_entry_score = st.slider("空單進場分數", 20, 50, 40)
    stop_loss_points = st.number_input("停損點數", min_value=10, max_value=300, value=50, step=10)

    st.divider()
    st.subheader("委託保護")
    enable_live_trade = st.checkbox("啟用永豐送單", value=False)
    confirm_order = st.checkbox("我確認允許本次送單", value=False)
    order_quantity = st.number_input("委託口數", min_value=1, max_value=10, value=1, step=1)
    order_is_market = st.checkbox("使用市價 IOC", value=True)
    limit_price = st.number_input("限價價格", min_value=0.0, value=0.0, step=1.0, disabled=order_is_market)

    if st.button("重新整理資料", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


api, api_error = get_api(
    simulation=simulation_mode,
    api_key=sj_api_key,
    secret_key=sj_secret_key,
)

with st.spinner("更新市場資料中..."):
    oi_data = load_oi_data(finmind_token)
    public_data = load_public_market_data()
    realtime = get_realtime_data(api)
    kbars, kbars_error = get_recent_txf_kbars(api)


for warning in (
    api_error,
    oi_data.get("error"),
    realtime.get("error"),
    kbars_error,
    *public_data.get("errors", []),
):
    if warning:
        st.warning(warning)


current_price = realtime["current_price"]
tech_data = build_tech_data(kbars, realtime)

score, label, reasons, feature = get_decision_score(tech_data, inst_data=oi_data, with_reason=True)

if "trader" not in st.session_state:
    st.session_state.trader = StrategyManager()

st.session_state.trader.update_config(
    long_entry_score=long_entry_score,
    short_entry_score=short_entry_score,
    stop_loss_points=stop_loss_points,
)
action, msg = st.session_state.trader.get_trade_action(score, current_price)


st.title(f"期權戰情室 - 台指期 {current_price:,.0f}")
st.caption(f"資料來源：{realtime.get('source', 'fallback')}｜更新時間：{realtime.get('updated_at') or '備援資料'}")
st.divider()

top1, top2, top3, top4 = st.columns(4)
top1.metric("綜合評分", score, label)
top2.metric("型態特徵", feature)
top3.metric("盤中均價線", f"{realtime['vwap']:,.0f}" if realtime["vwap"] else "無資料")
top4.metric("30日年化波動", f"{tech_data['30日年化波動率']:.1f}%" if tech_data["30日年化波動率"] else "無資料")

if action == "HOLD":
    st.info(f"AI 策略指令：{action}\n\n{msg}")
elif "BUY" in action or "CLOSE_SHORT" in action:
    st.success(f"AI 策略指令：{action}\n\n{msg}")
else:
    st.error(f"AI 策略指令：{action}\n\n{msg}")

tab_diag, tab_chips, tab_options, tab_account = st.tabs(["綜合診斷", "法人籌碼", "莊家區間", "真實部位"])

with tab_diag:
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

with tab_chips:
    st.subheader("三大法人期貨未平倉")
    st.caption(f"資料日期：{oi_data.get('date') or '待更新'}")

    col_f, col_t, col_d = st.columns(3)
    col_f.metric("外資及陸資", format_contracts(oi_data["外資"]), metric_delta(oi_data["外資"]))
    col_t.metric("投信", format_contracts(oi_data["投信"]), metric_delta(oi_data["投信"]))
    col_d.metric("自營商", format_contracts(oi_data["自營商"]), metric_delta(oi_data["自營商"]))

with tab_options:
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

with tab_account:
    st.subheader("永豐真實期貨部位 / 未實現損益")
    st.caption("部位與損益以永豐帳務 API 為準；StrategyManager 只作策略建議。")

    if api is None:
        st.info("尚未登入永豐 API，請先在側邊欄輸入 API Key / Secret 或設定環境變數。")
    elif st.button("更新部位", use_container_width=True):
        df_pos = get_fut_positions(api)
        if df_pos.empty:
            st.info("目前沒有期貨/選擇權未平倉部位。")
        else:
            st.dataframe(df_pos, use_container_width=True)
            st.metric("合計未實現損益", f"{df_pos['未實現損益'].sum():,.0f}")

    st.divider()
    st.subheader("手動送單")
    st.caption("預設只顯示策略建議。送單需要同時勾選啟用與本次確認。")

    can_order = action in {"BUY_LONG", "SELL_SHORT", "CLOSE_LONG", "CLOSE_SHORT"}
    if not can_order:
        st.warning("目前策略指令不是可送單動作，未開放送出委託。")
    elif not enable_live_trade or not confirm_order:
        st.warning("目前只顯示策略建議，尚未送出真實或模擬委託。")
    elif api is None:
        st.error("尚未登入永豐 API，無法送單。")
    elif st.button(f"送出永豐委託：{action}", type="primary", use_container_width=True):
        if not simulation_mode:
            ca_ok, ca_error = activate_ca_from_env(api)
            if not ca_ok:
                st.error(ca_error)
                st.stop()

        try:
            trade = place_futures_order(
                api,
                action,
                quantity=order_quantity,
                price=0 if order_is_market else limit_price,
                market=order_is_market,
            )
            st.success("委託已送出，請以永豐回報與帳務為準。")
            st.write(trade)
        except Exception as exc:
            st.error(f"委託送出失敗：{exc}")

if not has_credentials(sj_api_key, sj_secret_key):
    st.info("永豐功能可使用環境變數或 Streamlit secrets：SJ_API_KEY、SJ_SECRET_KEY、SJ_SIMULATION。")
