import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "trades.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    volume REAL,
    open_time TEXT NOT NULL,
    close_time TEXT NOT NULL,
    open_price REAL,
    close_price REAL,
    pnl REAL NOT NULL,
    balance_after REAL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS trade_flags (
    trade_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    detail TEXT,
    PRIMARY KEY (trade_id, rule_id),
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS trade_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    platform TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    order_spec_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT,
    decision_note TEXT,
    executed_order_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_close_time ON trades(close_time);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON trade_proposals(status);
"""


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_trades(conn: sqlite3.Connection, trades: list[dict]) -> int:
    rows = [
        (
            t["id"],
            t["source"],
            t["account_id"],
            t["symbol"],
            t["direction"],
            t.get("volume"),
            t["open_time"],
            t["close_time"],
            t.get("open_price"),
            t.get("close_price"),
            t["pnl"],
            t.get("balance_after"),
            json.dumps(t.get("raw", {}), default=str),
        )
        for t in trades
    ]
    conn.executemany(
        """
        INSERT INTO trades (id, source, account_id, symbol, direction, volume,
                             open_time, close_time, open_price, close_price,
                             pnl, balance_after, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            pnl=excluded.pnl,
            balance_after=excluded.balance_after,
            close_time=excluded.close_time,
            close_price=excluded.close_price,
            raw_json=excluded.raw_json
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def get_trades(conn: sqlite3.Connection, start: str | None = None, end: str | None = None,
               source: str | None = None) -> list[dict]:
    query = "SELECT * FROM trades WHERE 1=1"
    params: list = []
    if start:
        query += " AND close_time >= ?"
        params.append(start)
    if end:
        query += " AND close_time < ?"
        params.append(end)
    if source:
        query += " AND source = ?"
        params.append(source)
    query += " ORDER BY close_time ASC"
    return [dict(row) for row in conn.execute(query, params)]


def set_trade_flags(conn: sqlite3.Connection, trade_id: str, flags: list[dict]) -> None:
    conn.execute("DELETE FROM trade_flags WHERE trade_id = ?", (trade_id,))
    conn.executemany(
        "INSERT INTO trade_flags (trade_id, rule_id, detail) VALUES (?, ?, ?)",
        [(trade_id, f["rule_id"], f.get("detail", "")) for f in flags],
    )
    conn.commit()


def get_flags(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> list[dict]:
    query = """
        SELECT trade_flags.*, trades.close_time, trades.source, trades.symbol
        FROM trade_flags
        JOIN trades ON trades.id = trade_flags.trade_id
        WHERE 1=1
    """
    params: list = []
    if start:
        query += " AND trades.close_time >= ?"
        params.append(start)
    if end:
        query += " AND trades.close_time < ?"
        params.append(end)
    return [dict(row) for row in conn.execute(query, params)]


def create_proposal(conn: sqlite3.Connection, platform: str, symbol: str, direction: str,
                     order_spec: dict, rationale: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO trade_proposals (created_at, platform, symbol, direction, order_spec_json, rationale)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (datetime.now(timezone.utc).isoformat(), platform, symbol, direction, json.dumps(order_spec), rationale),
    )
    conn.commit()
    return cursor.lastrowid


def has_pending_proposal(conn: sqlite3.Connection, symbol: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM trade_proposals WHERE symbol = ? AND status = 'pending' LIMIT 1", (symbol,)
    ).fetchone()
    return row is not None


def get_proposal(conn: sqlite3.Connection, proposal_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM trade_proposals WHERE id = ?", (proposal_id,)).fetchone()
    return dict(row) if row else None


def list_proposals(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM trade_proposals WHERE status = ? ORDER BY created_at ASC", (status,)
        )
    else:
        rows = conn.execute("SELECT * FROM trade_proposals ORDER BY created_at ASC")
    return [dict(row) for row in rows]


def update_proposal_status(conn: sqlite3.Connection, proposal_id: int, status: str,
                            note: str | None = None, executed_order_id: str | None = None) -> None:
    conn.execute(
        """
        UPDATE trade_proposals
        SET status = ?, decided_at = ?, decision_note = ?, executed_order_id = COALESCE(?, executed_order_id)
        WHERE id = ?
        """,
        (status, datetime.now(timezone.utc).isoformat(), note, executed_order_id, proposal_id),
    )
    conn.commit()
