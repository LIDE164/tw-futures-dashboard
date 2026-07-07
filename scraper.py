import requests
import pandas as pd
from datetime import datetime

def get_taifex_institutional_oi():
    """
    爬取台灣期交所「三大法人期貨未平倉量」
    """
    url = 'https://www.taifex.com.tw/cht/3/futContractsDate'
    # 這裡我們預設抓取台股期貨 (TXF)
    payload = {
        'queryType': '1',
        'goDay': '',
        'doQuery': '1',
        'dateaddcnt': '',
        'queryDate': datetime.now().strftime('%Y/%m/%d'), # 抓取當天
        'commodityId': 'TXF' 
    }
    
    try:
        res = requests.post(url, data=payload)
        # 用 pandas 直接解析網頁中的表格
        dfs = pd.read_html(res.text)
        
        # 期交所的表格結構比較複雜，通常第 3 個 table 才是我們要的數據
        df = dfs[2] 
        
        # 簡單整理外資、投信、自營商的多空淨額 (這裡以抓取「未平倉餘額 - 多空淨額」為例)
        # 實際欄位索引會依期交所網頁微調，這裡是概念示範
        foreign_oi = df.iloc[5, 13]  # 外資未平倉淨額
        trust_oi = df.iloc[4, 13]    # 投信未平倉淨額
        dealer_oi = df.iloc[3, 13]   # 自營商未平倉淨額
        
        return {
            "外資": int(str(foreign_oi).replace(',', '')),
            "投信": int(str(trust_oi).replace(',', '')),
            "自營商": int(str(dealer_oi).replace(',', ''))
        }
    except Exception as e:
        print(f"爬取失敗: {e}")
        return {"外資": 0, "投信": 0, "自營商": 0}

# 測試印出
# print(get_taifex_institutional_oi())
