"""Valuation, margins, dead stock and the movement ledger."""

from __future__ import annotations

from .db import Database


def valuation(db: Database) -> dict:
    """What the stock is worth, at cost and at retail.

    Cost uses the weighted average of what was actually paid on purchase
    movements, falling back to the product's cost price when nothing has been
    received yet. Averaging what you paid is what makes the number defensible.
    """
    rows = db.query(
        """SELECT sl.sku, sl.name, sl.category, sl.on_hand, sl.cost_price, sl.sell_price,
                  (SELECT CASE WHEN SUM(m.quantity) > 0
                               THEN SUM(m.quantity * m.unit_cost) / SUM(m.quantity) END
                     FROM stock_movements m
                    WHERE m.product_id = sl.product_id AND m.reason = 'purchase' AND m.unit_cost IS NOT NULL
                  ) AS average_cost
           FROM stock_levels sl WHERE sl.active = 1 ORDER BY sl.sku""")
    items = []
    for row in rows:
        unit_cost = row["average_cost"] if row["average_cost"] is not None else row["cost_price"]
        at_cost = row["on_hand"] * unit_cost
        at_retail = row["on_hand"] * row["sell_price"]
        items.append({
            "sku": row["sku"], "name": row["name"], "category": row["category"],
            "on_hand": row["on_hand"], "unit_cost": round(unit_cost, 2),
            "value_at_cost": round(at_cost, 2), "value_at_retail": round(at_retail, 2),
            "margin_percent": round(100 * (row["sell_price"] - unit_cost) / row["sell_price"], 1)
            if row["sell_price"] else 0.0,
        })
    return {
        "items": items,
        "total_units": sum(i["on_hand"] for i in items),
        "total_at_cost": round(sum(i["value_at_cost"] for i in items), 2),
        "total_at_retail": round(sum(i["value_at_retail"] for i in items), 2),
        "potential_profit": round(sum(i["value_at_retail"] - i["value_at_cost"] for i in items), 2),
    }


def sales_summary(db: Database, days: int = 30) -> dict:
    rows = db.query(
        """SELECT p.sku, p.name, -SUM(m.quantity) AS units_sold,
                  -SUM(m.quantity) * p.sell_price AS revenue,
                  -SUM(m.quantity) * p.cost_price AS cost
           FROM stock_movements m JOIN products p ON p.id = m.product_id
           WHERE m.reason = 'sale' AND m.created_at >= datetime('now', ?)
           GROUP BY p.id ORDER BY units_sold DESC""", (f"-{days} days",))
    for row in rows:
        row["revenue"] = round(row["revenue"], 2)
        row["gross_profit"] = round(row["revenue"] - row["cost"], 2)
        row.pop("cost")
    return {
        "days": days, "products": rows,
        "units_sold": sum(r["units_sold"] for r in rows),
        "revenue": round(sum(r["revenue"] for r in rows), 2),
        "gross_profit": round(sum(r["gross_profit"] for r in rows), 2),
    }


def dead_stock(db: Database, days: int = 60) -> list[dict]:
    """Stock on hand that has not sold in `days` — cash sitting on a shelf."""
    return db.query(
        """SELECT sl.sku, sl.name, sl.on_hand, sl.cost_price,
                  ROUND(sl.on_hand * sl.cost_price, 2) AS tied_up,
                  (SELECT MAX(created_at) FROM stock_movements m
                    WHERE m.product_id = sl.product_id AND m.reason = 'sale') AS last_sold
           FROM stock_levels sl
           WHERE sl.active = 1 AND sl.on_hand > 0
             AND (last_sold IS NULL OR last_sold < datetime('now', ?))
           ORDER BY tied_up DESC""", (f"-{days} days",))


def movement_ledger(db: Database, limit: int = 100) -> list[dict]:
    return db.query(
        """SELECT m.created_at, p.sku, p.name, m.quantity, m.reason, m.unit_cost, m.reference, m.note
           FROM stock_movements m JOIN products p ON p.id = m.product_id
           ORDER BY m.created_at DESC, m.id DESC LIMIT ?""", (limit,))
