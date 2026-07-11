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
    reward_risk_ratio: float = 0.0
    risk_points: float = 0.0
    reward_points: float = 0.0


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


def evaluate_reward_risk(
    action,
    entry_price=0,
    stop_loss_price=0,
    take_profit_price=0,
    nearest_resistance=0,
    nearest_support=0,
):
    entry_price = float(entry_price or 0)
    stop_loss_price = float(stop_loss_price or 0)
    take_profit_price = float(take_profit_price or 0)
    nearest_resistance = float(nearest_resistance or 0)
    nearest_support = float(nearest_support or 0)

    if action == "BUY_LONG":
        risk_points = entry_price - stop_loss_price
        reward_points = take_profit_price - entry_price
        if nearest_resistance > entry_price:
            reward_points = min(reward_points, nearest_resistance - entry_price)
    elif action == "SELL_SHORT":
        risk_points = stop_loss_price - entry_price
        reward_points = entry_price - take_profit_price
        if 0 < nearest_support < entry_price:
            reward_points = min(reward_points, entry_price - nearest_support)
    else:
        return 0.0, 0.0, 0.0

    if risk_points <= 0 or reward_points <= 0:
        return 0.0, max(0.0, risk_points), max(0.0, reward_points)
    return reward_points / risk_points, risk_points, reward_points


def evaluate_signal_quality(
    action,
    tech_data=None,
    reject_choppy=True,
    require_60m_alignment=True,
    min_adx=20,
    min_volume_ratio=0.85,
    max_chase_atr=1.4,
):
    tech_data = tech_data or {}
    reasons = []

    if action not in ENTRY_ACTIONS:
        return reasons

    adx = float(tech_data.get("ADX") or 0)
    volume_ratio = float(tech_data.get("量比") or 0)
    chase_atr = float(tech_data.get("距MA20_ATR") or 0)
    trend_60m = int(float(tech_data.get("60分趨勢") or 0))

    if reject_choppy and bool(tech_data.get("盤整", False)):
        reasons.append("盤整盤訊號容易來回洗，暫停新進場。")

    if min_adx and adx < float(min_adx):
        reasons.append(f"ADX {adx:.1f} 低於 {float(min_adx):.1f}，趨勢強度不足。")

    if min_volume_ratio and 0 < volume_ratio < float(min_volume_ratio):
        reasons.append(f"量比 {volume_ratio:.2f} 低於 {float(min_volume_ratio):.2f}，成交量不足。")

    if max_chase_atr and chase_atr > float(max_chase_atr):
        reasons.append(f"價格距 MA20 已達 {chase_atr:.2f} ATR，避免追價。")

    if require_60m_alignment:
        if action == "BUY_LONG" and trend_60m <= 0:
            reasons.append("60 分趨勢未偏多，不追多。")
        elif action == "SELL_SHORT" and trend_60m >= 0:
            reasons.append("60 分趨勢未偏空，不追空。")

    return reasons


def evaluate_entry_risk(
    action,
    broker,
    market_status,
    now=None,
    max_daily_trades=3,
    max_daily_loss=1000,
    max_consecutive_losses=2,
    no_new_entry_before_close_minutes=15,
    min_reward_risk_ratio=1.5,
    entry_price=0,
    stop_loss_price=0,
    take_profit_price=0,
    nearest_resistance=0,
    nearest_support=0,
    tech_data=None,
    reject_choppy=True,
    require_60m_alignment=True,
    min_adx=20,
    min_volume_ratio=0.85,
    max_chase_atr=1.4,
):
    now = now or datetime.now(TAIPEI)
    today_trades = _today_trades(getattr(broker, "trades", []), now)
    entry_count = sum(1 for trade in today_trades if getattr(trade, "action", "") in ENTRY_ACTIONS)
    close_trades = [trade for trade in today_trades if getattr(trade, "action", "") in CLOSE_ACTIONS]
    daily_pnl = sum(float(getattr(trade, "pnl", 0) or 0) for trade in close_trades)
    consecutive_losses = _consecutive_losses(close_trades)

    reasons = []
    reward_risk_ratio = 0.0
    risk_points = 0.0
    reward_points = 0.0
    if action in ENTRY_ACTIONS:
        reward_risk_ratio, risk_points, reward_points = evaluate_reward_risk(
            action,
            entry_price,
            stop_loss_price,
            take_profit_price,
            nearest_resistance,
            nearest_support,
        )
        if min_reward_risk_ratio and reward_risk_ratio < float(min_reward_risk_ratio):
            reasons.append(
                f"風險報酬比 {reward_risk_ratio:.2f}R 低於 {float(min_reward_risk_ratio):.2f}R，先不追價。"
            )
        reasons.extend(
            evaluate_signal_quality(
                action,
                tech_data,
                reject_choppy=reject_choppy,
                require_60m_alignment=require_60m_alignment,
                min_adx=min_adx,
                min_volume_ratio=min_volume_ratio,
                max_chase_atr=max_chase_atr,
            )
        )
        if not market_status.allow_new_entry:
            reasons.append(f"市場目前為 {market_status.label}，不允許新進場。")
        if entry_count >= max_daily_trades:
            reasons.append(f"今日已達最多 {max_daily_trades} 筆進場。")
        if daily_pnl <= -abs(max_daily_loss):
            reasons.append(f"今日已實現損益 {daily_pnl:,.0f}，達到每日停損限制。")
        if consecutive_losses >= max_consecutive_losses:
            reasons.append(f"已連續虧損 {consecutive_losses} 筆，今日暫停新進場。")
        if _near_close(market_status, now, no_new_entry_before_close_minutes):
            reasons.append(f"收盤前 {no_new_entry_before_close_minutes} 分鐘不開新倉。")

    return RiskDecision(
        allowed=not reasons,
        reasons=reasons,
        daily_trades=entry_count,
        daily_pnl=daily_pnl,
        consecutive_losses=consecutive_losses,
        reward_risk_ratio=reward_risk_ratio,
        risk_points=risk_points,
        reward_points=reward_points,
    )
