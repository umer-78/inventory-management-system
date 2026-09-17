"""Schema and connection handling.

Stock is **never** stored as a single mutable number that code updates in place.
Every change is a row in `stock_movements`, and the quantity on hand is the sum
of those rows. That way the history explains the number, a mistake can be
corrected with a compensating movement, and two concurrent sales cannot quietly
overwrite each other.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS suppliers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    email     TEXT NOT NULL DEFAULT '',
    phone     TEXT NOT NULL DEFAULT '',
    lead_time_days INTEGER NOT NULL DEFAULT 7 CHECK (lead_time_days >= 0)
);

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sku          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'general',
    supplier_id  INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
    cost_price   REAL NOT NULL CHECK (cost_price >= 0),
    sell_price   REAL NOT NULL CHECK (sell_price >= 0),
    reorder_point INTEGER NOT NULL DEFAULT 0 CHECK (reorder_point >= 0),
    reorder_quantity INTEGER NOT NULL DEFAULT 0 CHECK (reorder_quantity >= 0),
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stock_movements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity   INTEGER NOT NULL CHECK (quantity != 0),   -- positive in, negative out
    reason     TEXT NOT NULL CHECK (reason IN ('purchase', 'sale', 'return', 'adjustment', 'damage', 'transfer')),
    unit_cost  REAL,
    reference  TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    status      TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'placed', 'received', 'cancelled')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    placed_at   TEXT,
    received_at TEXT,
    note        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS purchase_order_lines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    unit_cost  REAL NOT NULL CHECK (unit_cost >= 0),
    UNIQUE (order_id, product_id)
);

CREATE INDEX IF NOT EXISTS movements_product_idx ON stock_movements(product_id, created_at);
CREATE INDEX IF NOT EXISTS products_supplier_idx ON products(supplier_id);
CREATE INDEX IF NOT EXISTS po_status_idx ON purchase_orders(status);

CREATE VIEW IF NOT EXISTS stock_levels AS
SELECT p.id AS product_id, p.sku, p.name, p.category, p.supplier_id,
       p.cost_price, p.sell_price, p.reorder_point, p.reorder_quantity, p.active,
       COALESCE(SUM(m.quantity), 0) AS on_hand
FROM products p LEFT JOIN stock_movements m ON m.product_id = p.id
GROUP BY p.id;
"""


class Database:
    def __init__(self, path: str | Path = "inventory.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._memory = sqlite3.connect(self.path) if self.path == ":memory:" else None
        if self._memory:
            self._memory.row_factory = sqlite3.Row
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        if self._memory:
            return self._memory
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def transaction(self):
        """All-or-nothing: receiving a purchase order writes many rows or none."""
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            if conn is not self._memory:
                conn.close()

    def migrate(self) -> None:
        with self.transaction() as conn:
            conn.executescript(SCHEMA)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        conn = self.connect()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            if conn is not self._memory:
                conn.close()

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self.transaction() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid or cur.rowcount
