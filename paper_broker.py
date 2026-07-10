from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


@dataclass
class PaperTrade:
    time: str
    action: str
    price: float
    quantity: int
    pnl: float = 0.0
    note: str = ""
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0


@dataclass
class PaperBroker:
    multiplier: int = 10
    commission_per_side: float = 0.0
    slippage_points: float = 1.0
    position: int = 0
    entry_price: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    realized_pnl: float = 0.0
    trades: list = field(default_factory=list)

    def execute(self, action, price, quantity=1, note="", stop_loss_price=0.0, take_profit_price=0.0):
        price = float(price or 0)
        quantity = int(quantity)

        if price <= 0:
            return False, "價格資料異常，無法模擬成交。"

        if action == "BUY_LONG" and self.position == 0:
            fill_price = price + self.slippage_points
            self.position = quantity
            self.entry_price = fill_price
            self.stop_loss_price = float(stop_loss_price or 0)
            self.take_profit_price = float(take_profit_price or 0)
            self.trades.append(
                PaperTrade(
                    str(datetime.now()),
                    action,
                    fill_price,
                    quantity,
                    0.0,
                    note,
                    self.stop_loss_price,
                    self.take_profit_price,
                )
            )
            return True, "模擬多單進場完成。"

        if action == "SELL_SHORT" and self.position == 0:
            fill_price = price - self.slippage_points
            self.position = -quantity
            self.entry_price = fill_price
            self.stop_loss_price = float(stop_loss_price or 0)
            self.take_profit_price = float(take_profit_price or 0)
            self.trades.append(
                PaperTrade(
                    str(datetime.now()),
                    action,
                    fill_price,
                    quantity,
                    0.0,
                    note,
                    self.stop_loss_price,
                    self.take_profit_price,
                )
            )
            return True, "模擬空單進場完成。"

        if action == "CLOSE_LONG" and self.position > 0:
            fill_price = price - self.slippage_points
            quantity = self.position
            pnl = (fill_price - self.entry_price) * quantity * self.multiplier
            pnl -= self.commission_per_side * 2 * quantity
            self.realized_pnl += pnl
            self.trades.append(
                PaperTrade(
                    str(datetime.now()),
                    action,
                    fill_price,
                    quantity,
                    pnl,
                    note,
                    self.stop_loss_price,
                    self.take_profit_price,
                )
            )
            self.position = 0
            self.entry_price = 0.0
            self.stop_loss_price = 0.0
            self.take_profit_price = 0.0
            return True, f"模擬多單平倉完成，損益 {pnl:,.0f}。"

        if action == "CLOSE_SHORT" and self.position < 0:
            fill_price = price + self.slippage_points
            quantity = abs(self.position)
            pnl = (self.entry_price - fill_price) * quantity * self.multiplier
            pnl -= self.commission_per_side * 2 * quantity
            self.realized_pnl += pnl
            self.trades.append(
                PaperTrade(
                    str(datetime.now()),
                    action,
                    fill_price,
                    quantity,
                    pnl,
                    note,
                    self.stop_loss_price,
                    self.take_profit_price,
                )
            )
            self.position = 0
            self.entry_price = 0.0
            self.stop_loss_price = 0.0
            self.take_profit_price = 0.0
            return True, f"模擬空單平倉完成，損益 {pnl:,.0f}。"

        return False, "目前模擬持倉狀態不允許這個動作。"

    def unrealized_pnl(self, current_price):
        current_price = float(current_price or 0)

        if self.position > 0:
            return (current_price - self.entry_price) * self.position * self.multiplier

        if self.position < 0:
            return (self.entry_price - current_price) * abs(self.position) * self.multiplier

        return 0.0

    def equity(self, current_price):
        return self.realized_pnl + self.unrealized_pnl(current_price)

    def trades_df(self):
        return pd.DataFrame([trade.__dict__ for trade in self.trades])

    def reset(self):
        self.position = 0
        self.entry_price = 0.0
        self.stop_loss_price = 0.0
        self.take_profit_price = 0.0
        self.realized_pnl = 0.0
        self.trades.clear()
