"""Demo data: a small electronics shop with three months of trading."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from .service import Inventory

SUPPLIERS = [
    ("Karachi Components", "sales@kcomponents.example", "+92 21 555 0110", 10),
    ("Lahore Tech Supply", "orders@lahoretech.example", "+92 42 555 0144", 5),
    ("Shenzhen Direct", "export@sz-direct.example", "", 21),
]

# Reorder points are set to cover the supplier's lead time at the line's own
# sales rate, plus a margin: roughly daily sales x lead days x 1.3. That is why
# the Shenzhen lines (21 days) carry far more stock than the Lahore ones (5 days)
# for the same weekly demand — a point that ignores lead time is a stockout
# waiting to happen, and the demo would show one.
PRODUCTS = [
    # sku, name, category, supplier, cost, sell, reorder point, reorder qty, opening stock, weekly sales
    ("KB-101", "Mechanical keyboard, 87 keys", "peripherals", 0, 6200, 9800, 12, 25, 40, 6),
    ("MS-204", "Wireless mouse", "peripherals", 0, 1450, 2600, 26, 50, 80, 14),
    ("HD-512", "512GB NVMe SSD", "storage", 2, 7300, 10900, 16, 25, 42, 4),
    ("HD-1TB", "1TB portable drive", "storage", 2, 9100, 13500, 8, 15, 24, 2),
    ("MN-24", '24" IPS monitor', "displays", 1, 24500, 32900, 3, 6, 8, 1),
    ("CB-USB", "USB-C cable, 2m", "cables", 2, 320, 850, 140, 250, 380, 35),
    ("CB-HDMI", "HDMI 2.1 cable, 1.5m", "cables", 2, 540, 1300, 70, 150, 210, 18),
    ("HS-300", "Over-ear headset", "audio", 1, 4100, 6900, 6, 15, 20, 3),
    ("WC-108", "1080p webcam", "video", 0, 3300, 5400, 5, 12, 16, 2),
    ("PS-65W", "65W GaN charger", "power", 2, 2100, 3900, 28, 50, 76, 7),
    ("RT-AX", "Wi-Fi 6 router", "network", 1, 11800, 16900, 3, 6, 8, 1),
    ("SW-8P", "8-port gigabit switch", "network", 1, 5600, 8200, 4, 8, 10, 1),
]


def seed(inv: Inventory, *, weeks: int = 12, seed_value: int = 3) -> dict:
    rng = random.Random(seed_value)
    start = datetime.now() - timedelta(weeks=weeks)
    stamp = lambda day, hour=10: (start + timedelta(days=day, hours=hour)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
    suppliers = [inv.add_supplier(*s) for s in SUPPLIERS]
    products = []
    for sku, name, category, supplier_index, cost, sell, point, quantity, opening, weekly in PRODUCTS:
        product = inv.add_product(sku, name, cost, sell, category=category,
                                  supplier_id=suppliers[supplier_index]["id"],
                                  reorder_point=point, reorder_quantity=quantity)
        inv.receive(product["product_id"], opening, cost, reference="opening stock", at=stamp(0, 8))
        products.append((product, weekly, SUPPLIERS[supplier_index][3]))

    # Trading: weekly sales with noise, the occasional damaged unit and restocks.
    # pending tracks lines already on order, so a slow supplier is not ordered
    # from twice for the same dip.
    pending: dict[int, bool] = {}
    for week in range(weeks):
        for product, weekly, lead_days in products:
            sold = max(0, int(rng.gauss(weekly, weekly * 0.35)))
            available = inv.on_hand(product["product_id"])
            sold = min(sold, available)
            day = week * 7 + rng.randrange(1, 6)
            if sold:
                inv.sell(product["product_id"], sold, reference=f"week-{week + 1}", at=stamp(day))
            if rng.random() < 0.04 and inv.on_hand(product["product_id"]) > 1:
                inv.move(product["product_id"], -1, "damage", note="damaged in transit", at=stamp(day, 12))
            # A restock is not instant: it arrives after the supplier's lead
            # time, which is 5 days from Lahore and 21 from Shenzhen. An order
            # placed too late in the period has simply not landed yet, which is
            # what leaves lines sitting below their reorder point at the end —
            # the state the reorder report exists for. A demo where stock
            # replenishes the moment it dips never exercises it.
            # The reorder point exists to cover the lead time, so the buyer
            # orders every time it is crossed rather than sometimes.
            level = inv.on_hand(product["product_id"])
            if level <= product["reorder_point"] and not pending.get(product["product_id"], False):
                pending[product["product_id"]] = True
                arrival = day + lead_days
                if arrival < weeks * 7:
                    inv.receive(product["product_id"], product["reorder_quantity"],
                                product["cost_price"] * rng.uniform(0.95, 1.08),
                                reference=f"restock-week-{week + 1}", at=stamp(arrival, 16))
                    pending[product["product_id"]] = False

    # One order left in flight, so the purchasing screens have something to show.
    # Prefer anything below its reorder point; otherwise take the three thinnest lines.
    candidates = inv.suggest_reorder()[:3] or sorted(
        inv.products(), key=lambda row: row["on_hand"] - row["reorder_point"])[:3]
    supplier_id = next((row["supplier_id"] for row in candidates if row["supplier_id"]), suppliers[0]["id"])
    order = inv.create_order(
        supplier_id,
        [(row["product_id"], max(row["reorder_quantity"], 5), row["cost_price"]) for row in candidates],
        note="weekly top-up")
    inv.place_order(order["id"])
    return {"suppliers": len(suppliers), "products": len(products), "open_order": order["id"]}
