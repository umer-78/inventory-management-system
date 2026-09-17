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

PRODUCTS = [
    # sku, name, category, supplier, cost, sell, reorder point, reorder qty, opening stock, weekly sales
    ("KB-101", "Mechanical keyboard, 87 keys", "peripherals", 0, 6200, 9800, 8, 20, 30, 6),
    ("MS-204", "Wireless mouse", "peripherals", 0, 1450, 2600, 15, 40, 60, 14),
    ("HD-512", "512GB NVMe SSD", "storage", 2, 7300, 10900, 6, 15, 18, 4),
    ("HD-1TB", "1TB portable drive", "storage", 2, 9100, 13500, 5, 10, 12, 2),
    ("MN-24", '24" IPS monitor', "displays", 1, 24500, 32900, 3, 6, 7, 1),
    ("CB-USB", "USB-C cable, 2m", "cables", 2, 320, 850, 40, 100, 150, 35),
    ("CB-HDMI", "HDMI 2.1 cable, 1.5m", "cables", 2, 540, 1300, 30, 80, 95, 18),
    ("HS-300", "Over-ear headset", "audio", 1, 4100, 6900, 6, 15, 14, 3),
    ("WC-108", "1080p webcam", "video", 0, 3300, 5400, 5, 12, 9, 2),
    ("PS-65W", "65W GaN charger", "power", 2, 2100, 3900, 10, 25, 22, 7),
    ("RT-AX", "Wi-Fi 6 router", "network", 1, 11800, 16900, 3, 6, 4, 1),
    ("SW-8P", "8-port gigabit switch", "network", 1, 5600, 8200, 4, 8, 6, 1),
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
        products.append((product, weekly))

    # Trading: weekly sales with noise, the occasional damaged unit and restocks.
    for week in range(weeks):
        for product, weekly in products:
            sold = max(0, int(rng.gauss(weekly, weekly * 0.35)))
            available = inv.on_hand(product["product_id"])
            sold = min(sold, available)
            day = week * 7 + rng.randrange(1, 6)
            if sold:
                inv.sell(product["product_id"], sold, reference=f"week-{week + 1}", at=stamp(day))
            if rng.random() < 0.04 and inv.on_hand(product["product_id"]) > 1:
                inv.move(product["product_id"], -1, "damage", note="damaged in transit", at=stamp(day, 12))
            level = inv.on_hand(product["product_id"])
            if level <= product["reorder_point"] and rng.random() < 0.7:
                inv.receive(product["product_id"], product["reorder_quantity"],
                            product["cost_price"] * rng.uniform(0.95, 1.08),
                            reference=f"restock-week-{week + 1}", at=stamp(day, 16))

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
