import pandas as pd

from indicators import build_tech_data
from paper_broker import PaperBroker
from scoring import get_decision_score
from strategy import StrategyManager


def _normalise_kbars(df):
    df = df.copy()

    rename_map = {}
    for column in df.columns:
        lower = str(column).lower()
        if lower == "open":
            rename_map[column] = "Open"
        elif lower == "high":
            rename_map[column] = "High"
        elif lower == "low":
            rename_map[column] = "Low"
        elif lower == "close":
            rename_map[column] = "Close"
        elif lower == "volume":
            rename_map[column] = "Volume"

    df = df.rename(columns=rename_map)
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"K 線缺少欄位：{', '.join(missing)}")

    for column in required:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")

    return df.dropna(subset=required).reset_index(drop=True)


def _resample_kbars(df, rule="15min"):
    if "ts" not in df.columns or df["ts"].isna().all():
        return df

    resampled = (
        df.set_index("ts")
        .resample(rule)
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        .dropna()
        .reset_index()
    )
    resampled.attrs.update(df.attrs)
    resampled.attrs["signal_timeframe"] = rule
    return resampled


def _entry_plan(action, fill_reference, stop_loss_points, take_profit_points, slippage_points):
    fill_reference = float(fill_reference or 0)
    if action == "BUY_LONG":
        fill_price = fill_reference + slippage_points
        return fill_price - stop_loss_points, fill_price + take_profit_points
    if action == "SELL_SHORT":
        fill_price = fill_reference - slippage_points
        return fill_price + stop_loss_points, fill_price - take_profit_points
    return 0.0, 0.0


def _check_bar_exit(broker, bar, conservative=True):
    if broker.position == 0:
        return None, None, ""

    bar_open = float(bar["Open"])
    bar_high = float(bar["High"])
    bar_low = float(bar["Low"])
    stop_price = float(broker.stop_loss_price or 0)
    take_price = float(broker.take_profit_price or 0)

    if broker.position > 0:
        hit_stop = stop_price > 0 and bar_low <= stop_price
        hit_take = take_price > 0 and bar_high >= take_price
        if hit_stop and (conservative or not hit_take):
            return "CLOSE_LONG", min(bar_open, stop_price), f"多單觸及停損價 {stop_price:,.0f}"
        if hit_take:
            return "CLOSE_LONG", max(bar_open, take_price), f"多單觸及停利價 {take_price:,.0f}"

    if broker.position < 0:
        hit_stop = stop_price > 0 and bar_high >= stop_price
        hit_take = take_price > 0 and bar_low <= take_price
        if hit_stop and (conservative or not hit_take):
            return "CLOSE_SHORT", max(bar_open, stop_price), f"空單觸及停損價 {stop_price:,.0f}"
        if hit_take:
            return "CLOSE_SHORT", min(bar_open, take_price), f"空單觸及停利價 {take_price:,.0f}"

    return None, None, ""


def run_backtest(
    df,
    inst_data=None,
    quantity=1,
    multiplier=200,
    commission_per_side=0.0,
    slippage_points=1.0,
    long_entry_score=60,
    short_entry_score=40,
    stop_loss_points=50,
    take_profit_points=100,
    signal_timeframe="15min",
    include_institutional=False,
):
    if df is None or df.empty or len(df) < 60:
        return pd.DataFrame(), pd.DataFrame(), {"error": "K 線資料不足，至少需要 60 根以上資料。"}

    try:
        df = _normalise_kbars(df)
        df = _resample_kbars(df, signal_timeframe)
    except Exception as exc:
        return pd.DataFrame(), pd.DataFrame(), {"error": str(exc)}

    if len(df) < 60:
        return pd.DataFrame(), pd.DataFrame(), {"error": f"{signal_timeframe} K 線資料不足，至少需要 60 根以上資料。"}

    strategy = StrategyManager(
        long_entry_score=long_entry_score,
        short_entry_score=short_entry_score,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
    )
    broker = PaperBroker(
        multiplier=multiplier,
        commission_per_side=commission_per_side,
        slippage_points=slippage_points,
    )
    records = []

    for i in range(60, len(df) - 1):
        bar = df.iloc[i]
        exit_action, exit_price, exit_note = _check_bar_exit(broker, bar)
        filled_by_bar = False
        fill_message = ""

        if exit_action:
            filled_by_bar, fill_message = broker.execute(exit_action, exit_price, quantity=quantity, note=exit_note)
            if filled_by_bar:
                strategy.reset()

        history = df.iloc[: i + 1].copy()
        current_price = float(history["Close"].iloc[-1])
        next_open = float(df["Open"].iloc[i + 1])

        realtime = {
            "current_price": current_price,
            "volume": float(history["Volume"].iloc[-1]),
            "vwap": current_price,
        }

        strategy.sync_position(
            broker.position,
            broker.entry_price,
            broker.stop_loss_price,
            broker.take_profit_price,
        )
        tech_data = build_tech_data(history, realtime)
        score, label, reasons, feature = get_decision_score(
            tech_data,
            inst_data=inst_data or {} if include_institutional else {},
            with_reason=True,
        )
        action, message = ("HOLD", fill_message) if filled_by_bar else strategy.decide_action(score, current_price)

        filled = filled_by_bar
        if action != "HOLD" and not filled_by_bar:
            entry_stop, entry_take = _entry_plan(
                action,
                next_open,
                float(stop_loss_points),
                float(take_profit_points),
                float(slippage_points),
            )
            filled, fill_message = broker.execute(
                action,
                next_open,
                quantity=quantity,
                note=message,
                stop_loss_price=entry_stop,
                take_profit_price=entry_take,
            )
            if filled:
                strategy.apply_fill(action, next_open, quantity, entry_stop, entry_take)

        unrealized = broker.unrealized_pnl(current_price)
        equity = broker.realized_pnl + unrealized

        records.append(
            {
                "bar": i,
                "price": current_price,
                "next_open": next_open,
                "score": score,
                "label": label,
                "action": action,
                "filled": filled,
                "message": message,
                "fill_message": fill_message,
                "position": broker.position,
                "stop_loss_price": broker.stop_loss_price,
                "take_profit_price": broker.take_profit_price,
                "realized_pnl": broker.realized_pnl,
                "unrealized_pnl": unrealized,
                "equity": equity,
            }
        )

    if broker.position != 0 and len(df) > 0:
        last_close = float(df["Close"].iloc[-1])
        final_action = "CLOSE_LONG" if broker.position > 0 else "CLOSE_SHORT"
        broker.execute(final_action, last_close, quantity=abs(broker.position), note="回測結束強制平倉")

    trades = broker.trades_df()
    equity_curve = pd.DataFrame(records)
    summary = summarize_backtest(trades, equity_curve)
    return trades, equity_curve, summary


def summarize_backtest(trades, equity_curve):
    if trades.empty:
        return {
            "交易次數": 0,
            "總損益": 0,
            "勝率": 0,
            "平均獲利": 0,
            "平均虧損": 0,
            "盈虧比": 0,
            "最大回撤": 0,
            "最大連虧次數": 0,
            "平均持倉K棒數": 0,
            "多單損益": 0,
            "空單損益": 0,
        }

    close_trades = trades[trades["pnl"] != 0].copy()
    if close_trades.empty:
        return {
            "交易次數": 0,
            "總損益": 0,
            "勝率": 0,
            "平均獲利": 0,
            "平均虧損": 0,
            "盈虧比": 0,
            "最大回撤": 0,
            "最大連虧次數": 0,
            "平均持倉K棒數": 0,
            "多單損益": 0,
            "空單損益": 0,
        }

    total_pnl = close_trades["pnl"].sum()
    winners = close_trades[close_trades["pnl"] > 0]
    losers = close_trades[close_trades["pnl"] < 0]
    avg_win = winners["pnl"].mean() if not winners.empty else 0
    avg_loss = losers["pnl"].mean() if not losers.empty else 0
    payoff_ratio = abs(avg_win / avg_loss) if avg_loss else 0

    if not equity_curve.empty:
        peak = equity_curve["equity"].cummax()
        max_drawdown = (equity_curve["equity"] - peak).min()
    else:
        max_drawdown = 0

    max_losing_streak = 0
    current_streak = 0
    for pnl in close_trades["pnl"]:
        if pnl < 0:
            current_streak += 1
            max_losing_streak = max(max_losing_streak, current_streak)
        else:
            current_streak = 0

    close_trades = close_trades.reset_index(drop=True)
    entries = trades[trades["pnl"] == 0].reset_index(drop=True)
    holding_bars = 0
    if not entries.empty and len(entries) == len(close_trades):
        holding_bars = 1

    long_pnl = close_trades[close_trades["action"].eq("CLOSE_LONG")]["pnl"].sum()
    short_pnl = close_trades[close_trades["action"].eq("CLOSE_SHORT")]["pnl"].sum()

    return {
        "交易次數": int(len(close_trades)),
        "總損益": round(float(total_pnl), 0),
        "勝率": round(float((close_trades["pnl"] > 0).mean() * 100), 2),
        "平均獲利": round(float(avg_win), 0),
        "平均虧損": round(float(avg_loss), 0),
        "盈虧比": round(float(payoff_ratio), 2),
        "最大回撤": round(float(max_drawdown), 0),
        "最大連虧次數": int(max_losing_streak),
        "平均持倉K棒數": holding_bars,
        "多單損益": round(float(long_pnl), 0),
        "空單損益": round(float(short_pnl), 0),
    }
