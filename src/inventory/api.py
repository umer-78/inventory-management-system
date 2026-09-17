"""HTTP API for the inventory."""

from __future__ import annotations

import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import reports
from .db import Database
from .service import InsufficientStock, Inventory, NotFound


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    cost_price: float = Field(ge=0)
    sell_price: float = Field(ge=0)
    category: str = "general"
    supplier_id: int | None = None
    reorder_point: int = Field(default=0, ge=0)
    reorder_quantity: int = Field(default=0, ge=0)


class MovementIn(BaseModel):
    quantity: int = Field(description="positive receives stock, negative issues it")
    reason: Literal["purchase", "sale", "return", "adjustment", "damage", "transfer"]
    unit_cost: float | None = Field(default=None, ge=0)
    reference: str = ""
    note: str = ""


class OrderLineIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    unit_cost: float = Field(ge=0)


class OrderIn(BaseModel):
    supplier_id: int
    lines: list[OrderLineIn] = Field(min_length=1)
    note: str = ""


def create_app(db: Database | None = None) -> FastAPI:
    database = db or Database(os.environ.get("INVENTORY_DB", "inventory.db"))
    inv = Inventory(database)
    app = FastAPI(title="Inventory Management", version="1.0.0",
                  description="Products, stock movements, purchase orders and valuation.")

    @app.exception_handler(NotFound)
    def _not_found(request, exc: NotFound):  # noqa: ANN001
        raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok", "products": len(inv.products())}

    @app.get("/products", tags=["catalogue"])
    def list_products(category: str | None = None, search: str | None = None,
                      low_stock: bool = False, include_inactive: bool = False) -> list[dict]:
        return inv.products(category=category, search=search, low_stock=low_stock,
                            include_inactive=include_inactive)

    @app.post("/products", status_code=201, tags=["catalogue"])
    def add_product(body: ProductIn) -> dict:
        if database.one("SELECT 1 FROM products WHERE sku = ?", (body.sku.upper(),)):
            raise HTTPException(status_code=409, detail=f"sku {body.sku.upper()} already exists")
        return inv.add_product(body.sku, body.name, body.cost_price, body.sell_price,
                               category=body.category, supplier_id=body.supplier_id,
                               reorder_point=body.reorder_point, reorder_quantity=body.reorder_quantity)

    @app.get("/products/{product_id}", tags=["catalogue"])
    def get_product(product_id: int) -> dict:
        try:
            product = inv.product(product_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        product["history"] = inv.history(product_id, limit=20)
        return product

    @app.post("/products/{product_id}/movements", status_code=201, tags=["stock"])
    def add_movement(product_id: int, body: MovementIn) -> dict:
        try:
            return inv.move(product_id, body.quantity, body.reason, unit_cost=body.unit_cost,
                            reference=body.reference, note=body.note)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InsufficientStock as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/suppliers", tags=["catalogue"])
    def suppliers() -> list[dict]:
        return database.query("SELECT * FROM suppliers ORDER BY name")

    @app.get("/orders", tags=["purchasing"])
    def orders(status: str | None = None) -> list[dict]:
        sql = """SELECT o.*, s.name AS supplier_name,
                        (SELECT COUNT(*) FROM purchase_order_lines l WHERE l.order_id = o.id) AS lines
                 FROM purchase_orders o JOIN suppliers s ON s.id = o.supplier_id"""
        if status:
            return database.query(sql + " WHERE o.status = ? ORDER BY o.id DESC", (status,))
        return database.query(sql + " ORDER BY o.id DESC")

    @app.post("/orders", status_code=201, tags=["purchasing"])
    def create_order(body: OrderIn) -> dict:
        try:
            return inv.create_order(body.supplier_id,
                                    [(line.product_id, line.quantity, line.unit_cost) for line in body.lines],
                                    body.note)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/orders/{order_id}", tags=["purchasing"])
    def get_order(order_id: int) -> dict:
        try:
            return inv.order(order_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/orders/{order_id}/{action}", tags=["purchasing"])
    def order_action(order_id: int, action: Literal["place", "receive", "cancel"]) -> dict:
        actions = {"place": inv.place_order, "receive": inv.receive_order, "cancel": inv.cancel_order}
        try:
            return actions[action](order_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/reports/reorder", tags=["reports"])
    def reorder() -> list[dict]:
        return inv.suggest_reorder()

    @app.get("/reports/valuation", tags=["reports"])
    def valuation() -> dict:
        return reports.valuation(database)

    @app.get("/reports/sales", tags=["reports"])
    def sales(days: int = Query(30, ge=1, le=365)) -> dict:
        return reports.sales_summary(database, days)

    @app.get("/reports/dead-stock", tags=["reports"])
    def dead(days: int = Query(60, ge=1, le=365)) -> list[dict]:
        return reports.dead_stock(database, days)

    @app.get("/reports/ledger", tags=["reports"])
    def ledger(limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        return reports.movement_ledger(database, limit)

    return app


app = create_app()
