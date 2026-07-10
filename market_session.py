from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass
class MarketStatus:
    is_open: bool
    session: str
    label: str
    next_open: datetime | None
    allow_new_entry: bool


def _at(now, hour, minute):
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _next_monday_day_open(now):
    days_ahead = (7 - now.weekday()) % 7
    days_ahead = 7 if days_ahead == 0 else days_ahead
    return _at(now + timedelta(days=days_ahead), 8, 45)


def get_market_status(now=None):
    now = now or datetime.now(TAIPEI)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TAIPEI)
    else:
        now = now.astimezone(TAIPEI)

    weekday = now.weekday()
    current = now.time()

    # Friday night session runs into early Saturday. Other weekend periods are
    # treated conservatively as closed until an official TAIFEX calendar exists.
    if weekday == 5:
        if current <= time(5, 0):
            return MarketStatus(True, "night", "夜盤交易中", None, True)
        return MarketStatus(False, "closed", "週六休市", _next_monday_day_open(now), False)

    if weekday == 6:
        return MarketStatus(False, "closed", "週日休市", _next_monday_day_open(now), False)

    if current <= time(5, 0):
        if weekday == 0:
            return MarketStatus(False, "closed", "休市", _at(now, 8, 45), False)
        return MarketStatus(True, "night", "夜盤交易中", None, True)

    if current < time(8, 45):
        return MarketStatus(False, "pre_open", "開盤前", _at(now, 8, 45), False)

    if time(8, 45) <= current <= time(13, 45):
        return MarketStatus(True, "day", "日盤交易中", None, True)

    if current < time(15, 0):
        return MarketStatus(False, "break", "盤間休息", _at(now, 15, 0), False)

    return MarketStatus(True, "night", "夜盤交易中", None, True)


def format_datetime(value, fallback="無資料"):
    if value is None:
        return fallback
    if value.tzinfo is not None:
        value = value.astimezone(TAIPEI)
    return value.strftime("%Y/%m/%d %H:%M")
