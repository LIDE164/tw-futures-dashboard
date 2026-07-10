class StrategyManager:
    def __init__(
        self,
        long_entry_score=60,
        short_entry_score=40,
        long_exit_score=45,
        short_exit_score=45,
        stop_loss_points=50,
    ):
        self.position = 0
        self.entry_price = 0.0
        self.update_config(
            long_entry_score,
            short_entry_score,
            long_exit_score,
            short_exit_score,
            stop_loss_points,
        )

    def update_config(
        self,
        long_entry_score=60,
        short_entry_score=40,
        long_exit_score=45,
        short_exit_score=45,
        stop_loss_points=50,
    ):
        self.long_entry_score = int(long_entry_score)
        self.short_entry_score = int(short_entry_score)
        self.long_exit_score = int(long_exit_score)
        self.short_exit_score = int(short_exit_score)
        self.stop_loss_points = float(stop_loss_points)

    def reset(self):
        self.position = 0
        self.entry_price = 0.0

    def get_trade_action(self, current_score, current_price):
        current_score = int(current_score)
        current_price = float(current_price or 0)

        if current_price <= 0:
            return "HOLD", "目前價格資料異常，暫停產生交易指令。"

        action = "HOLD"
        reason = "目前無新訊號，維持既有留倉狀態。"

        if self.position == 0:
            if current_score >= self.long_entry_score:
                self.position = 1
                self.entry_price = current_price
                action = "BUY_LONG"
                reason = f"技術評分達 {current_score} 分，觸發做多進場。"
            elif current_score <= self.short_entry_score:
                self.position = -1
                self.entry_price = current_price
                action = "SELL_SHORT"
                reason = f"技術評分降至 {current_score} 分，觸發放空進場。"
            else:
                reason = (
                    f"評分位於觀望區間，等待突破 {self.long_entry_score} "
                    f"或跌破 {self.short_entry_score} 分。"
                )

        elif self.position == 1:
            profit = current_price - self.entry_price

            if profit <= -self.stop_loss_points:
                self.reset()
                action = "CLOSE_LONG"
                reason = f"強制停損：多單虧損 {profit:+.0f} 點，觸發風控平倉。"
            elif current_score < self.long_exit_score:
                self.reset()
                action = "CLOSE_LONG"
                reason = f"策略平倉：評分降至 {current_score} 分，多單平倉，損益 {profit:+.0f} 點。"
            else:
                reason = f"多單續抱中，當前波段損益 {profit:+.0f} 點，進場點 {self.entry_price:,.0f}。"

        elif self.position == -1:
            profit = self.entry_price - current_price

            if profit <= -self.stop_loss_points:
                self.reset()
                action = "CLOSE_SHORT"
                reason = f"強制停損：空單虧損 {profit:+.0f} 點，觸發風控平倉。"
            elif current_score > self.short_exit_score:
                self.reset()
                action = "CLOSE_SHORT"
                reason = f"策略平倉：評分回升至 {current_score} 分，空單平倉，損益 {profit:+.0f} 點。"
            else:
                reason = f"空單續抱中，當前波段損益 {profit:+.0f} 點，進場點 {self.entry_price:,.0f}。"

        return action, reason
