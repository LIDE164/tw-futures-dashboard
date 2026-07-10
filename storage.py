import json
import sqlite3
from pathlib import Path

from paper_broker import PaperTrade


DB_PATH = Path("data/trading.db")


def _connect(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return conn


def load_json_state(key, default=None):
    default = default if default is not None else {}
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return default


def save_json_state(key, value):
    payload = json.dumps(value, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, payload),
        )


def load_paper_broker_state():
    return load_json_state("paper_broker", {})


def save_paper_broker_state(broker):
    save_json_state(
        "paper_broker",
        {
            "position": broker.position,
            "entry_price": broker.entry_price,
            "stop_loss_price": broker.stop_loss_price,
            "take_profit_price": broker.take_profit_price,
            "realized_pnl": broker.realized_pnl,
            "trades": [trade.__dict__ for trade in broker.trades],
        },
    )


def restore_paper_broker_state(broker):
    state = load_paper_broker_state()
    if not state:
        return broker

    broker.position = int(state.get("position") or 0)
    broker.entry_price = float(state.get("entry_price") or 0)
    broker.stop_loss_price = float(state.get("stop_loss_price") or 0)
    broker.take_profit_price = float(state.get("take_profit_price") or 0)
    broker.realized_pnl = float(state.get("realized_pnl") or 0)
    broker.trades = [
        PaperTrade(
            time=item.get("time", ""),
            action=item.get("action", ""),
            price=float(item.get("price") or 0),
            quantity=int(item.get("quantity") or 0),
            pnl=float(item.get("pnl") or 0),
            note=item.get("note", ""),
            stop_loss_price=float(item.get("stop_loss_price") or 0),
            take_profit_price=float(item.get("take_profit_price") or 0),
        )
        for item in state.get("trades", [])
    ]
    return broker


def clear_paper_broker_state():
    with _connect() as conn:
        conn.execute("DELETE FROM app_state WHERE key = ?", ("paper_broker",))
