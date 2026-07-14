import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import sinopac_api
from adaptive_learning import apply_active_parameters
from alert_manager import (
    dispatch_alert,
    dispatch_hourly_analysis,
    dispatch_preopen_briefing,
    dispatch_research_report,
    dispatch_whale_distribution,
)
from briefing_image import render_preopen_briefing_image
from daily_research import format_close_research_report, run_close_research
from direction_observation import build_direction_observation, evaluate_formal_candidate
from entry_confirmation import evaluate_5m_confirmation
from historical_data import (
    get_history_status,
    load_continuous_kbars,
    load_order_flow_minutes,
    upsert_contract_kbars,
    upsert_order_flow_minutes,
)
from flow_cost_model import merge_true_order_flow
from hourly_report import build_hourly_analysis, format_hourly_analysis
from indicators import build_tech_data
from market_data import get_public_market_data
from market_session import TAIPEI, get_market_status
from paper_broker import PaperBroker
from preopen_briefing import build_preopen_briefing, format_preopen_briefing
from preopen_learning import evaluate_pending_forecasts, record_preopen_forecast
from risk_manager import evaluate_entry_risk, evaluate_signal_quality
from scoring import get_decision_score
from storage import (
    load_json_state,
    load_worker_trade_state,
    restore_paper_broker_state,
    save_paper_broker_state,
    save_signal,
    save_json_state,
    save_worker_trade_state,
    update_heartbeat,
)
from strategy import StrategyManager
from whale_monitor import WhaleFlowMonitor, build_distribution_event, build_test_distribution_event


SIGNAL_TIMEFRAME = "15min"
PRODUCT_ROOT = getattr(sinopac_api, "DEFAULT_FUTURES_ROOT", "TMF")
WORKER_NAME = "signal_worker"
TEST_SIGNAL_ACTIONS = {"BUY_LONG", "SELL_SHORT", "CLOSE_LONG", "CLOSE_SHORT"}
TEST_EXIT_EVENTS = {"STOP", "TARGET"}
HEARTBEAT_TEXT_PATH = Path("data/signal_worker_heartbeat.txt")
HISTORY_MAINTENANCE_STATE_KEY = "history_maintenance"
PREOPEN_BRIEFING_STATE_KEY = "preopen_briefing"
HOURLY_ANALYSIS_STATE_KEY = "hourly_analysis"
WHALE_FLOW_STATE_KEY = "whale_flow_monitor"


def _update_worker_heartbeat(status, detail=""):
    update_heartbeat(WORKER_NAME, status, detail)
    HEARTBEAT_TEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_TEXT_PATH.write_text(
        f"updated_at={datetime.now(TAIPEI):%Y-%m-%d %H:%M:%S}\nstatus={status}\ndetail={detail}\n",
        encoding="utf-8",
    )


def _resample_completed_bars(df, rule=SIGNAL_TIMEFRAME):
    if df is None or df.empty or "ts" not in df.columns:
        return pd.DataFrame()

    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in df.columns for column in required):
        return pd.DataFrame()

    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
    out = (
        out.dropna(subset=["ts"])
        .set_index("ts")
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
    current_bucket = pd.Timestamp.now(tz=TAIPEI).tz_localize(None).floor(rule)
    out = out[out["ts"] < current_bucket].copy()
    out.attrs.update(getattr(df, "attrs", {}))
    return out


def _entry_plan(action, realtime, stop_loss_points, take_profit_points):
    current_price = float(realtime.get("current_price") or 0)
    bid_price = float(realtime.get("bid_price") or 0)
    ask_price = float(realtime.get("ask_price") or 0)

    if action == "BUY_LONG":
        entry = ask_price or current_price
        return entry, entry - stop_loss_points, entry + take_profit_points

    if action == "SELL_SHORT":
        entry = bid_price or current_price
        return entry, entry + stop_loss_points, entry - take_profit_points

    return current_price, 0.0, 0.0


def _event_type(action, message, broker=None, current_price=0):
    if action == "BUY_LONG":
        return "ENTRY_LONG"
    if action == "SELL_SHORT":
        return "ENTRY_SHORT"
    if action in {"CLOSE_LONG", "CLOSE_SHORT"}:
        current_price = float(current_price or 0)
        if broker is not None and broker.position > 0:
            if broker.stop_loss_price and current_price <= broker.stop_loss_price:
                return "EXIT_STOP"
            if broker.take_profit_price and current_price >= broker.take_profit_price:
                return "EXIT_TARGET"
        if broker is not None and broker.position < 0:
            if broker.stop_loss_price and current_price >= broker.stop_loss_price:
                return "EXIT_STOP"
            if broker.take_profit_price and current_price <= broker.take_profit_price:
                return "EXIT_TARGET"
        if "停損" in message:
            return "EXIT_STOP"
        if "獲利目標" in message or "停利" in message:
            return "EXIT_TARGET"
        return "EXIT_SCORE"
    return "HOLD"


def _sync_paper_tracking(broker, action, current_price, entry_price, stop_price, take_price, message, args):
    if not args.auto_paper_fill:
        return False, "paper tracking disabled"

    if action in {"BUY_LONG", "SELL_SHORT"}:
        filled, fill_msg = broker.execute(
            action,
            entry_price,
            quantity=args.paper_quantity,
            note=f"worker alert: {message}",
            stop_loss_price=stop_price,
            take_profit_price=take_price,
        )
    elif action in {"CLOSE_LONG", "CLOSE_SHORT"}:
        filled, fill_msg = broker.execute(
            action,
            current_price,
            quantity=abs(broker.position) or args.paper_quantity,
            note=f"worker alert: {message}",
        )
    else:
        return False, "no paper tracking action"

    if filled:
        save_paper_broker_state(broker)
    return filled, fill_msg


def _round_to_tick(value, tick=5):
    if value <= 0:
        return 0.0
    return round(float(value) / tick) * tick


def _effective_risk_points(args, tech_data):
    fixed_stop = float(args.stop_loss_points)
    fixed_take = float(args.take_profit_points)
    if not args.adaptive_risk:
        return fixed_stop, fixed_take

    atr_points = float(tech_data.get("ATR") or 0)
    if atr_points <= 0:
        return fixed_stop, fixed_take

    stop_points = _round_to_tick(max(20, min(180, atr_points * float(args.atr_stop_multiplier))))
    take_points = _round_to_tick(max(stop_points, min(360, stop_points * float(args.reward_risk_ratio))))
    return stop_points, take_points


def _bar_timestamp(value):
    timestamp = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(timestamp) else timestamp


def _bars_since(bars, bar_time):
    start = _bar_timestamp(bar_time)
    if start is None or bars is None or bars.empty:
        return 0
    timestamps = pd.to_datetime(bars["ts"], errors="coerce")
    return int((timestamps > start).sum())


def _reconcile_worker_trade_state(broker, bars):
    state = load_worker_trade_state()
    latest_bar = _bar_timestamp(bars["ts"].iloc[-1])
    latest_bar_text = latest_bar.isoformat() if latest_bar is not None else ""
    saved_position = int(state.get("position") or 0)

    if broker.position == 0:
        if saved_position != 0:
            state["last_exit_bar_time"] = latest_bar_text
        state.update(
            {
                "position": 0,
                "entry_bar_time": "",
                "original_stop_price": 0.0,
                "breakeven_applied": False,
            }
        )
    elif saved_position != broker.position or not state.get("entry_bar_time"):
        state.update(
            {
                "position": broker.position,
                "entry_bar_time": latest_bar_text,
                "original_stop_price": float(broker.stop_loss_price or 0),
                "breakeven_applied": False,
            }
        )

    save_worker_trade_state(state)
    return state


def _apply_breakeven_stop(broker, state, current_price, trigger_r, buffer_points):
    if broker.position == 0 or not trigger_r or state.get("breakeven_applied"):
        return False

    entry_price = float(broker.entry_price or 0)
    original_stop = float(state.get("original_stop_price") or broker.stop_loss_price or 0)
    current_price = float(current_price or 0)
    buffer_points = float(buffer_points or 0)
    risk_points = abs(entry_price - original_stop)
    if entry_price <= 0 or original_stop <= 0 or current_price <= 0 or risk_points <= 0:
        return False

    if broker.position > 0:
        triggered = current_price >= entry_price + risk_points * float(trigger_r)
        new_stop = entry_price + buffer_points
        improves_stop = not broker.stop_loss_price or broker.stop_loss_price < new_stop
    else:
        triggered = current_price <= entry_price - risk_points * float(trigger_r)
        new_stop = entry_price - buffer_points
        improves_stop = not broker.stop_loss_price or broker.stop_loss_price > new_stop

    if not triggered or not improves_stop:
        return False

    broker.stop_loss_price = float(new_stop)
    state["breakeven_applied"] = True
    save_paper_broker_state(broker)
    save_worker_trade_state(state)
    return True


def _record_worker_fill_state(state, broker, action, bar_time, stop_price=0):
    timestamp = _bar_timestamp(bar_time)
    bar_text = timestamp.isoformat() if timestamp is not None else str(bar_time or "")
    if action in {"BUY_LONG", "SELL_SHORT"}:
        state.update(
            {
                "position": broker.position,
                "entry_bar_time": bar_text,
                "original_stop_price": float(stop_price or broker.stop_loss_price or 0),
                "breakeven_applied": False,
            }
        )
    elif action in {"CLOSE_LONG", "CLOSE_SHORT"}:
        state.update(
            {
                "position": 0,
                "entry_bar_time": "",
                "original_stop_price": 0.0,
                "breakeven_applied": False,
                "last_exit_bar_time": bar_text,
            }
        )
    save_worker_trade_state(state)


def _close_report_due(now, args):
    if not args.daily_close_report or now.weekday() >= 5:
        return False
    current_minutes = now.hour * 60 + now.minute
    report_minutes = int(args.daily_report_hour) * 60 + int(args.daily_report_minute)
    return report_minutes <= current_minutes < 15 * 60


def _preopen_session(now, args):
    if not args.preopen_briefing or now.weekday() >= 5:
        return ""
    current_minutes = now.hour * 60 + now.minute
    day_send = int(args.day_preopen_hour) * 60 + int(args.day_preopen_minute)
    night_send = int(args.night_preopen_hour) * 60 + int(args.night_preopen_minute)
    if day_send <= current_minutes < 8 * 60 + 45:
        return "day"
    if args.night_preopen_briefing and night_send <= current_minutes < 15 * 60:
        return "night"
    return ""


def _hourly_analysis_key(now, market_status, args):
    if not args.hourly_report or not market_status.is_open:
        return ""
    hour = int(now.hour)
    if market_status.session == "day" and hour not in {9, 10, 11, 12, 13}:
        return ""
    if market_status.session == "night" and hour not in set(range(16, 24)) | set(range(0, 5)):
        return ""
    return f"{now.date().isoformat()}:{market_status.session}:{hour:02d}"


def _maybe_send_hourly_analysis(
    now,
    args,
    market_status,
    realtime,
    bars,
    tech_data,
    score,
    label,
    reasons,
    action,
    message,
    broker,
    stop_loss_points,
    take_profit_points,
):
    hour_key = _hourly_analysis_key(now, market_status, args)
    if not hour_key or bars is None or bars.empty:
        return None
    state = load_json_state(HOURLY_ANALYSIS_STATE_KEY, {})
    if state.get("last_hour_key") == hour_key:
        return None

    latest_bar = pd.to_datetime(bars["ts"].iloc[-1], errors="coerce")
    now_naive = now.replace(tzinfo=None) if now.tzinfo is not None else now
    if pd.isna(latest_bar) or latest_bar > now_naive or now_naive - latest_bar > timedelta(minutes=90):
        return None

    monitor = getattr(args, "_whale_monitor", None)
    if monitor is None:
        return None
    flow_summary = monitor.hourly_summary(now_naive)
    analysis = build_hourly_analysis(
        hour_key=hour_key,
        market_status=market_status,
        realtime=realtime,
        bars=bars,
        tech_data=tech_data,
        flow_summary=flow_summary,
    )
    body = format_hourly_analysis(analysis)
    _, delivery = dispatch_hourly_analysis(analysis, body, image_path=None)
    state.update(
        {
            "last_hour_key": hour_key,
            "last_sent_at": now.isoformat(timespec="seconds"),
            "last_delivery": delivery,
            "last_bar_time": str(latest_bar),
        }
    )
    save_json_state(HOURLY_ANALYSIS_STATE_KEY, state)
    return {"analysis": analysis, "delivery": delivery}


def _maybe_send_preopen_briefing(
    now,
    args,
    realtime,
    bars,
    tech_data,
    score,
    label,
    reasons,
    action,
    message,
    broker,
    stop_loss_points,
    take_profit_points,
):
    session = _preopen_session(now, args)
    if not session:
        return None

    session_key = f"{now.date().isoformat()}:{session}"
    state = load_json_state(PREOPEN_BRIEFING_STATE_KEY, {})
    if state.get("last_session_key") == session_key:
        return None

    quality_reasons = evaluate_signal_quality(
        action,
        tech_data,
        reject_choppy=args.reject_choppy,
        require_60m_alignment=args.require_60m_alignment,
        min_adx=args.min_adx,
        min_volume_ratio=args.min_volume_ratio,
        max_chase_atr=args.max_chase_atr,
    )
    evaluate_pending_forecasts(bars, now=now)
    public_data = get_public_market_data()
    briefing = build_preopen_briefing(
        session=session,
        session_key=session_key,
        realtime=realtime,
        bars=bars,
        tech_data=tech_data,
        score=score,
        label=label,
        reasons=reasons,
        action=action,
        message=message,
        public_data=public_data,
        broker=broker,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
        commission_per_side=args.commission_per_side,
        allow_long=args.allow_long,
        allow_short=args.allow_short,
        quality_reasons=quality_reasons,
    )
    body = format_preopen_briefing(briefing)
    image_path = None
    try:
        image_path = render_preopen_briefing_image(briefing, bars)
    except Exception as exc:
        print(f"preopen image fallback to text: {exc}")
    _, delivery = dispatch_preopen_briefing(briefing, body, image_path=image_path)
    record_preopen_forecast(briefing)
    state.update(
        {
            "last_session_key": session_key,
            "last_sent_at": now.isoformat(timespec="seconds"),
            "last_delivery": delivery,
        }
    )
    save_json_state(PREOPEN_BRIEFING_STATE_KEY, state)
    return {"briefing": briefing, "delivery": delivery}


def _research_base_kwargs(args):
    return {
        "quantity": int(args.paper_quantity),
        "multiplier": 10,
        "commission_per_side": float(args.commission_per_side),
        "slippage_points": float(args.slippage_points),
        "long_entry_score": int(args.long_entry_score),
        "short_entry_score": int(args.short_entry_score),
        "stop_loss_points": float(args.stop_loss_points),
        "take_profit_points": float(args.take_profit_points),
        "adaptive_risk": bool(args.adaptive_risk),
        "atr_stop_multiplier": float(args.atr_stop_multiplier),
        "reward_risk_ratio": float(args.reward_risk_ratio),
        "min_entry_rr": float(args.min_entry_rr),
        "reject_choppy": bool(args.reject_choppy),
        "require_60m_alignment": bool(args.require_60m_alignment),
        "min_adx": float(args.min_adx),
        "min_volume_ratio": float(args.min_volume_ratio),
        "max_chase_atr": float(args.max_chase_atr),
        "confirmation_bars": int(args.confirmation_bars),
        "require_5m_confirmation": bool(args.require_5m_confirmation),
        "five_minute_long_score": int(args.five_minute_long_score),
        "five_minute_short_score": int(args.five_minute_short_score),
        "cooldown_bars": int(args.cooldown_bars),
        "allow_long": bool(args.allow_long),
        "allow_short": bool(args.allow_short),
        "breakeven_trigger_r": float(args.breakeven_trigger_r),
        "breakeven_buffer_points": float(args.breakeven_buffer_points),
        "max_holding_bars": int(args.max_holding_bars),
        "score_exit_requires_profit": bool(args.score_exit_requires_profit),
        "min_score_exit_profit_points": float(args.min_score_exit_profit_points),
        "signal_timeframe": SIGNAL_TIMEFRAME,
        "include_institutional": False,
    }


def _maintain_history_and_report(raw_kbars, args, now=None):
    now = now or datetime.now(TAIPEI)
    state = load_json_state(HISTORY_MAINTENANCE_STATE_KEY, {})
    report_date = now.date().isoformat()
    attrs = getattr(raw_kbars, "attrs", {})
    contract_code = str(attrs.get("contract_code", "") or "")
    delivery_date = str(attrs.get("delivery_date", "") or "")
    source = str(attrs.get("source", "Sinopac Shioaji kbars"))
    report_due = _close_report_due(now, args)
    sync_due = (
        bool(args.history_sync)
        and raw_kbars is not None
        and not raw_kbars.empty
        and contract_code
        and (
            state.get("last_sync_date") != report_date
            or state.get("contract_code") != contract_code
            or (report_due and state.get("close_sync_date") != report_date)
        )
    )

    sync_result = {}
    if sync_due:
        sync_result = upsert_contract_kbars(
            raw_kbars,
            contract_code=contract_code,
            delivery_date=delivery_date,
            product_root=PRODUCT_ROOT,
            source=source,
            synced_at=now.replace(tzinfo=None),
        )
        state.update(
            {
                "last_sync_date": report_date,
                "last_sync_at": now.isoformat(timespec="seconds"),
                "contract_code": contract_code,
            }
        )
        if report_due:
            state["close_sync_date"] = report_date
        save_json_state(HISTORY_MAINTENANCE_STATE_KEY, state)

    if not report_due or state.get("last_report_date") == report_date:
        return {"sync": sync_result, "report": None, "delivery": ""}

    history_status = get_history_status(PRODUCT_ROOT)
    if not str(history_status.get("last_ts") or "").startswith(report_date):
        return {"sync": sync_result, "report": None, "delivery": "no current trading-day bars"}

    history_start = now.replace(tzinfo=None) - timedelta(days=max(90, int(args.research_history_days)))
    history = load_continuous_kbars(PRODUCT_ROOT, start=history_start)
    true_flow = load_order_flow_minutes(start=history_start)
    history = merge_true_order_flow(history, true_flow)
    report = run_close_research(
        history,
        report_date=report_date,
        history_status=history_status,
        base_kwargs=_research_base_kwargs(args),
        folds=args.research_folds,
        min_reference_trades=args.min_reference_trades,
        min_oos_trades=args.min_oos_trades,
        run_optimisation=now.weekday() == 4,
        allow_auto_learning=args.adaptive_learning,
    )
    report_body = format_close_research_report(report)
    _, delivery = dispatch_research_report(report, report_body)
    state["last_report_date"] = report_date
    state["last_report_at"] = now.isoformat(timespec="seconds")
    state["last_report_delivery"] = delivery
    save_json_state(HISTORY_MAINTENANCE_STATE_KEY, state)
    return {"sync": sync_result, "report": report, "delivery": delivery}


def _heartbeat_detail(market_status, realtime, bars, score=None, label="", action="", message=""):
    current_price = float(realtime.get("current_price") or 0)
    bar_time = "no_completed_bar"
    if bars is not None and not bars.empty and "ts" in bars.columns:
        bar_time = pd.to_datetime(bars["ts"].iloc[-1]).strftime("%Y/%m/%d %H:%M")
    parts = [
        f"checked_at {datetime.now(TAIPEI):%Y/%m/%d %H:%M:%S}",
        f"market {market_status.label}",
        f"price {current_price:,.0f}",
        f"bar {bar_time}",
    ]
    if score is not None:
        parts.append(f"score {score} {label}")
    if action:
        parts.append(f"action {action}")
    if message:
        parts.append(str(message))
    return " | ".join(parts)


def _build_test_signal(action, args):
    now = datetime.now(TAIPEI)
    price = float(args.test_price)
    if price <= 0:
        price = 25000.0

    realtime = {
        "current_price": price,
        "bid_price": price - 1,
        "ask_price": price + 1,
    }
    effective_stop, effective_take = float(args.stop_loss_points), float(args.take_profit_points)
    entry_price, stop_price, take_price = _entry_plan(
        action,
        realtime,
        effective_stop,
        effective_take,
    )

    if action == "CLOSE_LONG":
        if args.test_exit == "TARGET":
            entry_price = price - effective_take
            stop_price = entry_price - effective_stop
            take_price = price
            event_type = "EXIT_TARGET"
            message = "test long target exit"
            reasons = ["test signal: long target reached", "telegram delivery check"]
        else:
            entry_price = price + effective_stop
            stop_price = price
            take_price = entry_price + effective_take
            event_type = "EXIT_STOP"
            message = "test long stop exit"
            reasons = ["test signal: long stop reached", "telegram delivery check"]
    elif action == "CLOSE_SHORT":
        if args.test_exit == "TARGET":
            entry_price = price + effective_take
            stop_price = entry_price + effective_stop
            take_price = price
            event_type = "EXIT_TARGET"
            message = "test short target exit"
            reasons = ["test signal: short target reached", "telegram delivery check"]
        else:
            entry_price = price - effective_stop
            stop_price = price
            take_price = entry_price - effective_take
            event_type = "EXIT_STOP"
            message = "test short stop exit"
            reasons = ["test signal: short stop reached", "telegram delivery check"]
    elif action == "BUY_LONG":
        event_type = "ENTRY_LONG"
        message = "test formal long candidate"
        reasons = ["測試：15／60 分趨勢同步偏多", "測試：買方量流與 VWAP 確認", "Telegram 傳送檢查"]
    elif action == "SELL_SHORT":
        event_type = "ENTRY_SHORT"
        message = "test formal short candidate"
        reasons = ["測試：15／60 分趨勢同步偏空", "測試：賣方量流與 VWAP 確認", "Telegram 傳送檢查"]
    else:
        raise ValueError(f"unsupported test signal action: {action}")

    signal = {
        "signal_key": f"TEST:{now:%Y%m%d%H%M%S}:{action}:{entry_price:.0f}",
        "contract_code": "TEST-TMF",
        "bar_time": now.strftime("%Y/%m/%d %H:%M:%S"),
        "action": action,
        "score": 70 if action in {"BUY_LONG", "CLOSE_SHORT"} else 30,
        "label": "test signal",
        "feature": "test",
        "price": price,
        "entry_price": entry_price,
        "stop_loss_price": stop_price,
        "take_profit_price": take_price,
        "reasons": reasons,
        "message": message,
        "is_test": True,
        "formal_candidate": {
            "allowed": True,
            "passed": ["15／60 分趨勢同向", "量流資料品質通過", "價格與 VWAP 同向", "風險條件通過"],
            "blocked": [],
        },
    }
    return signal, event_type


def send_test_signal(args):
    action = args.test_signal
    if action not in TEST_SIGNAL_ACTIONS:
        raise ValueError(f"--test-signal must be one of: {', '.join(sorted(TEST_SIGNAL_ACTIONS))}")

    signal, event_type = _build_test_signal(action, args)
    save_signal(signal)
    sent, detail = dispatch_alert(signal, event_type)
    _update_worker_heartbeat("test", f"{event_type} {action}: {detail}")
    print(f"{event_type} {action}: {detail}")
    return 0 if sent and detail in {"telegram sent", "webhook sent", "duplicate alert skipped"} else 1


def send_test_whale_alert(args):
    event = build_test_distribution_event(args.test_price, level=args.test_whale_level)
    event["episode"] = 1
    # Give each explicit test a unique session key without weakening live dedupe.
    event["session_key"] = f"{event['session_key']}:{datetime.now(TAIPEI):%H%M%S}"
    sent, detail = dispatch_whale_distribution(event)
    event_type = event["event_type"]
    _update_worker_heartbeat("test", f"{event_type}: {detail}")
    print(f"{event_type}: {detail}")
    return 0 if sent and detail in {"telegram sent", "webhook sent", "duplicate alert skipped"} else 1


def _setup_whale_monitor(api, args):
    monitor = WhaleFlowMonitor(
        delta_ratio_threshold=args.whale_delta_ratio,
        sell_streak_minutes=args.whale_sell_streak,
        min_tx_volume=args.whale_min_tx_volume,
        level1_delta_ratio_threshold=args.whale_level1_delta_ratio,
        level1_sell_streak_minutes=args.whale_level1_sell_streak,
        level1_min_tx_volume=args.whale_level1_min_tx_volume,
        min_completeness_ratio=args.whale_min_completeness,
        min_classification_ratio=args.whale_min_classification,
        burst_window_minutes=args.whale_burst_window,
        burst_delta_ratio_threshold=args.whale_burst_delta_ratio,
        burst_small_delta_ratio_threshold=args.whale_burst_small_delta_ratio,
        burst_min_tx_volume=args.whale_burst_min_tx_volume,
    )
    monitor.restore_state(load_json_state(WHALE_FLOW_STATE_KEY, {}))
    subscription = sinopac_api.subscribe_futures_order_flow(
        api,
        monitor.on_tick,
        monitor.on_bidask,
        product_roots=("TXF", "MXF", "TMF"),
    )
    for item in subscription.get("subscribed") or []:
        monitor.register_contract(item.get("product_root"), item.get("contract"))
    args._whale_monitor = monitor
    return subscription


def _maybe_send_whale_distribution(args, market_status, realtime, bars, tech_data, persist=True):
    monitor = getattr(args, "_whale_monitor", None)
    if not args.whale_alert or monitor is None or not getattr(market_status, "is_open", False):
        return None

    now = datetime.now(TAIPEI).replace(tzinfo=None)
    flow = monitor.snapshot(now)
    if persist:
        try:
            upsert_order_flow_minutes(monitor.minute_records(last_minutes=3))
        except Exception as exc:
            print(f"order flow persistence warning: {exc}")
    live_realtime = dict(realtime or {})
    if float(flow.get("current_price") or 0) > 0:
        live_realtime["current_price"] = float(flow["current_price"])
    event = build_distribution_event(
        flow,
        live_realtime,
        bars,
        tech_data=tech_data,
        now=now,
    )
    if not event:
        monitor.transition_level(0, now)
        if persist:
            save_json_state(WHALE_FLOW_STATE_KEY, monitor.export_state())
        return None
    event["direction_observation"] = build_direction_observation(
        tech_data,
        flow,
        current_price=live_realtime.get("current_price"),
        session_vwap=flow.get("session_vwap"),
    )
    if not monitor.transition_level(event.get("level"), now):
        if persist:
            save_json_state(WHALE_FLOW_STATE_KEY, monitor.export_state())
        return None
    event["episode"] = monitor.alert_episode()
    monitor.record_event(event)
    save_json_state(WHALE_FLOW_STATE_KEY, monitor.export_state())
    sent, detail = dispatch_whale_distribution(event)
    return {"event": event, "sent": sent, "delivery": detail}


def _wait_with_whale_checks(args):
    remaining = max(0.0, float(args.interval))
    check_interval = max(1.0, float(args.whale_check_interval))
    while remaining > 0:
        step = min(check_interval, remaining)
        time.sleep(step)
        remaining -= step
        context = getattr(args, "_whale_context", None)
        if not args.whale_alert or not context:
            continue
        try:
            _maybe_send_whale_distribution(
                args,
                context["market_status"],
                context["realtime"],
                context["bars"],
                context["tech_data"],
                persist=False,
            )
        except Exception as exc:
            print(f"whale flow quick check error: {exc}")


def evaluate_once(api, args):
    learning_profile = apply_active_parameters(args)
    market_status = get_market_status()
    realtime = sinopac_api.get_realtime_data_from_sinopac(api, product_root=PRODUCT_ROOT)
    raw_kbars, kbars_error = sinopac_api.get_recent_micro_txf_kbars(api, days=args.days)
    if kbars_error:
        _update_worker_heartbeat("warning", kbars_error)

    try:
        maintenance = _maintain_history_and_report(raw_kbars, args)
    except Exception as exc:
        maintenance = {"sync": {}, "report": None, "delivery": ""}
        _update_worker_heartbeat("warning", f"history/research maintenance failed: {exc}")
    if maintenance.get("report"):
        report = maintenance["report"]
        print(
            f"{report.get('report_date')} daily research: {report.get('status')} | "
            f"delivery: {maintenance.get('delivery')}"
        )

    bars = _resample_completed_bars(raw_kbars)
    five_minute_bars = _resample_completed_bars(raw_kbars, rule="5min")
    if bars.empty:
        _update_worker_heartbeat("warning", _heartbeat_detail(market_status, realtime, bars, message="no completed 15m bar"))
        return None

    broker = restore_paper_broker_state(
        PaperBroker(
            multiplier=10,
            commission_per_side=args.commission_per_side,
            slippage_points=args.slippage_points,
        )
    )
    broker.commission_per_side = args.commission_per_side
    broker.slippage_points = args.slippage_points
    worker_state = _reconcile_worker_trade_state(broker, bars)
    tech_data = build_tech_data(bars, realtime)
    args._whale_context = {
        "market_status": market_status,
        "realtime": dict(realtime or {}),
        "bars": bars,
        "tech_data": dict(tech_data or {}),
    }
    whale_result = _maybe_send_whale_distribution(
        args,
        market_status,
        realtime,
        bars,
        tech_data,
    )
    if whale_result:
        print(
            f"whale distribution {whale_result['event'].get('status_key')}: "
            f"{whale_result.get('delivery')}"
        )
    effective_stop_loss_points, effective_take_profit_points = _effective_risk_points(args, tech_data)
    strategy = StrategyManager(
        long_entry_score=args.long_entry_score,
        short_entry_score=args.short_entry_score,
        stop_loss_points=effective_stop_loss_points,
        take_profit_points=effective_take_profit_points,
        score_exit_requires_profit=args.score_exit_requires_profit,
        min_score_exit_profit_points=args.min_score_exit_profit_points,
    )
    strategy.sync_position(
        broker.position,
        broker.entry_price,
        broker.stop_loss_price,
        broker.take_profit_price,
    )

    score, label, reasons, feature = get_decision_score(tech_data, inst_data={}, with_reason=True)
    if learning_profile.get("applied"):
        reasons = list(reasons or [])
        reasons.append(f"受控學習參數：{learning_profile.get('profile')}")
    current_price = float(realtime.get("current_price") or 0)
    breakeven_applied = _apply_breakeven_stop(
        broker,
        worker_state,
        current_price,
        args.breakeven_trigger_r,
        args.breakeven_buffer_points,
    )
    if breakeven_applied:
        strategy.sync_position(
            broker.position,
            broker.entry_price,
            broker.stop_loss_price,
            broker.take_profit_price,
        )
    action, message = strategy.decide_action(score, current_price)
    monitor = getattr(args, "_whale_monitor", None)
    flow_snapshot = (
        monitor.snapshot(datetime.now(TAIPEI).replace(tzinfo=None))
        if monitor is not None
        else {}
    )
    direction_observation = build_direction_observation(
        tech_data,
        flow_snapshot,
        current_price=current_price,
        session_vwap=flow_snapshot.get("session_vwap"),
    )
    formal_candidate = None
    holding_bars = _bars_since(bars, worker_state.get("entry_bar_time"))
    if (
        broker.position > 0
        and args.max_holding_bars
        and holding_bars >= int(args.max_holding_bars)
        and current_price > broker.entry_price
    ):
        action = "CLOSE_LONG"
        message = f"持倉已達 {holding_bars} 根 15 分 K 且仍有獲利，執行逾時平倉。"
    elif (
        broker.position < 0
        and args.max_holding_bars
        and holding_bars >= int(args.max_holding_bars)
        and current_price < broker.entry_price
    ):
        action = "CLOSE_SHORT"
        message = f"持倉已達 {holding_bars} 根 15 分 K 且仍有獲利，執行逾時平倉。"
    hourly_result = _maybe_send_hourly_analysis(
        datetime.now(TAIPEI),
        args,
        market_status,
        realtime,
        bars,
        tech_data,
        score,
        label,
        reasons,
        action,
        message,
        broker,
        effective_stop_loss_points,
        effective_take_profit_points,
    )
    if hourly_result:
        print(
            f"hourly analysis {hourly_result['analysis'].get('hour_key')}: "
            f"{hourly_result.get('delivery')}"
        )
    preopen_result = _maybe_send_preopen_briefing(
        datetime.now(TAIPEI),
        args,
        realtime,
        bars,
        tech_data,
        score,
        label,
        reasons,
        action,
        message,
        broker,
        effective_stop_loss_points,
        effective_take_profit_points,
    )
    if preopen_result:
        print(
            f"preopen briefing {preopen_result['briefing'].get('session_key')}: "
            f"{preopen_result.get('delivery')}"
        )
    if action in {"BUY_LONG", "SELL_SHORT"} and args.require_5m_confirmation:
        confirmation = evaluate_5m_confirmation(
            action,
            five_minute_bars,
            bars["ts"].iloc[-1],
            long_confirm_score=args.five_minute_long_score,
            short_confirm_score=args.five_minute_short_score,
        )
        if not confirmation["confirmed"]:
            detail = " / ".join(confirmation.get("reasons") or [confirmation.get("status")])
            _update_worker_heartbeat(
                "ok",
                _heartbeat_detail(
                    market_status,
                    realtime,
                    bars,
                    score,
                    label,
                    action,
                    f"entry blocked: 5m confirmation failed: {detail}",
                ),
            )
            return None
        reasons = list(reasons or [])
        reasons.append(
            f"5 分確認通過：{confirmation.get('bar_time')}，評分 {confirmation.get('score')}"
        )
    event_type = _event_type(action, message, broker, current_price)
    if action == "BUY_LONG" and not args.allow_long:
        _update_worker_heartbeat("ok", _heartbeat_detail(market_status, realtime, bars, score, label, action, "entry blocked: long side disabled"))
        return None
    if action == "SELL_SHORT" and not args.allow_short:
        _update_worker_heartbeat("ok", _heartbeat_detail(market_status, realtime, bars, score, label, action, "entry blocked: short side disabled"))
        return None

    if action in {"BUY_LONG", "SELL_SHORT"}:
        cooldown_elapsed = _bars_since(bars, worker_state.get("last_exit_bar_time"))
        if worker_state.get("last_exit_bar_time") and cooldown_elapsed <= int(args.cooldown_bars):
            _update_worker_heartbeat(
                "ok",
                _heartbeat_detail(
                    market_status,
                    realtime,
                    bars,
                    score,
                    label,
                    action,
                    f"entry blocked: cooling down {cooldown_elapsed}/{int(args.cooldown_bars)} completed bars",
                ),
            )
            return None
        if not args.require_5m_confirmation and int(args.confirmation_bars) >= 2 and len(bars) >= 2:
            prev_history = bars.iloc[:-1].copy()
            prev_close = float(prev_history["Close"].iloc[-1])
            prev_realtime = {
                "current_price": prev_close,
                "volume": float(prev_history["Volume"].iloc[-1]),
                "vwap": prev_close,
            }
            prev_tech = build_tech_data(prev_history, prev_realtime)
            prev_score, _, _, _ = get_decision_score(prev_tech, inst_data={}, with_reason=True)
            missing_confirmation = (
                action == "BUY_LONG"
                and prev_score < int(args.long_entry_score)
            ) or (
                action == "SELL_SHORT"
                and prev_score > int(args.short_entry_score)
            )
            if missing_confirmation:
                _update_worker_heartbeat(
                    "ok",
                    _heartbeat_detail(
                        market_status,
                        realtime,
                        bars,
                        score,
                        label,
                        action,
                        f"entry blocked: previous score {prev_score} did not confirm",
                    ),
                )
                return None

        entry_price, stop_price, take_price = _entry_plan(
            action,
            realtime,
            float(effective_stop_loss_points),
            float(effective_take_profit_points),
        )
        risk = evaluate_entry_risk(
            action,
            broker,
            market_status,
            min_reward_risk_ratio=args.min_entry_rr,
            entry_price=entry_price,
            stop_loss_price=stop_price,
            take_profit_price=take_price,
            nearest_resistance=tech_data.get("上方壓力") or 0,
            nearest_support=tech_data.get("下方支撐") or 0,
            tech_data=tech_data,
            reject_choppy=args.reject_choppy,
            require_60m_alignment=args.require_60m_alignment,
            min_adx=args.min_adx,
            min_volume_ratio=args.min_volume_ratio,
            max_chase_atr=args.max_chase_atr,
        )
        if not risk.allowed:
            _update_worker_heartbeat(
                "ok",
                _heartbeat_detail(
                    market_status,
                    realtime,
                    bars,
                    score,
                    label,
                    action,
                    f"entry blocked: {' / '.join(risk.reasons)}",
                ),
            )
            return None

        formal_candidate = evaluate_formal_candidate(
            action,
            tech_data,
            flow_snapshot,
            current_price=current_price,
            session_vwap=flow_snapshot.get("session_vwap"),
        )
        if not formal_candidate["allowed"]:
            _update_worker_heartbeat(
                "ok",
                _heartbeat_detail(
                    market_status,
                    realtime,
                    bars,
                    score,
                    label,
                    action,
                    f"formal candidate blocked: {' / '.join(formal_candidate['blocked'])}",
                ),
            )
            return None
        reasons = list(reasons or [])
        reasons.extend(formal_candidate["passed"][:3])

    if action == "HOLD":
        _update_worker_heartbeat("ok", _heartbeat_detail(market_status, realtime, bars, score, label, action, message))
        return None

    entry_price, stop_price, take_price = _entry_plan(
        action,
        realtime,
        float(effective_stop_loss_points),
        float(effective_take_profit_points),
    )
    if action in {"CLOSE_LONG", "CLOSE_SHORT"}:
        entry_price = broker.entry_price or current_price
        stop_price = broker.stop_loss_price
        take_price = broker.take_profit_price

    bar_time = pd.to_datetime(bars["ts"].iloc[-1]).strftime("%Y/%m/%d %H:%M")
    contract_code = realtime.get("contract_code") or bars.attrs.get("contract_code", "")
    signal = {
        "signal_key": f"{contract_code}:{bar_time}:{action}:{entry_price:.0f}",
        "contract_code": contract_code,
        "bar_time": bar_time,
        "action": action,
        "score": score,
        "label": label,
        "feature": feature,
        "price": current_price,
        "entry_price": entry_price,
        "stop_loss_price": stop_price,
        "take_profit_price": take_price,
        "reasons": reasons,
        "message": message,
        "direction_observation": direction_observation,
    }
    if formal_candidate is not None:
        signal["formal_candidate"] = formal_candidate
    save_signal(signal)
    sent, detail = dispatch_alert(signal, event_type)
    tracking_detail = ""
    delivered = detail in {"telegram sent", "webhook sent"}
    if sent and delivered:
        tracked, tracking_detail = _sync_paper_tracking(
            broker,
            action,
            current_price,
            entry_price,
            stop_price,
            take_price,
            message,
            args,
        )
        tracking_detail = f" | paper: {tracking_detail}" if tracking_detail else ""
        if tracked:
            _record_worker_fill_state(worker_state, broker, action, bar_time, stop_price)
            strategy.apply_fill(
                action,
                entry_price,
                args.paper_quantity,
                stop_price,
                take_price,
            )
    elif sent:
        tracking_detail = " | paper: alert not delivered, tracking skipped"

    _update_worker_heartbeat(
        "ok",
        _heartbeat_detail(market_status, realtime, bars, score, label, action, f"{event_type}: {detail}{tracking_detail}"),
    )
    return signal


def run_worker(args):
    if args.test_signal:
        return send_test_signal(args)
    if args.test_whale_alert:
        return send_test_whale_alert(args)

    api, api_error = sinopac_api.get_api(simulation=args.simulation)
    if api_error:
        _update_worker_heartbeat("error", api_error)
        print(api_error)
        return 1

    if args.whale_alert:
        subscription = _setup_whale_monitor(api, args)
        subscribed = subscription.get("subscribed") or []
        errors = subscription.get("errors") or []
        print(
            "whale flow subscriptions: "
            + (", ".join(item.get("product_root", "") for item in subscribed) or "none")
        )
        for error in errors:
            print(error)
        if not subscribed:
            _update_worker_heartbeat(
                "warning",
                "大戶量流監控未訂閱成功；策略 Worker 繼續執行，但不會發倒貨警報。",
            )

    while True:
        try:
            signal = evaluate_once(api, args)
            if signal:
                print(f"{datetime.now(TAIPEI):%Y-%m-%d %H:%M:%S} {signal['action']} {signal['score']}")
        except Exception as exc:
            _update_worker_heartbeat("error", str(exc))
            print(f"signal_worker error: {exc}")

        if args.once:
            return 0
        _wait_with_whale_checks(args)


def parse_args():
    parser = argparse.ArgumentParser(description="Background signal worker for TMF strategy alerts")
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    parser.add_argument("--interval", type=int, default=30, help="Polling interval in seconds, default 30")
    parser.add_argument("--days", type=int, default=90, help="Recent kbar history days, default 90")
    parser.add_argument("--simulation", action=argparse.BooleanOptionalAction, default=sinopac_api.get_simulation_default())
    parser.add_argument("--long-entry-score", type=int, default=62)
    parser.add_argument("--short-entry-score", type=int, default=35)
    parser.add_argument("--stop-loss-points", type=float, default=50)
    parser.add_argument("--take-profit-points", type=float, default=100)
    parser.add_argument("--adaptive-risk", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--atr-stop-multiplier", type=float, default=1.2)
    parser.add_argument("--reward-risk-ratio", type=float, default=2.2)
    parser.add_argument("--min-entry-rr", type=float, default=1.5)
    parser.add_argument("--reject-choppy", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-60m-alignment", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-adx", type=float, default=22)
    parser.add_argument("--min-volume-ratio", type=float, default=1.0)
    parser.add_argument("--max-chase-atr", type=float, default=1.0)
    parser.add_argument("--confirmation-bars", type=int, default=2)
    parser.add_argument("--require-5m-confirmation", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--five-minute-long-score", type=int, default=50)
    parser.add_argument("--five-minute-short-score", type=int, default=50)
    parser.add_argument("--cooldown-bars", type=int, default=2)
    parser.add_argument("--breakeven-trigger-r", type=float, default=1.0)
    parser.add_argument("--breakeven-buffer-points", type=float, default=0)
    parser.add_argument("--max-holding-bars", type=int, default=24)
    parser.add_argument("--history-sync", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--daily-close-report", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--daily-report-hour", type=int, default=13)
    parser.add_argument("--daily-report-minute", type=int, default=50)
    parser.add_argument("--research-history-days", type=int, default=730)
    parser.add_argument("--research-folds", type=int, default=3)
    parser.add_argument("--min-reference-trades", type=int, default=100)
    parser.add_argument("--min-oos-trades", type=int, default=30)
    parser.add_argument("--adaptive-learning", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--preopen-briefing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--night-preopen-briefing", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--hourly-report", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--day-preopen-hour", type=int, default=8)
    parser.add_argument("--day-preopen-minute", type=int, default=35)
    parser.add_argument("--night-preopen-hour", type=int, default=14)
    parser.add_argument("--night-preopen-minute", type=int, default=50)
    parser.add_argument("--allow-long", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-short", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--score-exit-requires-profit", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-score-exit-profit-points", type=float, default=0)
    parser.add_argument("--paper-quantity", type=int, default=1)
    parser.add_argument("--commission-per-side", type=float, default=20.0)
    parser.add_argument("--slippage-points", type=float, default=2.0)
    parser.add_argument("--auto-paper-fill", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--whale-alert", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--whale-delta-ratio", type=float, default=-0.08)
    parser.add_argument("--whale-sell-streak", type=int, default=4)
    parser.add_argument("--whale-min-tx-volume", type=int, default=100)
    parser.add_argument("--whale-check-interval", type=float, default=2.0)
    parser.add_argument("--whale-level1-delta-ratio", type=float, default=-0.04)
    parser.add_argument("--whale-level1-sell-streak", type=int, default=2)
    parser.add_argument("--whale-level1-min-tx-volume", type=int, default=50)
    parser.add_argument("--whale-min-completeness", type=float, default=0.95)
    parser.add_argument("--whale-min-classification", type=float, default=0.80)
    parser.add_argument("--whale-burst-window", type=int, default=3)
    parser.add_argument("--whale-burst-delta-ratio", type=float, default=-0.12)
    parser.add_argument("--whale-burst-small-delta-ratio", type=float, default=-0.08)
    parser.add_argument("--whale-burst-min-tx-volume", type=float, default=300)
    parser.add_argument("--test-whale-alert", action="store_true", help="Send a synthetic whale-distribution alert")
    parser.add_argument("--test-whale-level", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--test-signal", choices=sorted(TEST_SIGNAL_ACTIONS), help="Send a test strategy alert")
    parser.add_argument("--test-exit", choices=sorted(TEST_EXIT_EVENTS), default="STOP", help="Exit event for close test signals")
    parser.add_argument("--test-price", type=float, default=25000.0, help="Reference price for --test-signal")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run_worker(parse_args()))

