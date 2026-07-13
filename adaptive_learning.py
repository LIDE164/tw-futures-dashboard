import hashlib
import json
from copy import deepcopy

from storage import load_json_state, save_json_state


LEARNING_STATE_KEY = "strategy_parameter_learning"
MIN_FULL_TRADES = 100
MIN_OOS_TRADES = 30
MIN_OOS_EXPECTANCY = 200.0
MIN_OOS_PROFIT_FACTOR = 1.2
REQUIRED_CONFIRMATIONS = 3
ROLLBACK_FAILURES = 3
MIN_EXPECTANCY_UPLIFT = 0.10

# The learner can tune only these bounded strategy parameters.
PARAMETER_BOUNDS = {
    "long_entry_score": (55, 75, int),
    "short_entry_score": (25, 45, int),
    "min_entry_rr": (1.2, 2.5, float),
    "atr_stop_multiplier": (0.8, 2.0, float),
    "reward_risk_ratio": (1.3, 3.0, float),
    "min_adx": (15, 35, float),
    "min_volume_ratio": (0.6, 1.8, float),
    "max_chase_atr": (0.5, 2.0, float),
    "confirmation_bars": (1, 3, int),
    "cooldown_bars": (0, 6, int),
    "allow_long": (False, True, bool),
    "allow_short": (False, True, bool),
    "breakeven_trigger_r": (0.0, 2.5, float),
    "breakeven_buffer_points": (0, 30, float),
    "max_holding_bars": (0, 96, int),
}


def _clean_parameters(parameters):
    cleaned = {}
    for name, value in dict(parameters or {}).items():
        if name not in PARAMETER_BOUNDS or value is None:
            continue
        lower, upper, converter = PARAMETER_BOUNDS[name]
        if converter is bool:
            cleaned[name] = bool(value)
            continue
        try:
            converted = converter(value)
        except (TypeError, ValueError):
            continue
        cleaned[name] = max(lower, min(upper, converted))
    return cleaned


def _signature(parameters):
    payload = json.dumps(_clean_parameters(parameters), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_learning_state():
    state = load_json_state(LEARNING_STATE_KEY, {})
    return state if isinstance(state, dict) else {}


def get_active_parameters():
    state = load_learning_state()
    if not state.get("auto_managed"):
        return {}
    return _clean_parameters(state.get("active_parameters"))


def apply_active_parameters(args):
    if not bool(getattr(args, "adaptive_learning", True)):
        return {"applied": False, "profile": "manual", "parameters": {}}
    parameters = get_active_parameters()
    for name, value in parameters.items():
        if hasattr(args, name):
            setattr(args, name, value)
    state = load_learning_state()
    return {
        "applied": bool(parameters),
        "profile": state.get("active_profile") or "formal-15m",
        "parameters": parameters,
        "promoted_at": state.get("promoted_at"),
    }


def _rollback_if_needed(state, report_date, walk):
    if not state.get("auto_managed") or state.get("last_health_date") == report_date:
        return None
    oos_trades = int(walk.get("oos_trades") or 0)
    expectancy = float(walk.get("weighted_expectancy") or 0)
    profit_factor = float(walk.get("median_profit_factor") or 0)
    if oos_trades < MIN_OOS_TRADES:
        return None

    state["last_health_date"] = report_date
    degraded = expectancy <= 0 or profit_factor < 1.0
    state["health_failures"] = int(state.get("health_failures") or 0) + 1 if degraded else 0
    if state["health_failures"] < ROLLBACK_FAILURES or not state.get("previous_parameters"):
        return None

    failed_profile = state.get("active_profile") or "learned"
    state["active_parameters"] = deepcopy(state["previous_parameters"])
    state["active_profile"] = state.get("previous_profile") or "formal-15m"
    state["previous_parameters"] = {}
    state["previous_profile"] = ""
    state["health_failures"] = 0
    state["last_rollback_date"] = report_date
    state["last_rollback_from"] = failed_profile
    return f"已連續 {ROLLBACK_FAILURES} 次樣本外退化，自動回退至 {state['active_profile']}。"


def process_research_learning(report, default_parameters=None, allow_auto_apply=True):
    report = dict(report or {})
    report_date = str(report.get("report_date") or "")
    learning = dict(report.get("learning") or {})
    candidate = dict(report.get("candidate") or {})
    walk = dict(report.get("walk_forward") or {})
    backtest = dict(report.get("backtest") or {})
    state = load_learning_state()

    rollback_message = _rollback_if_needed(state, report_date, walk)
    candidate_parameters = _clean_parameters(candidate.get("parameters"))
    full_trades = int(backtest.get("交易次數") or 0)
    oos_trades = int(walk.get("oos_trades") or 0)
    candidate_oos_trades = int(candidate.get("oos_trades") or 0)
    oos_expectancy = float(candidate.get("oos_expectancy") or 0)
    oos_profit_factor = float(candidate.get("oos_profit_factor") or 0)
    active_benchmark = max(
        float(state.get("active_oos_expectancy") or 0),
        float(walk.get("weighted_expectancy") or 0),
    )
    required_expectancy = max(
        MIN_OOS_EXPECTANCY,
        active_benchmark * (1.0 + MIN_EXPECTANCY_UPLIFT),
    )
    positive_ratio = float(walk.get("positive_folds") or 0) / max(
        float(walk.get("folds") or 0), 1.0
    )
    eligible = bool(
        allow_auto_apply
        and candidate_parameters
        and full_trades >= MIN_FULL_TRADES
        and oos_trades >= MIN_OOS_TRADES
        and candidate_oos_trades >= MIN_OOS_TRADES
        and bool(learning.get("stable_folds"))
        and positive_ratio >= 0.6
        and oos_expectancy >= required_expectancy
        and oos_profit_factor >= MIN_OOS_PROFIT_FACTOR
    )

    signature = _signature(candidate_parameters) if candidate_parameters else ""
    already_processed = state.get("last_candidate_date") == report_date
    if candidate_parameters and not already_processed:
        state["last_candidate_date"] = report_date
        if eligible and signature == state.get("candidate_signature"):
            state["candidate_confirmations"] = int(state.get("candidate_confirmations") or 0) + 1
        elif eligible:
            state["candidate_signature"] = signature
            state["candidate_confirmations"] = 1
        else:
            state["candidate_signature"] = signature
            state["candidate_confirmations"] = 0
        state["candidate_parameters"] = candidate_parameters
        state["candidate_profile"] = candidate.get("profile") or "weekly-challenger"
        state["candidate_metrics"] = {
            "full_trades": full_trades,
            "oos_trades": oos_trades,
            "candidate_oos_trades": candidate_oos_trades,
            "oos_expectancy": oos_expectancy,
            "oos_profit_factor": oos_profit_factor,
            "positive_fold_ratio": round(positive_ratio, 3),
        }

    confirmations = int(state.get("candidate_confirmations") or 0)
    promoted = bool(
        eligible
        and not already_processed
        and confirmations >= REQUIRED_CONFIRMATIONS
        and signature != state.get("active_signature")
    )
    if promoted:
        state["previous_parameters"] = deepcopy(
            state.get("active_parameters") or _clean_parameters(default_parameters)
        )
        state["previous_profile"] = state.get("active_profile") or "formal-15m"
        state["active_parameters"] = candidate_parameters
        state["active_profile"] = candidate.get("profile") or "weekly-challenger"
        state["active_signature"] = signature
        state["active_oos_expectancy"] = oos_expectancy
        state["active_oos_profit_factor"] = oos_profit_factor
        state["auto_managed"] = True
        state["promoted_at"] = report_date
        state["health_failures"] = 0
        state["candidate_confirmations"] = 0

    if rollback_message:
        reason = rollback_message
    elif promoted:
        reason = (
            f"挑戰者已連續 {REQUIRED_CONFIRMATIONS} 次通過樣本外門檻，"
            f"自動晉級為 {state['active_profile']}；下一輪 Worker 起生效。"
        )
    elif eligible:
        reason = (
            f"挑戰者通過本次門檻，觀察進度 {confirmations}/{REQUIRED_CONFIRMATIONS}；"
            "尚未改動正式警報參數。"
        )
    elif candidate_parameters:
        reason = (
            f"挑戰者未通過自動晉級：完整樣本 {full_trades}/{MIN_FULL_TRADES}、"
            f"Walk-forward {oos_trades}/{MIN_OOS_TRADES}、候選樣本外 "
            f"{candidate_oos_trades}/{MIN_OOS_TRADES}、期望值 {oos_expectancy:,.0f}"
            f"（門檻 {required_expectancy:,.0f}）、PF {oos_profit_factor:.2f}。"
        )
    else:
        reason = learning.get("reason") or "本日沒有新的候選參數，維持正式 15 分策略。"

    state["updated_at"] = report.get("created_at") or report_date
    save_json_state(LEARNING_STATE_KEY, state)
    return {
        **learning,
        "auto_apply": bool(promoted),
        "auto_learning_enabled": bool(allow_auto_apply),
        "active_profile": state.get("active_profile") or "formal-15m",
        "auto_managed": bool(state.get("auto_managed")),
        "candidate_confirmations": confirmations,
        "required_confirmations": REQUIRED_CONFIRMATIONS,
        "required_expectancy": round(required_expectancy, 2),
        "reason": reason,
    }
