from datetime import datetime, timedelta

import pandas as pd

from storage import load_json_state, save_json_state


STATE_KEY = "preopen_forecast_learning_v1"
CLASSES = ("bull", "range", "bear")


def _normalise(probabilities):
    total = sum(max(0.0, float(probabilities.get(name, 0))) for name in CLASSES) or 1.0
    raw = {name: max(0.0, float(probabilities.get(name, 0))) / total * 100 for name in CLASSES}
    rounded = {name: int(round(value)) for name, value in raw.items()}
    rounded[max(raw, key=raw.get)] += 100 - sum(rounded.values())
    return rounded


def calibrate_with_actual_forecasts(model):
    model = dict(model or {})
    raw = dict(model.get("probabilities") or {})
    state = load_json_state(STATE_KEY, {"forecasts": []})
    evaluated = [item for item in state.get("forecasts", []) if item.get("actual") in CLASSES]
    if not evaluated:
        model["forecast_tracking_count"] = 0
        model["forecast_tracking_accuracy"] = None
        return model

    correct = sum(item.get("predicted") == item.get("actual") for item in evaluated)
    reliability = {}
    actual_counts = {name: 1 for name in CLASSES}
    for name in CLASSES:
        predicted = [item for item in evaluated if item.get("predicted") == name]
        hits = sum(item.get("actual") == name for item in predicted)
        reliability[name] = (hits + 1) / (len(predicted) + 2)
    for item in evaluated:
        actual_counts[item["actual"]] += 1

    empirical_total = sum(actual_counts.values())
    weighted = {}
    for name in CLASSES:
        empirical = actual_counts[name] / empirical_total * 100
        adjusted = float(raw.get(name, 0)) * (0.55 + reliability[name])
        weighted[name] = adjusted * 0.82 + empirical * 0.18
    last = evaluated[-1]
    if last.get("predicted") != last.get("actual") and last.get("predicted") in weighted:
        weighted[last["predicted"]] *= 0.88

    model["raw_probabilities"] = raw
    model["probabilities"] = _normalise(weighted)
    model["forecast_tracking_count"] = len(evaluated)
    model["forecast_tracking_accuracy"] = round(correct / len(evaluated) * 100, 1)
    model["last_forecast_result"] = {
        "session_key": last.get("session_key"),
        "predicted": last.get("predicted"),
        "actual": last.get("actual"),
        "correct": last.get("predicted") == last.get("actual"),
    }
    return model


def evaluate_pending_forecasts(bars, now=None):
    if bars is None or bars.empty or "ts" not in bars.columns:
        return 0
    now = now or datetime.now()
    state = load_json_state(STATE_KEY, {"forecasts": []})
    forecasts = list(state.get("forecasts") or [])
    frame = bars.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce")
    frame = frame.dropna(subset=["ts"]).sort_values("ts")
    updated = 0
    for item in forecasts:
        if item.get("actual") in CLASSES:
            continue
        try:
            date_text, session = item["session_key"].split(":", 1)
            date = datetime.fromisoformat(date_text)
        except (KeyError, ValueError):
            continue
        if session == "day":
            start = date.replace(hour=8, minute=45)
            end = date.replace(hour=13, minute=46)
        else:
            start = date.replace(hour=15, minute=0)
            end = (date + timedelta(days=1)).replace(hour=5, minute=1)
        if now.replace(tzinfo=None) < end:
            continue
        outcome = frame[(frame["ts"] >= start) & (frame["ts"] < end)]
        if len(outcome) < 4:
            continue
        reference = float(item.get("reference_price") or 0)
        threshold = max(15.0, float(item.get("threshold") or 15))
        move = float(outcome["Close"].iloc[-1]) - reference
        item["actual"] = "bull" if move >= threshold else "bear" if move <= -threshold else "range"
        item["actual_move"] = round(move, 1)
        item["evaluated_at"] = now.isoformat(timespec="seconds")
        updated += 1
    if updated:
        save_json_state(STATE_KEY, {"forecasts": forecasts[-180:]})
    return updated


def record_preopen_forecast(briefing):
    model = briefing.get("scenario_model") or {}
    probabilities = model.get("probabilities") or {}
    if not probabilities or not briefing.get("session_key"):
        return
    state = load_json_state(STATE_KEY, {"forecasts": []})
    forecasts = [
        item for item in state.get("forecasts", [])
        if item.get("session_key") != briefing.get("session_key")
    ]
    forecasts.append(
        {
            "session_key": briefing.get("session_key"),
            "issued_at": datetime.now().isoformat(timespec="seconds"),
            "predicted": max(probabilities, key=probabilities.get),
            "probabilities": probabilities,
            "reference_price": float(briefing.get("last_price") or 0),
            "threshold": float(model.get("decision_threshold") or 15),
            "actual": None,
        }
    )
    save_json_state(STATE_KEY, {"forecasts": forecasts[-180:]})
