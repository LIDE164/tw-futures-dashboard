import requests
import pandas as pd
from datetime import datetime

def get_taifex_institutional_oi():
    url = 'https://www.taifex.com.tw/cht/3/futContractsDate'
    payload = {
        'queryType': '1',
        'goDay': '',
        'doQuery': '1',
        'dateaddcnt': '',
        'queryDate': datetime.now().strftime('%Y/%m/%d'), 
        'commodityId': 'TXF' 
    }
    
    # 加入 Headers，偽裝成一般電腦的 Chrome 瀏覽器
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
    }
    
    try:
        # 發送請求時帶上 headers
        res = requests.post(url, data=payload, headers=headers)
        
        # 使用 match 參數，直接尋找包含特定文字的表格，比直接指定 dfs[2] 更不容易因為網頁改版而報錯
        dfs = pd.read_html(res.text, match='多空淨額')
        df = dfs[0] 
        
        # 依照期交所目前最新的欄位結構抓取 (依實際情況可能需要微調索引)
        foreign_oi = df.iloc[5, 13]  
        trust_oi = df.iloc[4, 13]    
        dealer_oi = df.iloc[3, 13]   
        
        return {
            "外資": int(str(foreign_oi).replace(',', '')),
            "投信": int(str(trust_oi).replace(',', '')),
            "自營商": int(str(dealer_oi).replace(',', ''))
        }
    except Exception as e:
        print(f"爬取失敗: {e}")
        return {"外資": 0, "投信": 0, "自營商": 0}
