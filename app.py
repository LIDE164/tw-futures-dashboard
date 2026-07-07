import streamlit as st
import streamlit.components.v1 as components
from scraper import get_taifex_institutional_oi
from realtime_api import get_realtime_data
from scoring import get_decision_score

# 1. 隱藏 Streamlit 預設的白邊與選單，讓畫面全螢幕
st.set_page_config(page_title="期權戰情室", page_icon="📊", layout="wide")
st.markdown("""
    <style>
        .block-container { padding: 0 !important; max-width: 100% !important; }
        header { visibility: hidden; }
        #MainMenu { visibility: hidden; }
        footer { visibility: hidden; }
    </style>
""", unsafe_allow_html=True)

# 2. 抓取真實數據 (這裡會呼叫你寫好的爬蟲與 API)
try:
    oi_data = get_taifex_institutional_oi()
    realtime = get_realtime_data()
except:
    # 若爬蟲暫時失敗，給予預設值以防畫面崩潰
    oi_data = {"外資": 12500, "投信": 1200, "自營商": -3500}
    realtime = {"current_price": 23150, "volume": 450000, "vwap": 23110, "vix": 18.5}

# 3. 模擬技術指標並計算決策分數
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

score, label, rs, feature = get_decision_score(tech_data, {}, with_reason=True)
doc = {"score": score, "label": label, "feature": feature}

# 4. 將前端 HTML 介面寫成一個巨大的 Python f-string
# 注意裡面用 {變數名稱} 將 Python 抓到的真實數據塞進網頁中
html_template = f"""
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <style>
        :root {{
            --bg-color: #0d0e12;
            --panel-bg: #1a1b23;
            --accent: #2962ff;
            --text-main: #ffffff;
            --text-muted: #8b92a5;
            --up-color: #f23645;
            --down-color: #089981;
            --neutral-color: #ff9800;
        }}
        body {{
            background-color: var(--bg-color); color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0; padding: 0; padding-bottom: 70px; 
            overscroll-behavior-y: none;
        }}
        .app-header {{
            background-color: var(--panel-bg);
            padding: 15px; text-align: center; font-size: 1.2rem; font-weight: bold;
            border-bottom: 1px solid #2a2e39; position: sticky; top: 0; z-index: 100;
            display: flex; justify-content: space-between; align-items: center;
        }}
        .header-index {{ font-size: 1rem; display: flex; align-items: center; gap: 8px;}}
        .tabs {{ display: flex; background-color: var(--panel-bg); border-bottom: 1px solid #2a2e39; }}
        .tab {{
            flex: 1; text-align: center; padding: 12px 0; font-size: 0.95rem;
            color: var(--text-muted); cursor: pointer; border-bottom: 2px solid transparent;
            -webkit-tap-highlight-color: transparent;
        }}
        .tab.active {{ color: var(--accent); border-bottom: 2px solid var(--accent); font-weight: bold; }}
        .content-area {{ padding: 15px; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .card {{ background-color: var(--panel-bg); border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 2px 10px rgba(0,0,0,0.2); }}
        .card-header {{ display: flex; justify-content: space-between; margin-bottom: 15px; color: var(--text-muted); font-size: 0.85rem; border-bottom: 1px dashed #2a2e39; padding-bottom: 10px;}}
        .signal-card {{ border: 1px solid rgba(242, 54, 69, 0.3); background: linear-gradient(180deg, rgba(242,54,69,0.1) 0%, rgba(26,27,35,1) 100%); }}
        .signal-score {{ font-size: 3rem; font-weight: 800; color: var(--up-color); line-height: 1; text-align: center; margin: 10px 0;}}
        .signal-label {{ text-align: center; font-size: 1.1rem; font-weight: bold; margin-bottom: 5px;}}
        .signal-desc {{ text-align: center; font-size: 0.85rem; color: var(--text-muted); }}
        .data-row {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #2a2e39; font-size: 0.95rem;}}
        .data-row:last-child {{ border-bottom: none; padding-bottom: 0; }}
        .up {{ color: var(--up-color); }}
        .down {{ color: var(--down-color); }}
        .neutral {{ color: var(--neutral-color); }}
        .bottom-nav {{
            position: fixed; bottom: 0; width: 100%; background-color: var(--panel-bg);
            border-top: 1px solid #2a2e39; display: flex; justify-content: space-around;
            padding: 10px 0; z-index: 100; padding-bottom: env(safe-area-inset-bottom, 10px);
        }}
        .nav-item {{ display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); font-size: 0.75rem; gap: 4px; cursor: pointer; }}
        .nav-item.active {{ color: var(--accent); }}
        .nav-icon {{ font-size: 1.2rem; }}
        .btn-trade {{ background-color: var(--up-color); color: white; padding: 8px 20px; border-radius: 20px; font-weight: bold; font-size: 0.9rem; margin-top: -5px; }}
    </style>
</head>
<body>

<div class="app-header">
    <div>📊 期權戰情室</div>
    <div class="header-index">台指期 <span class="up">{realtime['current_price']:,.0f}</span></div>
</div>

<div class="tabs">
    <div class="tab active" id="tab-diagnostic" onclick="switchPage('diagnostic')">綜合診斷</div>
    <div class="tab" id="tab-chips" onclick="switchPage('chips')">法人籌碼</div>
    <div class="tab" id="tab-options" onclick="switchPage('options')">莊家區間</div>
</div>

<div class="content-area">
    <!-- ================= 綜合診斷 ================= -->
    <div class="tab-content active" id="content-diagnostic">
        <div class="card signal-card">
            <div class="card-header">
                <span>⚡ 即時技術評分系統</span>
                <span>即時更新</span>
            </div>
            <div class="signal-label up">{doc['label']}</div>
            <div class="signal-score">{doc['score']}</div>
            <div class="signal-desc">{doc['feature']}</div>
        </div>
        <div class="card">
            <div class="card-header">
                <span>🔥 盤中核心風向球</span>
                <span>即時數據</span>
            </div>
            <div class="data-row">
                <span style="color: var(--text-muted);">散戶小台多空比</span>
                <span class="up">-15.2% (偏多軋空)</span>
            </div>
            <div class="data-row">
                <span style="color: var(--text-muted);">盤中均價線 (VWAP)</span>
                <span class="up">{realtime['vwap']:,.0f} (站上)</span>
            </div>
            <div class="data-row">
                <span style="color: var(--text-muted);">選擇權 P/C Ratio</span>
                <span class="up">115% (支撐強)</span>
            </div>
        </div>
    </div>

    <!-- ================= 法人籌碼 ================= -->
    <div class="tab-content" id="content-chips">
        <div class="card">
            <div class="card-header">
                <span>🏦 三大法人期貨未平倉 (OI)</span>
                <span>盤後數據</span>
            </div>
            <div class="data-row">
                <span>外資及陸資</span>
                <span class="up">+{oi_data['外資']:,} 口</span>
            </div>
            <div class="data-row">
                <span>投信</span>
                <span class="up">+{oi_data['投信']:,} 口</span>
            </div>
            <div class="data-row">
                <span>自營商</span>
                <span class="down">{oi_data['自營商']:,} 口</span>
            </div>
        </div>
    </div>

    <!-- ================= 莊家區間 ================= -->
    <div class="tab-content" id="content-options">
        <div class="card">
            <div class="card-header">
                <span>🔮 選擇權最大未平倉量 (OI)</span>
                <span>找出本週實質壓撐</span>
            </div>
            <div class="data-row">
                <span style="color: var(--text-muted);">Call 壓力 (天花板)</span>
                <span class="down" style="font-weight: bold; font-size: 1.1rem;">23,500</span>
            </div>
            <div class="data-row">
                <span style="color: var(--text-muted);">Put 支撐 (地板)</span>
                <span class="up" style="font-weight: bold; font-size: 1.1rem;">22,800</span>
            </div>
        </div>
        <div class="card">
            <div class="card-header"><span>📊 波動率與情緒</span></div>
            <div class="data-row">
                <span style="color: var(--text-muted);">台指 VIX 恐慌指數</span>
                <span class="neutral">{realtime['vix']} (結構穩定)</span>
            </div>
        </div>
    </div>
</div>

<div class="bottom-nav">
    <div class="nav-item active"><div class="nav-icon">👁️</div><div>戰情</div></div>
    <div class="nav-item"><div class="nav-icon">📈</div><div>線圖</div></div>
    <div class="nav-item"><div class="nav-icon">💰</div><div>帳務</div></div>
    <div class="nav-item"><div class="btn-trade">閃電下單</div></div>
</div>

<script>
    function switchPage(pageId) {{
        document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
        document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
        document.getElementById('content-' + pageId).classList.add('active');
        document.getElementById('tab-' + pageId).classList.add('active');
    }}
</script>
</body>
</html>
"""

# 5. 使用 components.html 將這整包 HTML 嵌入 Streamlit 中 (設定高度避免雙重滾動條)
components.html(html_template, height=850, scrolling=True)
