import pytest
from fastapi.testclient import TestClient

from inventory import Database, InsufficientStock, Inventory, NotFound
from inventory.api import create_app
from inventory.cli import main
from inventory.reports import dead_stock, movement_ledger, sales_summary, valuation
from inventory.seed import seed


@pytest.fixture
def inv(tmp_path):
    return Inventory(Database(tmp_path / "t.db"))


@pytest.fixture
def shop(inv):
    supplier = inv.add_supplier("Test Supplier", lead_time_days=3)
    product = inv.add_product("SKU-1", "Widget", 100, 250, supplier_id=supplier["id"],
                              reorder_point=5, reorder_quantity=20)
    return inv, supplier, product


@pytest.fixture
def seeded(tmp_path):
    inventory = Inventory(Database(tmp_path / "demo.db"))
    seed(inventory, weeks=8)
    return inventory


# --------------------------------------------------------------- stock maths
def test_stock_is_the_sum_of_its_movements(shop):
    inv, _, product = shop
    pid = product["product_id"]
    assert inv.on_hand(pid) == 0
    inv.receive(pid, 50, 95)
    inv.sell(pid, 12)
    inv.move(pid, -2, "damage", note="dropped")
    inv.move(pid, 3, "return", note="customer changed their mind")
    assert inv.on_hand(pid) == 39
    assert len(inv.history(pid)) == 4


def test_cannot_sell_more_than_is_on_hand(shop):
    inv, _, product = shop
    inv.receive(product["product_id"], 5, 100)
    with pytest.raises(InsufficientStock) as err:
        inv.sell(product["product_id"], 6)
    assert err.value.available == 5 and err.value.requested == 6
    assert inv.on_hand(product["product_id"]) == 5, "a rejected sale must not change stock"


def test_zero_movements_are_rejected(shop):
    inv, _, product = shop
    with pytest.raises(ValueError):
        inv.move(product["product_id"], 0, "adjustment")


def test_stock_count_writes_a_visible_correction(shop):
    inv, _, product = shop
    pid = product["product_id"]
    inv.receive(pid, 20, 100)
    assert inv.adjust(pid, 20) is None, "a matching count is not a movement"
    movement = inv.adjust(pid, 17, note="annual count")
    assert movement["quantity"] == -3 and movement["reason"] == "adjustment"
    assert inv.on_hand(pid) == 17


def test_unknown_products(inv):
    with pytest.raises(NotFound):
        inv.product(999)
    with pytest.raises(NotFound):
        inv.product_by_sku("nope")


def test_sku_is_normalised_and_unique(inv):
    inv.add_product("ab-1", "A", 10, 20)
    assert inv.product_by_sku("AB-1")["sku"] == "AB-1"
    with pytest.raises(Exception):
        inv.add_product("AB-1", "duplicate", 10, 20)


def test_negative_prices_are_rejected(inv):
    with pytest.raises(Exception):
        inv.add_product("X-1", "X", -5, 10)


# ------------------------------------------------------------ purchase orders
def test_purchase_order_lifecycle(shop):
    inv, supplier, product = shop
    order = inv.create_order(supplier["id"], [(product["product_id"], 20, 95)], note="restock")
    assert order["status"] == "draft" and order["total_cost"] == 1900
    with pytest.raises(ValueError):
        inv.receive_order(order["id"])          # not placed yet
    placed = inv.place_order(order["id"])
    assert placed["status"] == "placed" and placed["expected_on"]
    received = inv.receive_order(order["id"])
    assert received["status"] == "received"
    assert inv.on_hand(product["product_id"]) == 20
    with pytest.raises(ValueError):
        inv.receive_order(order["id"])          # receiving twice would double the stock
    with pytest.raises(ValueError):
        inv.cancel_order(order["id"])


def test_cancelled_order_does_not_move_stock(shop):
    inv, supplier, product = shop
    order = inv.create_order(supplier["id"], [(product["product_id"], 10, 90)])
    inv.cancel_order(order["id"])
    assert inv.on_hand(product["product_id"]) == 0


def test_order_validation(shop):
    inv, supplier, product = shop
    with pytest.raises(NotFound):
        inv.create_order(999, [(product["product_id"], 1, 1)])
    with pytest.raises(ValueError):
        inv.create_order(supplier["id"], [])
    with pytest.raises(Exception):
        inv.create_order(supplier["id"], [(product["product_id"], 0, 10)])   # quantity must be > 0


def test_a_failed_order_line_rolls_the_whole_order_back(shop):
    inv, supplier, product = shop
    before = len(inv.db.query("SELECT id FROM purchase_orders"))
    with pytest.raises(Exception):
        inv.create_order(supplier["id"], [(product["product_id"], 5, 10), (999999, 5, 10)])
    assert len(inv.db.query("SELECT id FROM purchase_orders")) == before, "no half-written order"


# ------------------------------------------------------------------- reports
def test_reorder_suggestions(shop):
    inv, _, product = shop
    pid = product["product_id"]
    inv.receive(pid, 20, 100)
    assert inv.suggest_reorder() == []
    inv.sell(pid, 16)                       # 4 left, reorder point is 5
    rows = inv.suggest_reorder()
    assert [r["sku"] for r in rows] == ["SKU-1"]
    assert rows[0]["suggested_quantity"] == 20
    assert rows[0]["estimated_cost"] == 2000


def test_valuation_uses_the_average_of_what_was_paid(shop):
    inv, _, product = shop
    pid = product["product_id"]
    inv.receive(pid, 10, 100)
    inv.receive(pid, 10, 140)               # average 120
    data = valuation(inv.db)
    item = data["items"][0]
    assert item["unit_cost"] == pytest.approx(120)
    assert item["value_at_cost"] == pytest.approx(2400)
    assert item["value_at_retail"] == pytest.approx(5000)
    assert data["potential_profit"] == pytest.approx(2600)
    assert item["margin_percent"] == pytest.approx(52.0)


def test_valuation_falls_back_to_the_list_cost(shop):
    inv, _, product = shop
    inv.move(product["product_id"], 5, "adjustment", note="found in the back")
    assert valuation(inv.db)["items"][0]["unit_cost"] == 100


def test_sales_and_dead_stock(seeded):
    sales = sales_summary(seeded.db, days=30)
    assert sales["units_sold"] > 0
    assert sales["revenue"] > sales["gross_profit"] > 0
    assert len(movement_ledger(seeded.db, limit=5)) == 5
    assert isinstance(dead_stock(seeded.db, days=1000), list)


def test_seed_produces_a_working_shop(seeded):
    assert len(seeded.products()) == 12
    assert seeded.db.one("SELECT COUNT(*) AS n FROM stock_movements")["n"] > 100
    order = seeded.db.one("SELECT * FROM purchase_orders WHERE status = 'placed'")
    assert order, "the demo should leave one order in flight"


# ----------------------------------------------------------------------- API
def test_api(shop):
    inv, supplier, product = shop
    client = TestClient(create_app(inv.db))
    assert client.get("/health").json()["status"] == "ok"

    created = client.post("/products", json={"sku": "api-1", "name": "From the API",
                                             "cost_price": 50, "sell_price": 90})
    assert created.status_code == 201 and created.json()["sku"] == "API-1"
    assert client.post("/products", json={"sku": "API-1", "name": "dup", "cost_price": 1,
                                          "sell_price": 2}).status_code == 409
    assert client.post("/products", json={"sku": "x", "name": "y", "cost_price": -1,
                                          "sell_price": 2}).status_code == 422

    pid = product["product_id"]
    assert client.post(f"/products/{pid}/movements",
                       json={"quantity": 10, "reason": "purchase", "unit_cost": 95}).status_code == 201
    assert client.post(f"/products/{pid}/movements",
                       json={"quantity": -50, "reason": "sale"}).status_code == 409
    assert client.post(f"/products/{pid}/movements",
                       json={"quantity": 1, "reason": "teleport"}).status_code == 422
    assert client.post("/products/999/movements",
                       json={"quantity": 1, "reason": "purchase"}).status_code == 404

    order = client.post("/orders", json={"supplier_id": supplier["id"],
                                         "lines": [{"product_id": pid, "quantity": 5, "unit_cost": 90}]})
    assert order.status_code == 201
    order_id = order.json()["id"]
    assert client.post(f"/orders/{order_id}/receive").status_code == 409     # not placed
    assert client.post(f"/orders/{order_id}/place").json()["status"] == "placed"
    assert client.post(f"/orders/{order_id}/receive").json()["status"] == "received"
    assert client.get(f"/products/{pid}").json()["on_hand"] == 15

    assert client.get("/reports/valuation").json()["total_units"] > 0
    assert isinstance(client.get("/reports/reorder").json(), list)
    assert client.get("/reports/sales?days=7").json()["days"] == 7
    assert client.get("/orders?status=received").json()[0]["id"] == order_id


def test_cli(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    assert main(["--db", db, "demo"]) == 0
    assert main(["--db", db, "demo"]) == 1          # refuses to seed twice
    capsys.readouterr()
    assert main(["--db", db, "list", "--search", "cable"]) == 0
    assert "CB-USB" in capsys.readouterr().out
    assert main(["--db", db, "move", "CB-USB", "-5", "--reason", "sale"]) == 0
    assert main(["--db", db, "move", "CB-USB", "-999999", "--reason", "sale"]) == 3
    assert main(["--db", db, "count", "CB-USB", "42"]) == 0
    capsys.readouterr()
    assert main(["--db", db, "history", "CB-USB"]) == 0
    assert "adjustment" in capsys.readouterr().out
    assert main(["--db", db, "valuation"]) == 0
    assert main(["--db", db, "export", str(tmp_path / "stock.csv")]) == 0
    assert (tmp_path / "stock.csv").read_text().startswith("product_id,sku,name")
