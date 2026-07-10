from dataclasses import dataclass
from datetime import datetime, time

from market_session import TAIPEI


ENTRY_ACTIONS = {"BUY_LONG", "SELL_SHORT"}
CLOSE_ACTIONS = {"CLOSE_LONG", "CLOSE_SHORT"}


@dataclass
class RiskDecision:
    allowed: bool
    reasons: list
    daily_trades: int
    daily_pnl: float
    consecutive_losses: int


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TAIPEI)
    return parsed.astimezone(TAIPEI)


def _today_trades(trades, now):
    today = now.astimezone(TAIPEI).date()
    result = []
    for trade in trades:
        trade_time = _parse_time(getattr(trade, "time", ""))
        if trade_time and trade_time.date() == today:
            result.append(trade)
    return result


def _consecutive_losses(close_trades):
    losses = 0
    for trade in reversed(close_trades):
        if float(getattr(trade, "pnl", 0) or 0) < 0:
            losses += 1
        else:
            break
    return losses


def _near_close(market_status, now, minutes=15):
    current = now.astimezone(TAIPEI).time()
    if market_status.session == "day":
        return time(13, 45 - minutes) <= current <= time(13, 45)
    if market_status.session == "night":
        return time(4, 45) <= current <= time(5, 0)
    return False


def evaluate_entry_risk(
    action,
    broker,
    market_status,
    now=None,
    max_daily_trades=3,
    max_daily_loss=1000,
    max_consecutive_losses=2,
    no_new_entry_before_close_minutes=15,
):
    now = now or datetime.now(TAIPEI)
    today_trades = _today_trades(getattr(broker, "trades", []), now)
    entry_count = sum(1 for trade in today_trades if getattr(trade, "action", "") in ENTRY_ACTIONS)
    close_trades = [trade for trade in today_trades if getattr(trade, "action", "") in CLOSE_ACTIONS]
    daily_pnl = sum(float(getattr(trade, "pnl", 0) or 0) for trade in close_trades)
    consecutive_losses = _consecutive_losses(close_trades)

    reasons = []
    if action in ENTRY_ACTIONS:
        if not market_status.allow_new_entry:
            reasons.append(f"目前市場狀態為{market_status.label}，禁止新進場。")
        if entry_count >= max_daily_trades:
            reasons.append(f"今日已達最多 {max_daily_trades} 筆新進場。")
        if daily_pnl <= -abs(max_daily_loss):
            reasons.append(f"今日累計損益 {daily_pnl:,.0f}，已達最大虧損限制。")
        if consecutive_losses >= max_consecutive_losses:
            reasons.append(f"已連續虧損 {consecutive_losses} 筆，今日停止新進場。")
        if _near_close(market_status, now, no_new_entry_before_close_minutes):
            reasons.append(f"收盤前 {no_new_entry_before_close_minutes} 分鐘不開新倉。")

    return RiskDecision(
        allowed=not reasons,
        reasons=reasons,
        daily_trades=entry_count,
        daily_pnl=daily_pnl,
        consecutive_losses=consecutive_losses,
    )
