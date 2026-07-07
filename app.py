import streamlit as st
from scraper import get_taifex_institutional_oi
from realtime_api import get_realtime_data
from scoring import get_decision_score

st.set_page_config(page_title="期權戰情室", page_icon="📊", layout="centered")

# 1. 抓取真實數據 (網頁重新整理時執行)
oi_data = get_taifex_institutional_oi()
realtime = get_realtime_data()

# 2. 模擬我們從看盤軟體算出的技術指標字典 (實戰中這會是你用 TA-Lib 算出來的結果)
tech_data = {
    '收盤價': realtime['current_price'],
    'BB_DN': realtime['current_price'] * 0.99,
    'MACD柱': 1.5,
    '前日MACD柱': 0.5,
    '成交量': realtime['volume'],
    '5日均量': realtime['volume'] * 0.8,
    '訊號': True,
    'ADX': 28.5,
    '回測有撐': True
}
fund_data = {} # 留給股票基本面的擴充，期貨暫時傳空字典

# 3. 呼叫統一的 scoring function 計算分數與加減分明細
score, label, rs, feature = get_decision_score(tech_data, fund_data, with_reason=True)

# 將結果存入 doc，確保分數永遠一致，且測試時完全不要再算分數
doc = {
    "score": score,
    "label": label,
    "feature": feature,
    "reasons": rs,
}

# ================= 開始渲染網頁 =================
st.markdown(f"<h3 style='text-align: center;'>📊 期權戰情室 (當前報價 {realtime['current_price']:.0f})</h3>", unsafe_allow_html=True)
st.divider()

tab_diag, tab_chip, tab_opt = st.tabs(["綜合診斷", "法人籌碼", "莊家區間"])

with tab_diag:
    st.success("⚡ 即時技術評分系統")
    col1, col2 = st.columns([1, 2])
    with col1:
        st.metric(label="綜合技術評分", value=doc['score'])
    with col2:
        st.markdown(f"**{doc['label']}**")
        st.caption(doc['feature'])
    
    # 完美展示加扣分明細
    with st.expander("查看加扣分解析明細"):
        for reason in doc['reasons']:
            if "+" in reason:
                st.markdown(f"<span style='color: #ff4d4f;'>✅ {reason}</span>", unsafe_allow_html=True)
            else:
                st.markdown(f"<span style='color: #00e676;'>⚠️ {reason}</span>", unsafe_allow_html=True)

with tab_chip:
    st.subheader("🏦 三大法人期貨未平倉 (OI)")
    c1, c2, c3 = st.columns(3)
    c1.metric(label="外資", value=f"{oi_data['外資']:,}")
    c2.metric(label="投信", value=f"{oi_data['投信']:,}")
    c3.metric(label="自營商", value=f"{oi_data['自營商']:,}")
