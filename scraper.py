import requests
import pandas as pd

def get_taifex_institutional_oi():
    url = 'https://www.taifex.com.tw/cht/3/futContractsDate'
    
    # 秘訣 1：日期留白。期交所會自動回傳「最新一個交易日」的資料，避免遇到假日或盤中無資料
    payload = {
        'queryType': '1',
        'goDay': '',
        'doQuery': '1',
        'dateaddcnt': '',
        'queryDate': '', 
        'commodityId': 'TXF' 
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
    }
    
    try:
        res = requests.post(url, data=payload, headers=headers, timeout=10)
        dfs = pd.read_html(res.text)
        
        # 確認是否有抓到足夠的表格
        if len(dfs) < 3:
            return {"外資": 0, "投信": 0, "自營商": 0, "error": "找不到期交所表格，可能網頁結構已改變"}
            
        df = dfs[2] # 期交所的資料通常在第 3 個表格
        
        # 秘訣 2：強制轉為字串並清除所有逗號與空白，避免 int() 轉換報錯
        foreign_oi = str(df.iloc[5, 13]).replace(',', '').replace(' ', '')
        trust_oi = str(df.iloc[4, 13]).replace(',', '').replace(' ', '')
        dealer_oi = str(df.iloc[3, 13]).replace(',', '').replace(' ', '')
        
        return {
            "外資": int(foreign_oi),
            "投信": int(trust_oi),
            "自營商": int(dealer_oi),
            "error": None # 成功時 error 為 None
        }
    except Exception as e:
        # 秘訣 3：把真實的錯誤原因傳送出去！
        return {"外資": 0, "投信": 0, "自營商": 0, "error": f"爬蟲報錯: {str(e)}"}
