class StrategyManager:
    def __init__(
        self,
        long_entry_score=60,
        short_entry_score=40,
        long_exit_score=45,
        short_exit_score=45,
        stop_loss_points=50,
        take_profit_points=100,
        score_exit_requires_profit=False,
        min_score_exit_profit_points=0,
    ):
        self.position = 0
        self.entry_price = 0.0
        self.stop_loss_price = 0.0
        self.take_profit_price = 0.0
        self.update_config(
            long_entry_score,
            short_entry_score,
            long_exit_score,
            short_exit_score,
            stop_loss_points,
            take_profit_points,
            score_exit_requires_profit,
            min_score_exit_profit_points,
        )

    def update_config(
        self,
        long_entry_score=60,
        short_entry_score=40,
        long_exit_score=45,
        short_exit_score=45,
        stop_loss_points=50,
        take_profit_points=100,
        score_exit_requires_profit=False,
        min_score_exit_profit_points=0,
    ):
        self.long_entry_score = int(long_entry_score)
        self.short_entry_score = int(short_entry_score)
        self.long_exit_score = int(long_exit_score)
        self.short_exit_score = int(short_exit_score)
        self.stop_loss_points = float(stop_loss_points)
        self.take_profit_points = float(take_profit_points)
        self.score_exit_requires_profit = bool(score_exit_requires_profit)
        self.min_score_exit_profit_points = float(min_score_exit_profit_points or 0)

    def sync_position(self, position=0, entry_price=0.0, stop_loss_price=0.0, take_profit_price=0.0):
        self.position = int(position)
        self.entry_price = float(entry_price or 0)
        self.stop_loss_price = float(stop_loss_price or 0)
        self.take_profit_price = float(take_profit_price or 0)

    def reset(self):
        self.sync_position(0, 0.0, 0.0, 0.0)

    def decide_action(self, current_score, current_price):
        current_score = int(current_score)
        current_price = float(current_price or 0)

        if current_price <= 0:
            return "HOLD", "目前價格資料異常，暫停產生交易指令。"

        if self.position == 0:
            if current_score >= self.long_entry_score:
                return "BUY_LONG", f"技術評分達 {current_score} 分，觸發做多進場訊號。"

            if current_score <= self.short_entry_score:
                return "SELL_SHORT", f"技術評分降至 {current_score} 分，觸發放空進場訊號。"

            return (
                "HOLD",
                f"評分位於觀望區間，等待突破 {self.long_entry_score} 或跌破 {self.short_entry_score} 分。",
            )

        if self.position > 0:
            profit_points = current_price - self.entry_price
            stop_price = self.stop_loss_price or self.entry_price - self.stop_loss_points
            take_price = self.take_profit_price or self.entry_price + self.take_profit_points

            if current_price >= take_price:
                return "CLOSE_LONG", f"多單到達預設獲利目標 {take_price:,.0f}，產生平倉訊號。"

            if current_price <= stop_price:
                return "CLOSE_LONG", f"多單達停損價 {stop_price:,.0f}，產生平倉訊號。"

            if current_score < self.long_exit_score:
                if self.score_exit_requires_profit and profit_points < self.min_score_exit_profit_points:
                    return (
                        "HOLD",
                        f"評分降至 {current_score} 分，但多單尚未達浮盈門檻，先依停損/停利觀察。",
                    )
                return "CLOSE_LONG", f"評分降至 {current_score} 分，多單產生策略平倉訊號。"

            return "HOLD", f"多單續抱觀察，波段點數 {profit_points:+.0f}，進場點 {self.entry_price:,.0f}。"

        if self.position < 0:
            profit_points = self.entry_price - current_price
            stop_price = self.stop_loss_price or self.entry_price + self.stop_loss_points
            take_price = self.take_profit_price or self.entry_price - self.take_profit_points

            if current_price <= take_price:
                return "CLOSE_SHORT", f"空單到達預設獲利目標 {take_price:,.0f}，產生回補訊號。"

            if current_price >= stop_price:
                return "CLOSE_SHORT", f"空單達停損價 {stop_price:,.0f}，產生回補訊號。"

            if current_score > self.short_exit_score:
                if self.score_exit_requires_profit and profit_points < self.min_score_exit_profit_points:
                    return (
                        "HOLD",
                        f"評分回升至 {current_score} 分，但空單尚未達浮盈門檻，先依停損/停利觀察。",
                    )
                return "CLOSE_SHORT", f"評分回升至 {current_score} 分，空單產生策略平倉訊號。"

            return "HOLD", f"空單續抱觀察，波段點數 {profit_points:+.0f}，進場點 {self.entry_price:,.0f}。"

        return "HOLD", "策略狀態異常，暫停產生交易指令。"

    def apply_fill(self, action, fill_price, quantity=1, stop_loss_price=0.0, take_profit_price=0.0):
        fill_price = float(fill_price or 0)
        quantity = int(quantity)

        if action == "BUY_LONG":
            self.sync_position(
                quantity,
                fill_price,
                stop_loss_price or fill_price - self.stop_loss_points,
                take_profit_price or fill_price + self.take_profit_points,
            )
        elif action == "SELL_SHORT":
            self.sync_position(
                -quantity,
                fill_price,
                stop_loss_price or fill_price + self.stop_loss_points,
                take_profit_price or fill_price - self.take_profit_points,
            )
        elif action in {"CLOSE_LONG", "CLOSE_SHORT"}:
            self.reset()

    def get_trade_action(self, current_score, current_price):
        return self.decide_action(current_score, current_price)
