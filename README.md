# Inventory Management System

[![CI](https://github.com/umer-78/inventory-management-system/actions/workflows/ci.yml/badge.svg)](https://github.com/umer-78/inventory-management-system/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688)
![SQLite](https://img.shields.io/badge/SQLite-ledger%20model-003b57)
![License](https://img.shields.io/badge/license-MIT-green)

Stock control for a small business: **products, suppliers, purchase orders,
stock movements, valuation, reorder alerts and dead-stock reports** — as a CLI
and an HTTP API.

The central design decision: **stock on hand is never stored as a number**.
Every receipt, sale, return, damage and correction is a row in
`stock_movements`, and the quantity is the sum of those rows. The history
explains the number, a mistake is fixed with a compensating movement rather than
an overwrite, and two concurrent updates cannot silently clobber each other.

```text
$ inventory --db shop.db demo
created 12 products from 3 suppliers, with 12 weeks of movements

$ inventory --db shop.db list
SKU        PRODUCT                           ON HAND  REORDER        VALUE
CB-HDMI    HDMI 2.1 cable, 1.5m                   51       30    Rs 27,540
CB-USB     USB-C cable, 2m                       117       40    Rs 37,440
HD-1TB     1TB portable drive                     15        5   Rs 136,500
HD-512     512GB NVMe SSD                         12        6    Rs 87,600
HS-300     Over-ear headset                       14        6    Rs 57,400
KB-101     Mechanical keyboard, 87 keys           26        8   Rs 161,200
MN-24      24" IPS monitor                         7        3   Rs 171,500
```

## What it does

| Area | Detail |
|---|---|
| **Catalogue** | SKUs (normalised and unique), categories, suppliers with lead times, cost and sell price, reorder point and quantity |
| **Stock** | Movements with reasons (purchase, sale, return, adjustment, damage, transfer), references and notes; issuing more than you hold is refused |
| **Stock counts** | `inventory count SKU 42` writes the difference as a visible adjustment instead of editing the number |
| **Purchasing** | Draft → placed → received, with an expected date from the supplier's lead time; receiving is one transaction and cannot run twice |
| **Reorder** | Everything at or below its point, with the quantity and the cost to order |
| **Valuation** | Weighted average of what was actually paid, at cost and at retail, with margins |
| **Sales** | Units, revenue and gross profit over a window |
| **Dead stock** | On-hand items with no sale in N days, and the cash tied up in them |
| **Ledger** | Every movement, newest first |

## Quick start

```bash
git clone https://github.com/umer-78/inventory-management-system.git
cd inventory-management-system
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

inventory --db shop.db demo          # 12 products, 3 suppliers, 12 weeks of trading
inventory --db shop.db list --low    # what is running out
inventory --db shop.db reorder       # what to buy, and what it will cost
inventory --db shop.db valuation
inventory --db shop.db sales --days 30
inventory --db shop.db dead --days 60
inventory --db shop.db move CB-USB -12 --reason sale --reference INV-2291
inventory --db shop.db count CB-USB 105
inventory --db shop.db history CB-USB
inventory --db shop.db export stock.csv
inventory --db shop.db serve         # http://127.0.0.1:8000/docs
```

```text
$ inventory --db shop.db valuation
319 units in stock
  at cost    Rs 936,453
  at retail  Rs 1,479,650
  potential profit Rs 543,197
```

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET`/`POST` | `/products` | filters: `category`, `search`, `low_stock` |
| `GET` | `/products/{id}` | product with its last 20 movements |
| `POST` | `/products/{id}/movements` | 409 when there is not enough stock |
| `GET` | `/suppliers` | |
| `GET`/`POST` | `/orders` | |
| `POST` | `/orders/{id}/place` · `/receive` · `/cancel` | state machine, enforced |
| `GET` | `/reports/reorder` · `/valuation` · `/sales` · `/dead-stock` · `/ledger` | |

## Tests

```bash
ruff check .
python -m pytest -q     # 18 tests
```

They cover the movement arithmetic, refusing to oversell, stock counts writing a
correction, SKU normalisation and uniqueness, the full purchase-order state
machine (including that receiving twice is refused and a bad line rolls the whole
order back), weighted-average valuation, reorder suggestions, and the API and CLI
end to end.

## Design notes

- **A view, not a cached column.** `stock_levels` sums the movements, so the
  quantity on hand cannot drift away from its history.
- **Constraints in the database.** Reasons and statuses are `CHECK`ed, quantities
  and prices cannot be negative, and foreign keys are on. Bad data is rejected
  at the lowest level, not only in Python.
- **Transactions where they matter.** Creating and receiving an order writes
  every line or none.
- **Money is reported, not guessed.** Valuation uses the average of what was paid,
  falling back to the list cost only when nothing has been received yet.

## Not included

Multi-warehouse transfers, barcode scanning, serial and batch tracking, FIFO/LIFO
costing and a web UI. The ledger model leaves room for all of them.

## License

[MIT](LICENSE)
