def get_decision_score(data, fund_data=None, inst_data=None, with_reason=False):
    """
    統一的技術指標決策計分板。
    確保分數永遠一致，且解析 100% 精準對應得分。
    """
    sc, rs = 0, []
    adx = data.get('ADX', 0)
    is_trending = adx >= 25 

    # 1. 趨勢與訊號強度
    if data.get('訊號', False): 
        add_score = 3 if is_trending else 1
        sc += add_score
        if with_reason: 
            rs.append(f"訊號成立 ADX={adx:.1f} +{add_score}")

    # 2. 尋找布林下軌防守支撐
    if data.get('收盤價', 0) <= data.get('BB_DN', 0) * 1.02:
        sc += 2
        if with_reason: 
            rs.append("布林下軌支撐 +2")

    # 3. MACD 動能判定
    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999):
        sc += 2
        if with_reason: 
            rs.append("MACD好轉 +2")
    else:
        sc -= 3
        if with_reason: 
            rs.append("MACD轉弱 -3")
            
    # 4. 爆量點火結構
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1:
        sc += 2
        if with_reason: 
            rs.append("量增 +2")

    # 5. 型態確立
    if data.get('回測有撐', False):
        sc += 2
        if with_reason: 
            rs.append("回測支撐 +2")

    # 基礎分 50 分，上下限控制在 5 ~ 99 分
    final_score = max(5, min(99, int(50 + sc * 3)))

    if final_score >= 60:
        label = "🟢 強勢買進"
    elif final_score >= 45:
        label = "🟡 偏多觀察"
    else:
        label = "⚪ 忽略"

    feature = "一般狀態"
    if data.get('紅吞', False):
        feature = "🔥 紅吞表態"
    elif data.get('回測有撐', False):
        feature = "💪 回檔有撐"

    return (final_score, label, rs, feature) if with_reason else (final_score, label, feature)