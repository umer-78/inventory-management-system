"""inventory: stock control from the command line."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from . import reports
from .db import Database
from .seed import seed as seed_demo
from .service import InsufficientStock, Inventory, NotFound

MONEY = "Rs"


def money(value: float) -> str:
    return f"{MONEY} {value:,.0f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="inventory", description=__doc__)
    ap.add_argument("--db", default=os.environ.get("INVENTORY_DB", "inventory.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the database")
    sub.add_parser("demo", help="fill it with a demo shop and 12 weeks of trading")

    ls = sub.add_parser("list", help="list products")
    ls.add_argument("--low", action="store_true", help="only items at or below the reorder point")
    ls.add_argument("--category")
    ls.add_argument("--search")

    mv = sub.add_parser("move", help="record a stock movement")
    mv.add_argument("sku")
    mv.add_argument("quantity", type=int, help="negative to issue stock")
    mv.add_argument("--reason", default="adjustment",
                    choices=["purchase", "sale", "return", "adjustment", "damage", "transfer"])
    mv.add_argument("--cost", type=float, default=None)
    mv.add_argument("--reference", default="")

    ct = sub.add_parser("count", help="reconcile a physical count")
    ct.add_argument("sku")
    ct.add_argument("counted", type=int)

    hi = sub.add_parser("history", help="movements for one product")
    hi.add_argument("sku")

    sub.add_parser("reorder", help="what to buy now")
    sub.add_parser("valuation", help="what the stock is worth")

    sa = sub.add_parser("sales", help="sales summary")
    sa.add_argument("--days", type=int, default=30)

    dd = sub.add_parser("dead", help="stock that is not moving")
    dd.add_argument("--days", type=int, default=60)

    ex = sub.add_parser("export", help="write the stock list to CSV")
    ex.add_argument("path", type=Path)

    sv = sub.add_parser("serve", help="run the HTTP API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)

    args = ap.parse_args(argv)
    db = Database(args.db)
    inv = Inventory(db)

    def find(sku: str):
        try:
            return inv.product_by_sku(sku)
        except NotFound as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(2) from exc

    if args.cmd == "init":
        print(f"database ready at {Path(args.db).resolve()}")
        return 0

    if args.cmd == "demo":
        if db.one("SELECT 1 FROM products LIMIT 1"):
            print("database already has products — use a new --db path")
            return 1
        info = seed_demo(inv)
        print(f"created {info['products']} products from {info['suppliers']} suppliers, "
              f"with 12 weeks of movements")
        return 0

    if args.cmd == "list":
        rows = inv.products(low_stock=args.low, category=args.category, search=args.search)
        if not rows:
            print("nothing matches")
            return 0
        print(f"{'SKU':<10} {'PRODUCT':<32} {'ON HAND':>8} {'REORDER':>8} {'VALUE':>12}")
        for r in rows:
            flag = "  <-- low" if r["on_hand"] <= r["reorder_point"] else ""
            print(f"{r['sku']:<10} {r['name'][:32]:<32} {r['on_hand']:>8} {r['reorder_point']:>8} "
                  f"{money(r['on_hand'] * r['cost_price']):>12}{flag}")
        return 0

    if args.cmd == "move":
        product = find(args.sku)
        try:
            inv.move(product["product_id"], args.quantity, args.reason,
                     unit_cost=args.cost, reference=args.reference)
        except InsufficientStock as exc:
            print(exc, file=sys.stderr)
            return 3
        print(f"{product['sku']}: {args.quantity:+d} ({args.reason}) -> "
              f"{inv.on_hand(product['product_id'])} on hand")
        return 0

    if args.cmd == "count":
        product = find(args.sku)
        movement = inv.adjust(product["product_id"], args.counted)
        if movement is None:
            print(f"{product['sku']}: count matches the system ({args.counted})")
        else:
            print(f"{product['sku']}: adjusted by {movement['quantity']:+d} to {args.counted}")
        return 0

    if args.cmd == "history":
        product = find(args.sku)
        print(f"{product['sku']} — {product['name']} ({product['on_hand']} on hand)")
        for m in inv.history(product["product_id"]):
            cost = f" @ {money(m['unit_cost'])}" if m["unit_cost"] else ""
            print(f"  {m['created_at']}  {m['quantity']:+5d}  {m['reason']:<11}{cost} {m['reference']}")
        return 0

    if args.cmd == "reorder":
        rows = inv.suggest_reorder()
        if not rows:
            print("nothing needs reordering")
            return 0
        total = sum(r["estimated_cost"] for r in rows)
        print(f"{'SKU':<10} {'PRODUCT':<30} {'ON HAND':>8} {'POINT':>6} {'ORDER':>6} {'COST':>12}  SUPPLIER")
        for r in rows:
            print(f"{r['sku']:<10} {r['name'][:30]:<30} {r['on_hand']:>8} {r['reorder_point']:>6} "
                  f"{r['suggested_quantity']:>6} {money(r['estimated_cost']):>12}  {r['supplier_name'] or '-'}")
        print(f"\n{len(rows)} product{'s' if len(rows) != 1 else ''} to reorder, about {money(total)}")
        return 0

    if args.cmd == "valuation":
        data = reports.valuation(db)
        print(f"{data['total_units']:,} units in stock")
        print(f"  at cost    {money(data['total_at_cost'])}")
        print(f"  at retail  {money(data['total_at_retail'])}")
        print(f"  potential profit {money(data['potential_profit'])}")
        print(f"\n{'SKU':<10} {'ON HAND':>8} {'UNIT COST':>12} {'VALUE':>12} {'MARGIN':>8}")
        for item in sorted(data["items"], key=lambda i: -i["value_at_cost"])[:10]:
            print(f"{item['sku']:<10} {item['on_hand']:>8} {money(item['unit_cost']):>12} "
                  f"{money(item['value_at_cost']):>12} {item['margin_percent']:>7}%")
        return 0

    if args.cmd == "sales":
        data = reports.sales_summary(db, args.days)
        print(f"last {data['days']} days: {data['units_sold']:,} units, "
              f"{money(data['revenue'])} revenue, {money(data['gross_profit'])} gross profit")
        for row in data["products"][:10]:
            print(f"  {row['sku']:<10} {row['units_sold']:>5} units  {money(row['revenue']):>12}")
        return 0

    if args.cmd == "dead":
        rows = reports.dead_stock(db, args.days)
        if not rows:
            print(f"everything has sold in the last {args.days} days")
            return 0
        print(f"not sold in {args.days} days:")
        for r in rows:
            print(f"  {r['sku']:<10} {r['name'][:30]:<30} {r['on_hand']:>5} units  "
                  f"{money(r['tied_up']):>12} tied up  last sold {r['last_sold'] or 'never'}")
        return 0

    if args.cmd == "export":
        rows = inv.products(include_inactive=True)
        with args.path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} products to {args.path}")
        return 0

    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(db), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
