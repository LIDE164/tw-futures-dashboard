class StrategyManager:
    def __init__(self):
        # 0: 空手, 1: 持有多單, -1: 持有空單
        self.position = 0 
        self.entry_price = 0

    def get_trade_action(self, current_score, current_price):
        action = "HOLD"
        reason = "目前無新訊號，維持既有留倉狀態。"

        # 狀態一：目前【空手】，尋找進場扣板機點
        if self.position == 0:
            if current_score >= 60:
                self.position = 1
                self.entry_price = current_price
                action = "BUY_LONG"
                reason = f"🚀 技術評分達 {current_score} 分符合進場閾值，觸發【做多進場】！"
            elif current_score <= 40: 
                self.position = -1
                self.entry_price = current_price
                action = "SELL_SHORT"
                reason = f"📉 技術評分跌至 {current_score} 分轉為極弱勢，觸發【放空進場】！"
            else:
                reason = "技術評分處於過度區間，等候突破 60 或跌破 40 分進場指令。"

        # 狀態二：目前【持有多單】，尋找出場斷點
        elif self.position == 1:
            profit = current_price - self.entry_price
            
            # 🛡️ 優先級 1：絕對風控線 (虧損達 50 點無條件砍單)
            if profit <= -50:
                self.position = 0
                action = "CLOSE_LONG"
                reason = f"🛑 【強制停損】大盤急殺！帳面虧損達 {profit:+.0f} 點觸發風控，多單無條件平倉！"
            
            # 📉 優先級 2：技術指標轉弱線 (總分跌破 45 分防守點)
            elif current_score < 45:
                self.position = 0
                action = "CLOSE_LONG"
                reason = f"📉 【策略平倉】技術評分降至 {current_score} 分 (跌破45分生命線)，多單平倉獲利了結 (損益: {profit:+.0f} 點)。"
            else:
                reason = f"多單安全續抱中。當前波段損益：{profit:+.0f} 點 (進場點: {self.entry_price:,.0f})"

        # 狀態三：目前【持有空單】，尋找出場斷點
        elif self.position == -1:
            profit = self.entry_price - current_price 
            
            # 🛡️ 優先級 1：絕對風控線 (被軋空達 50 點無條件砍單)
            if profit <= -50:
                self.position = 0
                action = "CLOSE_SHORT"
                reason = f"🛑 【強制停損】大盤暴漲軋空！虧損達 {profit:+.0f} 點觸發風控，空單無條件平倉！"
                
            # 📈 優先級 2：技術指標轉強線 (總分回升突破 45 分)
            elif current_score > 45:
                self.position = 0
                action = "CLOSE_SHORT"
                reason = f"📈 【策略平倉】技術評分回升至 {current_score} 分 (多頭動能回溫)，空單防守平倉 (損益: {profit:+.0f} 點)。"
            else:
                reason = f"空單續抱中。當前波段損益：{profit:+.0f} 點 (進場點: {self.entry_price:,.0f})"

        return action, reason
