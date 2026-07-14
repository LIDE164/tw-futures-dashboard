import pandas as pd

from entry_confirmation import evaluate_5m_confirmation
from flow_cost_model import add_flow_cost_features, evaluate_flow_cost_entry
from indicators import build_tech_data
from paper_broker import PaperBroker
from scoring import get_decision_score, get_directional_strengths
from strategy import StrategyManager


# The longest indicator needs roughly 240 15-minute bars for a 60-hour trend.
# A bounded causal window keeps parameter scans fast without using future bars.
INDICATOR_LOOKBACK_BARS = 400

try:
    from risk_manager import evaluate_reward_risk, evaluate_signal_quality
except ImportError:
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
        if action not in {"BUY_LONG", "SELL_SHORT"}:
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
        elif lower == "amount":
            rename_map[column] = "Amount"

    df = df.rename(columns=rename_map)
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"K 線缺少欄位：{', '.join(missing)}")

    for column in required:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if "Amount" in df.columns:
        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")

    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")

    return df.dropna(subset=required).reset_index(drop=True)


def _resample_kbars(df, rule="15min"):
    if "ts" not in df.columns or df["ts"].isna().all():
        return df

    aggregation = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }
    if "Amount" in df.columns:
        aggregation["Amount"] = "sum"
    for column in ("FlowBuy", "FlowSell", "SmallFlowBuy", "SmallFlowSell"):
        if column in df.columns:
            aggregation[column] = "sum"
    for column in ("FlowCompleteness", "FlowClassification", "SmallFlowCompleteness", "SmallFlowClassification"):
        if column in df.columns:
            aggregation[column] = "min"

    resampled = (
        df.set_index("ts")
        .resample(rule)
        .agg(aggregation)
        # Flow columns are intentionally sparse before the local Tick archive
        # began. Keep valid OHLCV bars so add_flow_cost_features can use the
        # causal K-bar proxy there, and true Tick flow only where complete.
        .dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        .reset_index()
    )
    resampled.attrs.update(df.attrs)
    resampled.attrs["signal_timeframe"] = rule
    if str(rule).lower() in {"5min", "15min"} and "ts" in resampled.columns:
        minutes = resampled["ts"].dt.hour * 60 + resampled["ts"].dt.minute
        # TAIFEX final prints at 13:45 and 05:00 form one-minute buckets, not a
        # completed 5/15-minute decision bar.
        resampled = resampled[~minutes.isin({13 * 60 + 45, 5 * 60})].reset_index(drop=True)
    return resampled


def _trading_session_key(value):
    ts = pd.Timestamp(value)
    minutes = ts.hour * 60 + ts.minute
    if 8 * 60 + 45 <= minutes < 13 * 60 + 45:
        return f"{ts.date().isoformat()}:day"
    if minutes >= 15 * 60:
        return f"{ts.date().isoformat()}:night"
    if minutes < 5 * 60:
        return f"{(ts - pd.Timedelta(days=1)).date().isoformat()}:night"
    return f"{ts.date().isoformat()}:closed"


def _entry_plan(action, fill_reference, stop_loss_points, take_profit_points, slippage_points):
    fill_reference = float(fill_reference or 0)
    if action == "BUY_LONG":
        fill_price = fill_reference + slippage_points
        return fill_price - stop_loss_points, fill_price + take_profit_points
    if action == "SELL_SHORT":
        fill_price = fill_reference - slippage_points
        return fill_price + stop_loss_points, fill_price - take_profit_points
    return 0.0, 0.0


def _round_to_tick(value, tick=5):
    if value <= 0:
        return 0.0
    return round(float(value) / tick) * tick


def _effective_risk_points(tech_data, fixed_stop, fixed_take, adaptive_risk=True, atr_multiplier=1.2, rr_ratio=2.0):
    fixed_stop = float(fixed_stop)
    fixed_take = float(fixed_take)
    if not adaptive_risk:
        return fixed_stop, fixed_take

    atr_points = float(tech_data.get("ATR") or 0)
    if atr_points <= 0:
        return fixed_stop, fixed_take

    stop_points = _round_to_tick(max(20, min(180, atr_points * float(atr_multiplier))))
    take_points = _round_to_tick(max(stop_points, min(360, stop_points * float(rr_ratio))))
    return stop_points, take_points


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


def _open_entry_bar(trades):
    entry_bar = None
    for trade in trades:
        action = getattr(trade, "action", "")
        trade_bar = getattr(trade, "bar", None)
        if action in {"BUY_LONG", "SELL_SHORT"} and pd.notna(trade_bar):
            entry_bar = int(trade_bar)
        elif action in {"CLOSE_LONG", "CLOSE_SHORT"}:
            entry_bar = None
    return entry_bar


def _maybe_apply_breakeven_stop(broker, bar, trigger_r=0.7, buffer_points=0):
    if broker.position == 0 or not trigger_r:
        return

    entry_price = float(broker.entry_price or 0)
    stop_price = float(broker.stop_loss_price or 0)
    if entry_price <= 0 or stop_price <= 0:
        return

    bar_high = float(bar["High"])
    bar_low = float(bar["Low"])
    buffer_points = float(buffer_points or 0)

    if broker.position > 0:
        risk_points = entry_price - stop_price
        trigger_price = entry_price + risk_points * float(trigger_r)
        breakeven_stop = entry_price + buffer_points
        if risk_points > 0 and bar_high >= trigger_price and stop_price < breakeven_stop:
            broker.stop_loss_price = breakeven_stop

    if broker.position < 0:
        risk_points = stop_price - entry_price
        trigger_price = entry_price - risk_points * float(trigger_r)
        breakeven_stop = entry_price - buffer_points
        if risk_points > 0 and bar_low <= trigger_price and stop_price > breakeven_stop:
            broker.stop_loss_price = breakeven_stop


def _empty_summary():
    return {
        "交易次數": 0,
        "總損益": 0,
        "勝率": 0,
        "勝率可信下限90": 0,
        "期望值": 0,
        "平均獲利": 0,
        "平均虧損": 0,
        "盈虧比": 0,
        "Profit Factor": 0,
        "最大回撤": 0,
        "最大連虧次數": 0,
        "平均持倉K棒數": 0,
        "多單損益": 0,
        "空單損益": 0,
        "多單勝率": 0,
        "空單勝率": 0,
        "多單交易次數": 0,
        "空單交易次數": 0,
        "停損次數": 0,
        "停利次數": 0,
        "策略平倉次數": 0,
        "停損損益": 0,
        "停利損益": 0,
        "策略平倉損益": 0,
        "診斷": [],
    }


def _wilson_lower_bound(wins, total, z=1.64):
    total = int(total or 0)
    wins = int(wins or 0)
    if total <= 0:
        return 0.0
    phat = wins / total
    z2 = z * z
    denominator = 1 + z2 / total
    center = phat + z2 / (2 * total)
    margin = z * ((phat * (1 - phat) + z2 / (4 * total)) / total) ** 0.5
    return max(0.0, min(100.0, ((center - margin) / denominator) * 100))


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
    adaptive_risk=True,
    atr_stop_multiplier=1.2,
    reward_risk_ratio=2.0,
    min_entry_rr=1.5,
    reject_choppy=True,
    require_60m_alignment=True,
    min_adx=20,
    min_volume_ratio=0.85,
    max_chase_atr=1.4,
    confirmation_bars=2,
    require_5m_confirmation=True,
    five_minute_long_score=55,
    five_minute_short_score=45,
    cooldown_bars=1,
    allow_long=True,
    allow_short=True,
    breakeven_trigger_r=0.7,
    breakeven_buffer_points=0,
    max_holding_bars=0,
    score_exit_requires_profit=True,
    min_score_exit_profit_points=0,
    signal_timeframe="15min",
    include_institutional=False,
    use_directional_strength=False,
    long_strength_entry=65,
    short_strength_entry=65,
    use_flow_cost_filter=False,
    flow_lookback_bars=4,
    min_flow_ratio=0.05,
    min_flow_volume_intensity=0.8,
    max_cost_distance_atr=1.2,
    require_cost_slope=True,
    min_flow_close_location=0.0,
    force_flat_at_session_end=False,
):
    if df is None or df.empty or len(df) < 60:
        return pd.DataFrame(), pd.DataFrame(), {"error": "K 線資料不足，至少需要 60 根以上資料。"}

    try:
        normalised = _normalise_kbars(df)
        five_minute_bars = _resample_kbars(normalised, "5min")
        df = _resample_kbars(normalised, signal_timeframe)
        df = add_flow_cost_features(df, lookback_bars=flow_lookback_bars)
    except Exception as exc:
        return pd.DataFrame(), pd.DataFrame(), {"error": str(exc)}

    if len(df) < 60:
        return pd.DataFrame(), pd.DataFrame(), {"error": f"{signal_timeframe} K 線資料不足，至少需要 60 根以上資料。"}

    strategy = StrategyManager(
        long_entry_score=long_entry_score,
        short_entry_score=short_entry_score,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
        score_exit_requires_profit=score_exit_requires_profit,
        min_score_exit_profit_points=min_score_exit_profit_points,
    )
    broker = PaperBroker(
        multiplier=multiplier,
        commission_per_side=commission_per_side,
        slippage_points=slippage_points,
    )
    records = []
    cooldown_until_bar = -1

    for i in range(60, len(df) - 1):
        bar = df.iloc[i]
        session_boundary = bool(
            force_flat_at_session_end
            and "ts" in df.columns
            and _trading_session_key(df["ts"].iloc[i]) != _trading_session_key(df["ts"].iloc[i + 1])
        )
        exit_action, exit_price, exit_note = _check_bar_exit(broker, bar)
        filled_by_bar = False
        fill_message = ""

        if exit_action:
            filled_by_bar, fill_message = broker.execute(exit_action, exit_price, quantity=quantity, note=exit_note)
            if filled_by_bar:
                setattr(broker.trades[-1], "bar", i)
                strategy.reset()
                cooldown_until_bar = max(cooldown_until_bar, i + int(cooldown_bars))
        elif broker.position != 0:
            _maybe_apply_breakeven_stop(broker, bar, breakeven_trigger_r, breakeven_buffer_points)

        if session_boundary and broker.position != 0 and not filled_by_bar:
            close_action = "CLOSE_LONG" if broker.position > 0 else "CLOSE_SHORT"
            filled_by_bar, fill_message = broker.execute(
                close_action,
                float(bar["Close"]),
                quantity=abs(broker.position),
                note="交易時段結束，盤中策略強制平倉",
            )
            if filled_by_bar:
                setattr(broker.trades[-1], "bar", i)
                strategy.reset()
                cooldown_until_bar = max(cooldown_until_bar, i + int(cooldown_bars))

        history_start = max(0, i + 1 - INDICATOR_LOOKBACK_BARS)
        history = df.iloc[history_start : i + 1].copy()
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
        effective_stop_loss_points, effective_take_profit_points = _effective_risk_points(
            tech_data,
            stop_loss_points,
            take_profit_points,
            adaptive_risk,
            atr_stop_multiplier,
            reward_risk_ratio,
        )
        strategy.update_config(
            long_entry_score=long_entry_score,
            short_entry_score=short_entry_score,
            stop_loss_points=effective_stop_loss_points,
            take_profit_points=effective_take_profit_points,
            score_exit_requires_profit=score_exit_requires_profit,
            min_score_exit_profit_points=min_score_exit_profit_points,
        )
        score, label, reasons, feature = get_decision_score(
            tech_data,
            inst_data=inst_data or {} if include_institutional else {},
            with_reason=True,
        )
        action, message = ("HOLD", fill_message) if filled_by_bar else strategy.decide_action(score, current_price)
        long_strength, short_strength = get_directional_strengths(tech_data)
        if use_directional_strength and not filled_by_bar and broker.position == 0:
            long_ready = bool(allow_long and long_strength >= int(long_strength_entry))
            short_ready = bool(allow_short and short_strength >= int(short_strength_entry))
            if long_ready and (not short_ready or long_strength >= short_strength + 5):
                action = "BUY_LONG"
                message = f"獨立多方強度 {long_strength} 達到 {int(long_strength_entry)}。"
            elif short_ready and (not long_ready or short_strength >= long_strength + 5):
                action = "SELL_SHORT"
                message = f"獨立空方強度 {short_strength} 達到 {int(short_strength_entry)}。"
            else:
                action = "HOLD"
                message = f"方向強度未確認：多 {long_strength}／空 {short_strength}。"
        entry_bar = _open_entry_bar(broker.trades)
        holding_bars = i - entry_bar if broker.position != 0 and entry_bar is not None else 0
        if (
            not filled_by_bar
            and max_holding_bars
            and broker.position > 0
            and holding_bars >= int(max_holding_bars)
            and current_price > broker.entry_price
        ):
            action = "CLOSE_LONG"
            message = f"持倉 {holding_bars} 根 K 後仍未到停利，先以獲利出場。"
        elif (
            not filled_by_bar
            and max_holding_bars
            and broker.position < 0
            and holding_bars >= int(max_holding_bars)
            and current_price < broker.entry_price
        ):
            action = "CLOSE_SHORT"
            message = f"持倉 {holding_bars} 根 K 後仍未到停利，先以獲利回補。"
        if action == "BUY_LONG" and not allow_long:
            action = "HOLD"
            message = "目前設定停用多單，跳過做多訊號。"
        elif action == "SELL_SHORT" and not allow_short:
            action = "HOLD"
            message = "目前設定停用空單，跳過做空訊號。"
        elif session_boundary and action in {"BUY_LONG", "SELL_SHORT"}:
            action = "HOLD"
            message = "交易時段即將結束，盤中策略不建立新部位。"

        if action in {"BUY_LONG", "SELL_SHORT"} and require_5m_confirmation:
            confirmation = evaluate_5m_confirmation(
                action,
                five_minute_bars,
                df["ts"].iloc[i] if "ts" in df.columns else None,
                long_confirm_score=five_minute_long_score,
                short_confirm_score=five_minute_short_score,
            )
            if not confirmation["confirmed"]:
                action = "HOLD"
                message = "5 分進場確認未通過：" + " / ".join(
                    confirmation.get("reasons") or [confirmation.get("status", "等待確認")]
                )
                fill_message = message

        flow_cost_features = {}
        if action in {"BUY_LONG", "SELL_SHORT"} and use_flow_cost_filter:
            flow_reasons, flow_cost_features = evaluate_flow_cost_entry(
                action,
                df.iloc[i],
                tech_data.get("ATR") or 0,
                min_flow_ratio=min_flow_ratio,
                min_volume_intensity=min_flow_volume_intensity,
                max_cost_distance_atr=max_cost_distance_atr,
                require_cost_slope=require_cost_slope,
                min_close_location=min_flow_close_location,
            )
            if flow_reasons:
                action = "HOLD"
                message = "K線＋量流＋成本未通過：" + " / ".join(flow_reasons)
                fill_message = message

        filled = filled_by_bar
        rr_ratio = 0.0
        if action != "HOLD" and not filled_by_bar:
            entry_stop, entry_take = _entry_plan(
                action,
                next_open,
                float(effective_stop_loss_points),
                float(effective_take_profit_points),
                float(slippage_points),
            )
            entry_price = next_open + float(slippage_points) if action == "BUY_LONG" else next_open - float(slippage_points)
            rr_ratio, rr_risk, rr_reward = evaluate_reward_risk(
                action,
                entry_price,
                entry_stop,
                entry_take,
                tech_data.get("上方壓力") or 0,
                tech_data.get("下方支撐") or 0,
            )
            if action in {"BUY_LONG", "SELL_SHORT"} and min_entry_rr and rr_ratio < float(min_entry_rr):
                action = "HOLD"
                message = f"風險報酬比 {rr_ratio:.2f}R 低於 {float(min_entry_rr):.2f}R，跳過進場。"
                fill_message = message
                filled = False
            elif action in {"BUY_LONG", "SELL_SHORT"} and i <= cooldown_until_bar:
                action = "HOLD"
                message = f"剛平倉後冷卻 {int(cooldown_bars)} 根 K，跳過新進場。"
                fill_message = message
                filled = False
            elif (
                action in {"BUY_LONG", "SELL_SHORT"}
                and not require_5m_confirmation
                and int(confirmation_bars) >= 2
            ):
                prev_history = history.iloc[:-1].copy()
                prev_realtime = {
                    "current_price": float(prev_history["Close"].iloc[-1]),
                    "volume": float(prev_history["Volume"].iloc[-1]),
                    "vwap": float(prev_history["Close"].iloc[-1]),
                }
                prev_tech = build_tech_data(prev_history, prev_realtime)
                prev_score, _, _, _ = get_decision_score(
                    prev_tech,
                    inst_data=inst_data or {} if include_institutional else {},
                    with_reason=True,
                )
                if use_directional_strength:
                    previous_long, previous_short = get_directional_strengths(prev_tech)
                    missing_confirmation = (
                        action == "BUY_LONG" and previous_long < int(long_strength_entry)
                    ) or (
                        action == "SELL_SHORT" and previous_short < int(short_strength_entry)
                    )
                else:
                    missing_confirmation = (
                        action == "BUY_LONG"
                        and prev_score < int(long_entry_score)
                    ) or (
                        action == "SELL_SHORT"
                        and prev_score > int(short_entry_score)
                    )
                if missing_confirmation:
                    action = "HOLD"
                    message = f"上一根分數 {prev_score} 未連續確認方向，跳過進場。"
                    fill_message = message
                    filled = False
                else:
                    quality_reasons = evaluate_signal_quality(
                        action,
                        tech_data,
                        reject_choppy=reject_choppy,
                        require_60m_alignment=require_60m_alignment,
                        min_adx=min_adx,
                        min_volume_ratio=min_volume_ratio,
                        max_chase_atr=max_chase_atr,
                    )
                    if quality_reasons:
                        action = "HOLD"
                        message = " / ".join(quality_reasons)
                        fill_message = message
                        filled = False
                    else:
                        filled, fill_message = broker.execute(
                            action,
                            next_open,
                            quantity=quantity,
                            note=message,
                            stop_loss_price=entry_stop,
                            take_profit_price=entry_take,
                        )
                        if filled:
                            setattr(broker.trades[-1], "bar", i + 1)
                            strategy.apply_fill(action, next_open, quantity, entry_stop, entry_take)
            elif action in {"BUY_LONG", "SELL_SHORT"}:
                quality_reasons = evaluate_signal_quality(
                    action,
                    tech_data,
                    reject_choppy=reject_choppy,
                    require_60m_alignment=require_60m_alignment,
                    min_adx=min_adx,
                    min_volume_ratio=min_volume_ratio,
                    max_chase_atr=max_chase_atr,
                )
                if quality_reasons:
                    action = "HOLD"
                    message = " / ".join(quality_reasons)
                    fill_message = message
                    filled = False
                else:
                    filled, fill_message = broker.execute(
                        action,
                        next_open,
                        quantity=quantity,
                        note=message,
                        stop_loss_price=entry_stop,
                        take_profit_price=entry_take,
                    )
                    if filled:
                        setattr(broker.trades[-1], "bar", i + 1)
                        strategy.apply_fill(action, next_open, quantity, entry_stop, entry_take)
            else:
                filled, fill_message = broker.execute(
                    action,
                    next_open,
                    quantity=quantity,
                    note=message,
                    stop_loss_price=entry_stop,
                    take_profit_price=entry_take,
                )
                if filled:
                    setattr(broker.trades[-1], "bar", i + 1)
                    strategy.apply_fill(action, next_open, quantity, entry_stop, entry_take)
                    if action in {"CLOSE_LONG", "CLOSE_SHORT"}:
                        cooldown_until_bar = max(cooldown_until_bar, i + int(cooldown_bars))

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
                "risk_stop_points": effective_stop_loss_points,
                "risk_take_points": effective_take_profit_points,
                "reward_risk_ratio": rr_ratio,
                "flow_ratio": flow_cost_features.get("flow_ratio", df.iloc[i].get("FlowRatio", 0)),
                "session_vwap": flow_cost_features.get("session_vwap", df.iloc[i].get("SessionVWAP", 0)),
                "cost_distance_atr": flow_cost_features.get("cost_distance_atr", 0),
                "flow_source": flow_cost_features.get("flow_source", df.iloc[i].get("FlowSource", "kbar_proxy")),
                "realized_pnl": broker.realized_pnl,
                "unrealized_pnl": unrealized,
                "equity": equity,
            }
        )

    if broker.position != 0 and len(df) > 0:
        last_close = float(df["Close"].iloc[-1])
        final_action = "CLOSE_LONG" if broker.position > 0 else "CLOSE_SHORT"
        filled, _ = broker.execute(final_action, last_close, quantity=abs(broker.position), note="回測結束強制平倉")
        if filled:
            setattr(broker.trades[-1], "bar", len(df) - 1)

    trades = broker.trades_df()
    equity_curve = pd.DataFrame(records)
    summary = summarize_backtest(trades, equity_curve)
    return trades, equity_curve, summary


def summarize_backtest(trades, equity_curve):
    if trades.empty:
        return _empty_summary()

    close_trades = trades[trades["pnl"] != 0].copy()
    if close_trades.empty:
        return _empty_summary()

    total_pnl = close_trades["pnl"].sum()
    winners = close_trades[close_trades["pnl"] > 0]
    losers = close_trades[close_trades["pnl"] < 0]
    avg_win = winners["pnl"].mean() if not winners.empty else 0
    avg_loss = losers["pnl"].mean() if not losers.empty else 0
    payoff_ratio = abs(avg_win / avg_loss) if avg_loss else 0
    expectancy = close_trades["pnl"].mean() if not close_trades.empty else 0
    gross_profit = winners["pnl"].sum() if not winners.empty else 0
    gross_loss = abs(losers["pnl"].sum()) if not losers.empty else 0
    profit_factor = gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit > 0 else 0)

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

    holding_bars_list = []
    open_entry_bar = None
    for _, trade in trades.iterrows():
        action = trade.get("action", "")
        trade_bar = trade.get("bar", None)
        if action in {"BUY_LONG", "SELL_SHORT"} and pd.notna(trade_bar):
            open_entry_bar = trade_bar
        elif (
            action in {"CLOSE_LONG", "CLOSE_SHORT"}
            and open_entry_bar is not None
            and pd.notna(open_entry_bar)
            and pd.notna(trade_bar)
        ):
            holding_bars_list.append(max(0, int(trade_bar) - int(open_entry_bar)))
            open_entry_bar = None
    holding_bars = round(float(pd.Series(holding_bars_list).mean()), 2) if holding_bars_list else 0

    long_pnl = close_trades[close_trades["action"].eq("CLOSE_LONG")]["pnl"].sum()
    short_pnl = close_trades[close_trades["action"].eq("CLOSE_SHORT")]["pnl"].sum()
    long_trades = close_trades[close_trades["action"].eq("CLOSE_LONG")]
    short_trades = close_trades[close_trades["action"].eq("CLOSE_SHORT")]
    long_win_rate = (long_trades["pnl"] > 0).mean() * 100 if not long_trades.empty else 0
    short_win_rate = (short_trades["pnl"] > 0).mean() * 100 if not short_trades.empty else 0

    notes = close_trades["note"].fillna("").astype(str)
    stop_mask = notes.str.contains("停損", regex=False)
    take_mask = notes.str.contains("停利|獲利目標", regex=True)
    strategy_exit_mask = ~(stop_mask | take_mask)
    stop_count = int(stop_mask.sum())
    take_count = int(take_mask.sum())
    strategy_exit_count = int(strategy_exit_mask.sum())
    stop_pnl = close_trades.loc[stop_mask, "pnl"].sum()
    take_pnl = close_trades.loc[take_mask, "pnl"].sum()
    strategy_exit_pnl = close_trades.loc[strategy_exit_mask, "pnl"].sum()

    diagnostics = []
    win_count = int((close_trades["pnl"] > 0).sum())
    trade_count = int(len(close_trades))
    win_rate = float((close_trades["pnl"] > 0).mean() * 100)
    win_rate_lower = _wilson_lower_bound(win_count, trade_count)
    if win_rate < 50 and payoff_ratio >= 1.5 and expectancy > 0:
        diagnostics.append("勝率偏低但期望值為正，屬於低勝率高盈虧比；不要只用勝率否定策略。")
    elif win_rate < 50 and expectancy <= 0:
        diagnostics.append("勝率與期望值都偏弱，應優先提高進場品質或降低震盪盤交易。")
    if not long_trades.empty and long_win_rate < 45:
        diagnostics.append("多單勝率偏低，可提高多單門檻或要求 60 分趨勢偏多。")
    if not short_trades.empty and short_win_rate < 45:
        diagnostics.append("空單勝率偏低，可提高空單品質門檻或暫停做空。")
    if stop_count > take_count * 1.5 and stop_count >= 3:
        diagnostics.append("停損次數明顯多於停利，可能停損太窄、追價太遠或盤整過濾不足。")
    if strategy_exit_count > take_count and strategy_exit_pnl < 0:
        diagnostics.append("策略平倉貢獻為負，評分反轉出場可能太敏感。")
    if profit_factor < 1.2:
        diagnostics.append("Profit Factor 低於 1.2，尚未達到可用策略門檻。")
    if win_rate >= 60 and win_rate_lower < 45:
        diagnostics.append("勝率表面達標，但交易樣本不足或不穩，90%可信下限仍偏低。")
    if not diagnostics:
        diagnostics.append("目前主要指標沒有明顯單點問題，請用樣本外驗證確認穩定性。")

    return {
        "交易次數": int(len(close_trades)),
        "總損益": round(float(total_pnl), 0),
        "勝率": round(float(win_rate), 2),
        "勝率可信下限90": round(float(win_rate_lower), 2),
        "期望值": round(float(expectancy), 0),
        "平均獲利": round(float(avg_win), 0),
        "平均虧損": round(float(avg_loss), 0),
        "盈虧比": round(float(payoff_ratio), 2),
        "Profit Factor": round(float(profit_factor), 2),
        "最大回撤": round(float(max_drawdown), 0),
        "最大連虧次數": int(max_losing_streak),
        "平均持倉K棒數": holding_bars,
        "多單損益": round(float(long_pnl), 0),
        "空單損益": round(float(short_pnl), 0),
        "多單勝率": round(float(long_win_rate), 2),
        "空單勝率": round(float(short_win_rate), 2),
        "多單交易次數": int(len(long_trades)),
        "空單交易次數": int(len(short_trades)),
        "停損次數": stop_count,
        "停利次數": take_count,
        "策略平倉次數": strategy_exit_count,
        "停損損益": round(float(stop_pnl), 0),
        "停利損益": round(float(take_pnl), 0),
        "策略平倉損益": round(float(strategy_exit_pnl), 0),
        "診斷": diagnostics,
    }


def optimize_backtest_parameters(df, base_kwargs=None, min_trades=5, top_n=10):
    base_kwargs = dict(base_kwargs or {})
    results = []

    profiles = [
        # name, long, short, min RR, ATR stop, target R, ADX, volume ratio,
        # max chase ATR, confirmation, cooldown, long, short, breakeven R,
        # breakeven buffer, max holding, profitable score exit, min exit profit.
        ("期望值趨勢", 65, 35, 1.5, 1.3, 2.2, 22, 1.00, 1.0, 2, 2, True, True, 1.0, 0, 24, True, 0),
        ("期望值只做多", 62, 35, 1.5, 1.2, 2.2, 22, 1.00, 1.0, 2, 2, True, False, 1.0, 0, 24, True, 0),
        ("低回撤只做多", 65, 35, 1.4, 1.0, 1.8, 25, 1.00, 0.8, 2, 2, True, False, 0.8, 5, 16, True, 0),
        ("高勝率保守", 65, 35, 1.0, 1.0, 1.15, 20, 0.90, 1.0, 2, 2, True, True, 0.6, 5, 12, True, 0),
        ("高勝率標準", 60, 40, 1.0, 1.1, 1.25, 18, 0.85, 1.2, 2, 1, True, True, 0.7, 0, 16, True, 0),
        ("平衡標準", 60, 40, 1.2, 1.2, 1.5, 20, 0.85, 1.4, 2, 1, True, True, 0.8, 0, 24, True, 0),
        ("趨勢型", 65, 35, 1.5, 1.3, 2.0, 22, 1.00, 1.2, 2, 1, True, True, 1.0, 0, 0, False, 0),
        ("只做多高勝率", 65, 35, 1.0, 1.0, 1.15, 20, 0.90, 1.0, 2, 2, True, False, 0.6, 5, 12, True, 0),
        ("只做空高勝率", 65, 35, 1.0, 1.0, 1.15, 20, 0.90, 1.0, 2, 2, False, True, 0.6, 5, 12, True, 0),
    ]

    for (
        profile_name,
        long_score,
        short_score,
        min_rr,
        atr_stop,
        take_rr,
        min_adx,
        min_volume_ratio,
        max_chase,
        confirmation,
        cooldown,
        allow_long,
        allow_short,
        breakeven_trigger,
        breakeven_buffer,
        max_holding,
        score_exit_profit_only,
        score_exit_min_profit,
    ) in profiles:
        kwargs = {
            **base_kwargs,
            "long_entry_score": long_score,
            "short_entry_score": short_score,
            "min_entry_rr": min_rr,
            "atr_stop_multiplier": atr_stop,
            "reward_risk_ratio": take_rr,
            "min_adx": min_adx,
            "min_volume_ratio": min_volume_ratio,
            "max_chase_atr": max_chase,
            "confirmation_bars": confirmation,
            "cooldown_bars": cooldown,
            "allow_long": allow_long,
            "allow_short": allow_short,
            "breakeven_trigger_r": breakeven_trigger,
            "breakeven_buffer_points": breakeven_buffer,
            "max_holding_bars": max_holding,
            "score_exit_requires_profit": score_exit_profit_only,
            "min_score_exit_profit_points": score_exit_min_profit,
            "reject_choppy": True,
            "require_60m_alignment": True,
        }
        _, _, summary = run_backtest(df, **kwargs)
        if summary.get("error"):
            continue
        if int(summary.get("交易次數", 0)) < int(min_trades):
            continue

        win_rate = float(summary.get("勝率", 0) or 0)
        win_rate_lower = float(summary.get("勝率可信下限90", 0) or 0)
        expectancy = float(summary.get("期望值", 0) or 0)
        profit_factor = float(summary.get("Profit Factor", 0) or 0)
        total_pnl = float(summary.get("總損益", 0) or 0)
        max_drawdown = abs(float(summary.get("最大回撤", 0) or 0))
        avg_loss = abs(float(summary.get("平均虧損", 0) or 0))
        trade_count = int(summary.get("交易次數", 0) or 0)
        expectancy_r = expectancy / avg_loss if avg_loss > 0 else 0.0
        recovery_factor = total_pnl / max_drawdown if max_drawdown > 0 else 0.0
        target_hit = win_rate >= 60 and expectancy > 0 and profit_factor >= 1.2
        robust_hit = target_hit and win_rate_lower >= 45
        viable = expectancy > 0 and profit_factor >= 1.0
        robust_expectancy = (
            expectancy > 0
            and profit_factor >= 1.3
            and recovery_factor >= 1.0
            and trade_count >= max(10, int(min_trades))
        )
        quality_score = (
            min(max(profit_factor, 0), 3.0) / 2.0 * 30
            + min(max(expectancy_r, 0), 0.75) / 0.5 * 25
            + min(max(recovery_factor, 0), 3.0) / 2.0 * 20
            + min(max(win_rate_lower, 0), 60.0) / 45.0 * 15
            + min(trade_count, 30) / 30.0 * 10
        )
        results.append(
            {
                "設定類型": profile_name,
                "多單門檻": long_score,
                "空單門檻": short_score,
                "最低RR": min_rr,
                "ATR停損倍數": atr_stop,
                "停利倍數": take_rr,
                "最低ADX": min_adx,
                "最低量比": min_volume_ratio,
                "最大追價ATR": max_chase,
                "確認K": confirmation,
                "冷卻K": cooldown,
                "允許多單": bool(allow_long),
                "允許空單": bool(allow_short),
                "保本觸發R": breakeven_trigger,
                "保本加點": breakeven_buffer,
                "最長持倉K": max_holding,
                "評分出場需浮盈": bool(score_exit_profit_only),
                "評分出場最低浮盈": score_exit_min_profit,
                "正期望": bool(viable),
                "穩健正期望": bool(robust_expectancy),
                "達標": bool(target_hit),
                "可信達標": bool(robust_hit),
                "每筆期望R": round(float(expectancy_r), 3),
                "回撤效率": round(float(recovery_factor), 2),
                "品質分數": round(float(quality_score), 2),
                **summary,
            }
        )

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results)
    return out.sort_values(
        ["穩健正期望", "可信達標", "正期望", "品質分數", "期望值", "回撤效率", "交易次數"],
        ascending=[False, False, False, False, False, False, False],
    ).head(top_n).reset_index(drop=True)


def optimize_then_validate(df, base_kwargs=None, train_ratio=0.7, min_trades=5, top_n=5):
    base_kwargs = dict(base_kwargs or {})
    if df is None or df.empty:
        return pd.DataFrame()

    try:
        prepared = _resample_kbars(_normalise_kbars(df), base_kwargs.get("signal_timeframe", "15min"))
    except Exception:
        return pd.DataFrame()

    split_at = int(len(prepared) * float(train_ratio))
    if split_at < 80 or len(prepared) - split_at < 80:
        return pd.DataFrame()

    train_df = prepared.iloc[:split_at].copy()
    test_df = prepared.iloc[split_at:].copy()
    train_top = optimize_backtest_parameters(
        train_df,
        base_kwargs=base_kwargs,
        min_trades=min_trades,
        top_n=top_n,
    )
    if train_top.empty:
        return pd.DataFrame()

    rows = []
    for _, row in train_top.iterrows():
        params = {
            "long_entry_score": int(row["多單門檻"]),
            "short_entry_score": int(row["空單門檻"]),
            "min_entry_rr": float(row["最低RR"]),
            "atr_stop_multiplier": float(row.get("ATR停損倍數", base_kwargs.get("atr_stop_multiplier", 1.2))),
            "reward_risk_ratio": float(row.get("停利倍數", base_kwargs.get("reward_risk_ratio", 2.0))),
            "min_adx": float(row["最低ADX"]),
            "min_volume_ratio": float(row.get("最低量比", base_kwargs.get("min_volume_ratio", 0.85))),
            "max_chase_atr": float(row["最大追價ATR"]),
            "confirmation_bars": int(row.get("確認K", base_kwargs.get("confirmation_bars", 2))),
            "cooldown_bars": int(row.get("冷卻K", base_kwargs.get("cooldown_bars", 1))),
            "allow_long": bool(row.get("允許多單", base_kwargs.get("allow_long", True))),
            "allow_short": bool(row.get("允許空單", base_kwargs.get("allow_short", True))),
            "breakeven_trigger_r": float(row.get("保本觸發R", base_kwargs.get("breakeven_trigger_r", 0.7))),
            "breakeven_buffer_points": float(row.get("保本加點", base_kwargs.get("breakeven_buffer_points", 0))),
            "max_holding_bars": int(row.get("最長持倉K", base_kwargs.get("max_holding_bars", 0))),
            "score_exit_requires_profit": bool(row.get("評分出場需浮盈", base_kwargs.get("score_exit_requires_profit", True))),
            "min_score_exit_profit_points": float(row.get("評分出場最低浮盈", base_kwargs.get("min_score_exit_profit_points", 0))),
            "reject_choppy": True,
            "require_60m_alignment": True,
        }
        test_kwargs = {**base_kwargs, **params}
        _, _, test_summary = run_backtest(test_df, **test_kwargs)
        if test_summary.get("error"):
            continue
        rows.append(
            {
                **params,
                "設定類型": row.get("設定類型", ""),
                "訓練勝率": row.get("勝率", 0),
                "訓練期望值": row.get("期望值", 0),
                "訓練總損益": row.get("總損益", 0),
                "訓練交易次數": row.get("交易次數", 0),
                "樣本外勝率": test_summary.get("勝率", 0),
                "樣本外勝率可信下限90": test_summary.get("勝率可信下限90", 0),
                "樣本外期望值": test_summary.get("期望值", 0),
                "樣本外總損益": test_summary.get("總損益", 0),
                "樣本外交易次數": test_summary.get("交易次數", 0),
                "樣本外PF": test_summary.get("Profit Factor", 0),
                "樣本外最大回撤": test_summary.get("最大回撤", 0),
                "樣本外達標": bool(
                    float(test_summary.get("勝率", 0) or 0) >= 60
                    and float(test_summary.get("期望值", 0) or 0) > 0
                    and float(test_summary.get("Profit Factor", 0) or 0) >= 1.2
                ),
                "樣本外可信達標": bool(
                    float(test_summary.get("勝率", 0) or 0) >= 60
                    and float(test_summary.get("勝率可信下限90", 0) or 0) >= 45
                    and float(test_summary.get("期望值", 0) or 0) > 0
                    and float(test_summary.get("Profit Factor", 0) or 0) >= 1.2
                ),
            }
        )

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values(
        ["樣本外可信達標", "樣本外達標", "樣本外期望值", "樣本外總損益", "樣本外勝率"],
        ascending=[False, False, False, False, False],
    ).reset_index(drop=True)


def walk_forward_validate(df, base_kwargs=None, folds=3, min_trades=3):
    base_kwargs = dict(base_kwargs or {})
    if df is None or df.empty:
        return pd.DataFrame()

    try:
        prepared = _resample_kbars(_normalise_kbars(df), base_kwargs.get("signal_timeframe", "15min"))
    except Exception:
        return pd.DataFrame()

    folds = max(1, int(folds or 1))
    total_len = len(prepared)
    min_train = 80
    test_size = max(60, total_len // (folds + 2))
    rows = []

    for fold in range(folds):
        train_end = min_train + fold * test_size
        test_end = train_end + test_size
        if train_end < min_train or test_end > total_len:
            continue

        train_df = prepared.iloc[:train_end].copy()
        test_df = prepared.iloc[train_end:test_end].copy()
        train_top = optimize_backtest_parameters(
            train_df,
            base_kwargs=base_kwargs,
            min_trades=max(1, min_trades),
            top_n=1,
        )
        if train_top.empty:
            rows.append({"fold": fold + 1, "狀態": "訓練段無候選"})
            continue

        row = train_top.iloc[0]
        params = {
            "long_entry_score": int(row["多單門檻"]),
            "short_entry_score": int(row["空單門檻"]),
            "min_entry_rr": float(row["最低RR"]),
            "atr_stop_multiplier": float(row.get("ATR停損倍數", base_kwargs.get("atr_stop_multiplier", 1.2))),
            "reward_risk_ratio": float(row.get("停利倍數", base_kwargs.get("reward_risk_ratio", 2.0))),
            "min_adx": float(row["最低ADX"]),
            "min_volume_ratio": float(row.get("最低量比", base_kwargs.get("min_volume_ratio", 0.85))),
            "max_chase_atr": float(row["最大追價ATR"]),
            "confirmation_bars": int(row.get("確認K", base_kwargs.get("confirmation_bars", 2))),
            "cooldown_bars": int(row.get("冷卻K", base_kwargs.get("cooldown_bars", 1))),
            "allow_long": bool(row.get("允許多單", base_kwargs.get("allow_long", True))),
            "allow_short": bool(row.get("允許空單", base_kwargs.get("allow_short", True))),
            "breakeven_trigger_r": float(row.get("保本觸發R", base_kwargs.get("breakeven_trigger_r", 0.7))),
            "breakeven_buffer_points": float(row.get("保本加點", base_kwargs.get("breakeven_buffer_points", 0))),
            "max_holding_bars": int(row.get("最長持倉K", base_kwargs.get("max_holding_bars", 0))),
            "score_exit_requires_profit": bool(row.get("評分出場需浮盈", base_kwargs.get("score_exit_requires_profit", True))),
            "min_score_exit_profit_points": float(row.get("評分出場最低浮盈", base_kwargs.get("min_score_exit_profit_points", 0))),
            "reject_choppy": True,
            "require_60m_alignment": True,
        }
        _, _, test_summary = run_backtest(test_df, **{**base_kwargs, **params})
        if test_summary.get("error"):
            rows.append({"fold": fold + 1, "狀態": test_summary["error"]})
            continue

        rows.append(
            {
                "fold": fold + 1,
                "狀態": "ok",
                "設定類型": row.get("設定類型", ""),
                "訓練勝率": row.get("勝率", 0),
                "訓練期望值": row.get("期望值", 0),
                "樣本外勝率": test_summary.get("勝率", 0),
                "樣本外勝率可信下限90": test_summary.get("勝率可信下限90", 0),
                "樣本外期望值": test_summary.get("期望值", 0),
                "樣本外PF": test_summary.get("Profit Factor", 0),
                "樣本外交易次數": test_summary.get("交易次數", 0),
                "樣本外可信達標": bool(
                    float(test_summary.get("勝率", 0) or 0) >= 60
                    and float(test_summary.get("勝率可信下限90", 0) or 0) >= 45
                    and float(test_summary.get("期望值", 0) or 0) > 0
                    and float(test_summary.get("Profit Factor", 0) or 0) >= 1.2
                ),
            }
        )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
