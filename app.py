import streamlit as st

from indicators import build_tech_data
from realtime_api import get_realtime_data
from scoring import get_decision_score
from scraper import get_taifex_institutional_oi
from sinopac_api import (
    activate_ca_from_env,
    get_api,
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


with st.sidebar:
    st.subheader("金鑰與風控設定")
    finmind_token = st.text_input("FinMind API Token", type="password", help="輸入 Token 可提高 FinMind API 穩定度")

    st.divider()
    st.subheader("永豐 Shioaji")
    simulation_mode = st.checkbox("使用模擬模式", value=get_simulation_default())
    sj_api_key = st.text_input("SJ API Key", type="password", help="可改用 Streamlit secrets 或環境變數 SJ_API_KEY")
    sj_secret_key = st.text_input("SJ Secret Key", type="password", help="可改用 Streamlit secrets 或環境變數 SJ_SECRET_KEY")

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
    realtime = get_realtime_data(api)
    kbars = get_recent_txf_kbars(api)


for warning in (api_error, oi_data.get("error"), realtime.get("error")):
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
top4.metric("台指 VIX", realtime["vix"], "結構穩定", delta_color="off")

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
    if reasons:
        st.table({"因素": reasons})
    else:
        st.write("目前沒有明確加減分因素。")

    c1, c2, c3 = st.columns(3)
    c1.metric("散戶小台多空比", "-15.2%", "偏多軋空")
    c2.metric("成交量", f"{realtime['volume']:,.0f}")
    c3.metric("選擇權 P/C Ratio", "115%", "支撐強")

with tab_chips:
    st.subheader("三大法人期貨未平倉")
    st.caption(f"資料日期：{oi_data.get('date') or '待更新'}")

    col_f, col_t, col_d = st.columns(3)
    col_f.metric("外資及陸資", format_contracts(oi_data["外資"]), metric_delta(oi_data["外資"]))
    col_t.metric("投信", format_contracts(oi_data["投信"]), metric_delta(oi_data["投信"]))
    col_d.metric("自營商", format_contracts(oi_data["自營商"]), metric_delta(oi_data["自營商"]))

with tab_options:
    st.subheader("選擇權最大未平倉量")
    col_call, col_put = st.columns(2)
    col_call.metric("Call 壓力", "23,500", "-壓力", delta_color="inverse")
    col_put.metric("Put 支撐", "22,800", "+支撐")

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
