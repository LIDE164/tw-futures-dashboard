import streamlit as st

# 1. 網頁基本設定 (適合手機版)
st.set_page_config(page_title="期權戰情室", page_icon="📊", layout="centered")

st.markdown("<h3 style='text-align: center;'>📊 期權戰情室 (台指期 23,150)</h3>", unsafe_allow_html=True)
st.divider()

# 2. 建立三個分頁籤
tab_diag, tab_chip, tab_opt = st.tabs(["綜合診斷", "法人籌碼", "莊家區間"])

# ================= 頁面一：綜合診斷 =================
with tab_diag:
    st.success("⚡ 即時技術評分系統 (13:25 更新)")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        st.metric(label="綜合技術評分", value="68")
    with col2:
        st.markdown("**🟢 強勢買進**")
        st.caption("均價線之上 | 量增 | 布林下軌有撐")
    
    st.divider()
    st.subheader("🔥 盤中核心風向球")
    st.metric(label="散戶小台多空比", value="-15.2%", delta="偏多軋空", delta_color="normal")
    st.metric(label="盤中均價線 (VWAP)", value="23,110", delta="站上均線", delta_color="normal")
    st.metric(label="選擇權 P/C Ratio", value="115%", delta="支撐強", delta_color="normal")

# ================= 頁面二：法人籌碼 =================
with tab_chip:
    st.subheader("🏦 三大法人期貨未平倉 (OI)")
    c1, c2, c3 = st.columns(3)
    c1.metric(label="外資", value="+12,500", delta="+2000")
    c2.metric(label="投信", value="+1,200", delta="+150")
    c3.metric(label="自營", value="-3,500", delta="-500", delta_color="inverse")
    
    st.divider()
    st.subheader("💵 大盤現貨買賣超")
    st.metric(label="外資及陸資", value="+152.4 億")
    st.metric(label="投信", value="+45.8 億")

# ================= 頁面三：莊家區間 =================
with tab_opt:
    st.subheader("🔮 選擇權最大未平倉量 (OI)")
    st.metric(label="Call 壓力 (天花板)", value="23,500", delta="壓", delta_color="inverse")
    st.metric(label="Put 支撐 (地板)", value="22,800", delta="撐", delta_color="normal")
    
    st.divider()
    st.metric(label="台指 VIX 恐慌指數", value="18.5", delta="結構穩定", delta_color="off")
