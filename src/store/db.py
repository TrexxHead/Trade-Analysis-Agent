import json
import sqlite3
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

CREATE INDEX IF NOT EXISTS idx_trades_close_time ON trades(close_time);
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
            json.dumps(t.get("raw", {})),
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
