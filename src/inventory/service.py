"""Business rules on top of the tables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .db import Database


class NotFound(Exception):
    pass


class InsufficientStock(Exception):
    def __init__(self, sku: str, requested: int, available: int):
        super().__init__(f"{sku}: asked for {requested}, only {available} on hand")
        self.sku, self.requested, self.available = sku, requested, available


@dataclass
class Inventory:
    db: Database

    # ------------------------------------------------------------- catalogue
    def add_supplier(self, name: str, email: str = "", phone: str = "", lead_time_days: int = 7) -> dict:
        supplier_id = self.db.execute(
            "INSERT INTO suppliers (name, email, phone, lead_time_days) VALUES (?, ?, ?, ?)",
            (name.strip(), email, phone, lead_time_days))
        return self.db.one("SELECT * FROM suppliers WHERE id = ?", (supplier_id,))

    def add_product(self, sku: str, name: str, cost_price: float, sell_price: float, *,
                    category: str = "general", supplier_id: int | None = None,
                    reorder_point: int = 0, reorder_quantity: int = 0) -> dict:
        product_id = self.db.execute(
            """INSERT INTO products (sku, name, category, supplier_id, cost_price, sell_price,
                                     reorder_point, reorder_quantity)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sku.strip().upper(), name.strip(), category, supplier_id, cost_price, sell_price,
             reorder_point, reorder_quantity))
        return self.product(product_id)

    def product(self, product_id: int) -> dict:
        row = self.db.one("SELECT * FROM stock_levels WHERE product_id = ?", (product_id,))
        if not row:
            raise NotFound(f"product {product_id} not found")
        return row

    def product_by_sku(self, sku: str) -> dict:
        row = self.db.one("SELECT * FROM stock_levels WHERE sku = ?", (sku.strip().upper(),))
        if not row:
            raise NotFound(f"sku {sku} not found")
        return row

    def products(self, *, category: str | None = None, low_stock: bool = False,
                 search: str | None = None, include_inactive: bool = False) -> list[dict]:
        sql = ["SELECT * FROM stock_levels WHERE 1 = 1"]
        params: list = []
        if not include_inactive:
            sql.append("AND active = 1")
        if category:
            sql.append("AND category = ?")
            params.append(category)
        if search:
            sql.append("AND (name LIKE ? OR sku LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        if low_stock:
            sql.append("AND on_hand <= reorder_point")
        return self.db.query(" ".join(sql) + " ORDER BY sku", tuple(params))

    # ----------------------------------------------------------------- stock
    def on_hand(self, product_id: int) -> int:
        row = self.db.one(
            "SELECT COALESCE(SUM(quantity), 0) AS n FROM stock_movements WHERE product_id = ?",
            (product_id,))
        return int(row["n"])

    def move(self, product_id: int, quantity: int, reason: str, *, unit_cost: float | None = None,
             reference: str = "", note: str = "", at: str | None = None) -> dict:
        """Record a stock movement. Negative quantities leave the warehouse.

        `at` backdates the movement (used by the demo data loader); leave it out
        and the database stamps the current time.
        """
        product = self.product(product_id)
        if quantity == 0:
            raise ValueError("a movement of zero is not a movement")
        if quantity < 0:
            available = int(product["on_hand"])
            if available + quantity < 0:
                raise InsufficientStock(product["sku"], -quantity, available)
        if at:
            movement_id = self.db.execute(
                """INSERT INTO stock_movements (product_id, quantity, reason, unit_cost, reference, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (product_id, quantity, reason, unit_cost, reference, note, at))
        else:
            movement_id = self.db.execute(
                """INSERT INTO stock_movements (product_id, quantity, reason, unit_cost, reference, note)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (product_id, quantity, reason, unit_cost, reference, note))
        return self.db.one("SELECT * FROM stock_movements WHERE id = ?", (movement_id,))

    def receive(self, product_id: int, quantity: int, unit_cost: float, reference: str = "",
                at: str | None = None) -> dict:
        return self.move(product_id, abs(quantity), "purchase", unit_cost=unit_cost,
                         reference=reference, at=at)

    def sell(self, product_id: int, quantity: int, reference: str = "", at: str | None = None) -> dict:
        return self.move(product_id, -abs(quantity), "sale", reference=reference, at=at)

    def adjust(self, product_id: int, counted: int, note: str = "stock count") -> dict | None:
        """Correct the book figure to a physical count, keeping the difference visible."""
        difference = counted - self.on_hand(product_id)
        if difference == 0:
            return None
        return self.move(product_id, difference, "adjustment", note=note)

    def history(self, product_id: int, limit: int = 50) -> list[dict]:
        return self.db.query(
            "SELECT * FROM stock_movements WHERE product_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (product_id, limit))

    # -------------------------------------------------------- purchase orders
    def create_order(self, supplier_id: int, lines: list[tuple[int, int, float]], note: str = "") -> dict:
        """lines: [(product_id, quantity, unit_cost)]. Written in one transaction."""
        if not self.db.one("SELECT 1 FROM suppliers WHERE id = ?", (supplier_id,)):
            raise NotFound(f"supplier {supplier_id} not found")
        if not lines:
            raise ValueError("a purchase order needs at least one line")
        with self.db.transaction() as conn:
            cur = conn.execute("INSERT INTO purchase_orders (supplier_id, note) VALUES (?, ?)",
                               (supplier_id, note))
            order_id = cur.lastrowid
            for product_id, quantity, unit_cost in lines:
                conn.execute(
                    """INSERT INTO purchase_order_lines (order_id, product_id, quantity, unit_cost)
                       VALUES (?, ?, ?, ?)""", (order_id, product_id, quantity, unit_cost))
        return self.order(order_id)

    def order(self, order_id: int) -> dict:
        order = self.db.one(
            """SELECT o.*, s.name AS supplier_name, s.lead_time_days
               FROM purchase_orders o JOIN suppliers s ON s.id = o.supplier_id WHERE o.id = ?""",
            (order_id,))
        if not order:
            raise NotFound(f"purchase order {order_id} not found")
        order["lines"] = self.db.query(
            """SELECT l.*, p.sku, p.name FROM purchase_order_lines l
               JOIN products p ON p.id = l.product_id WHERE l.order_id = ? ORDER BY l.id""", (order_id,))
        order["total_cost"] = round(sum(line["quantity"] * line["unit_cost"] for line in order["lines"]), 2)
        if order["status"] == "placed":
            placed = date.fromisoformat(order["placed_at"][:10])
            order["expected_on"] = (placed + timedelta(days=order["lead_time_days"])).isoformat()
        return order

    def place_order(self, order_id: int) -> dict:
        order = self.order(order_id)
        if order["status"] != "draft":
            raise ValueError(f"order {order_id} is {order['status']}, not draft")
        self.db.execute(
            "UPDATE purchase_orders SET status = 'placed', placed_at = datetime('now') WHERE id = ?",
            (order_id,))
        return self.order(order_id)

    def receive_order(self, order_id: int) -> dict:
        """Receive every line as stock, in one transaction, exactly once."""
        order = self.order(order_id)
        if order["status"] != "placed":
            raise ValueError(f"only a placed order can be received; this one is {order['status']}")
        with self.db.transaction() as conn:
            for line in order["lines"]:
                conn.execute(
                    """INSERT INTO stock_movements (product_id, quantity, reason, unit_cost, reference)
                       VALUES (?, ?, 'purchase', ?, ?)""",
                    (line["product_id"], line["quantity"], line["unit_cost"], f"PO-{order_id}"))
            conn.execute(
                "UPDATE purchase_orders SET status = 'received', received_at = datetime('now') WHERE id = ?",
                (order_id,))
        return self.order(order_id)

    def cancel_order(self, order_id: int) -> dict:
        order = self.order(order_id)
        if order["status"] == "received":
            raise ValueError("a received order cannot be cancelled; return the stock instead")
        self.db.execute("UPDATE purchase_orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        return self.order(order_id)

    def suggest_reorder(self) -> list[dict]:
        """Everything at or below its reorder point, with the quantity to order."""
        rows = self.db.query(
            """SELECT sl.*, s.name AS supplier_name, s.lead_time_days
               FROM stock_levels sl LEFT JOIN suppliers s ON s.id = sl.supplier_id
               WHERE sl.active = 1 AND sl.on_hand <= sl.reorder_point
               ORDER BY (sl.on_hand - sl.reorder_point), sl.sku""")
        for row in rows:
            row["suggested_quantity"] = max(row["reorder_quantity"],
                                            row["reorder_point"] - int(row["on_hand"]))
            row["estimated_cost"] = round(row["suggested_quantity"] * row["cost_price"], 2)
        return rows
