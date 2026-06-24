"""
RED -> GREEN -> TRIANGULATE for adapters/facturadorpro7_api/inventory_adapter.py.

Unit-level checks using httpx.MockTransport (no network).

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_inventory_adapter.py
"""
import asyncio
import json as _json
import sys

import httpx

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from adapters.facturadorpro7_api.inventory_adapter import InventoryAdapter
from core.domain import Brand, Category, Item, StockMovement, StockTxn
from core.ports import InventoryPort

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


def make_adapter(handler) -> InventoryAdapter:
    creds = TenantCredentials(base_url="https://fake.tenant.test", token="fake-token")
    client = FacturadorPro7Client(creds)
    client._client._transport = httpx.MockTransport(handler)
    return InventoryAdapter(client)


def check_is_inventory_port():
    adapter = make_adapter(lambda r: httpx.Response(200, json={}))
    check("InventoryAdapter implements InventoryPort (ABC)", isinstance(adapter, InventoryPort))


def check_get_item_calls_record_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": 704, "description": "Macetero", "sale_unit_price": "12.50"})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.get_item(704))
    check("get_item() calls GET /api/items/record/{id}", seen["path"] == "/api/items/record/704")
    check("get_item() returns Item with real fields", isinstance(result, Item) and result.id == 704 and result.price == 12.50)


def check_get_item_different_id_triangulation():
    """Triangulation: a different id returns different real data, not a hardcoded fake."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 999, "description": "Otra cosa", "sale_unit_price": "5.00"})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.get_item(999))
    check("get_item() with different id returns DIFFERENT real data", result.id == 999 and result.description == "Otra cosa")


def check_update_item_posts_with_id_and_patch():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/items/update":
            seen["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"id": 704, "description": "Nuevo nombre", "sale_unit_price": "20.00"})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.update_item(704, {"description": "Nuevo nombre", "sale_unit_price": 20.0}))
    check("update_item() calls POST /api/items/update", "body" in seen)
    check("update_item() includes id in payload", seen["body"]["id"] == 704)
    check("update_item() includes patch fields", seen["body"]["description"] == "Nuevo nombre")
    check("update_item() returns refreshed Item (re-fetched)", result.description == "Nuevo nombre")


def check_update_item_always_fetches_current_first_and_sends_full_payload():
    """Real API discovery (live sandbox): /api/items/update behaves like a
    full-record update, not a partial PATCH, despite its name and the
    openapi.yaml-documented required set (id/description only). It ALSO
    requires unit_type_id/currency_type_id/sale_unit_price/
    purchase_unit_price/sale_affectation_igv_type_id/
    purchase_affectation_igv_type_id. update_item() must ALWAYS fetch the
    current item first and merge the patch on top of the full required set."""
    calls = []
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/items/update":
            seen["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"id": 5, "description": "Existing Desc", "sale_unit_price": "1.00", "has_igv": True})

    adapter = make_adapter(handler)
    asyncio.run(adapter.update_item(5, {"sale_unit_price": 9.99}))
    check("update_item() fetches current item first (GET before POST)",
          calls.count("/api/items/record/5") >= 1)
    check("update_item() sends full required set: unit_type_id", "unit_type_id" in seen["body"])
    check("update_item() sends full required set: currency_type_id", "currency_type_id" in seen["body"])
    check("update_item() sends full required set: purchase_unit_price", "purchase_unit_price" in seen["body"])
    check("update_item() sends full required set: sale_affectation_igv_type_id", "sale_affectation_igv_type_id" in seen["body"])
    check("update_item() sends full required set: purchase_affectation_igv_type_id", "purchase_affectation_igv_type_id" in seen["body"])
    check("update_item() merges the caller's patch on top (sale_unit_price overridden to 9.99)", seen["body"]["sale_unit_price"] == 9.99)


def check_change_active_calls_correct_path_with_1():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"success": True})

    adapter = make_adapter(handler)
    asyncio.run(adapter.change_active(42, True))
    check("change_active(True) calls GET /api/items/change-active/{id}/1", seen["path"] == "/api/items/change-active/42/1")


def check_change_active_calls_correct_path_with_0():
    """Triangulation: False -> different path segment (0)."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"success": True})

    adapter = make_adapter(handler)
    asyncio.run(adapter.change_active(42, False))
    check("change_active(False) calls GET /api/items/change-active/{id}/0", seen["path"] == "/api/items/change-active/42/0")


def check_change_favorite_calls_correct_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"success": True})

    adapter = make_adapter(handler)
    asyncio.run(adapter.change_favorite(7, True))
    check("change_favorite(True) calls GET /api/items/change-favorite/{id}/1", seen["path"] == "/api/items/change-favorite/7/1")


def check_list_categories_parses_real_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": 1, "name": "Bebidas"}, {"id": 2, "name": "Abarrotes"}]})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.list_categories())
    check("list_categories() returns 2 real Category entities", len(result) == 2 and all(isinstance(c, Category) for c in result))
    check("list_categories() maps real names", {c.name for c in result} == {"Bebidas", "Abarrotes"})


def check_list_brands_parses_real_list():
    """Triangulation: different endpoint/shape, different entity type."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": 10, "name": "Marca X"}]})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.list_brands())
    check("list_brands() returns 1 real Brand entity", len(result) == 1 and isinstance(result[0], Brand))
    check("list_brands() maps real name", result[0].name == "Marca X")


def check_register_transaction_posts_required_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "id": 55, "stock": 15.0})

    adapter = make_adapter(handler)
    txn = StockTxn(item_code="ABC123", type="input", warehouse_id=1, inventory_transaction_id=2, quantity=10.0)
    result = asyncio.run(adapter.register_transaction(txn))
    check("register_transaction() calls /api/inventory/transaction with real item_code", seen["body"]["item_code"] == "ABC123")
    check("register_transaction() sends type='input'", seen["body"]["type"] == "input")
    check("register_transaction() returns StockMovement with real resulting_stock", isinstance(result, StockMovement) and result.resulting_stock == 15.0)


def check_register_transaction_output_type_triangulation():
    """Triangulation: 'output' type with different quantity -> different payload."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "id": 56, "stock": 3.0})

    adapter = make_adapter(handler)
    txn = StockTxn(item_code="XYZ999", type="output", warehouse_id=2, inventory_transaction_id=5, quantity=7.0)
    result = asyncio.run(adapter.register_transaction(txn))
    check("register_transaction() with type='output' sends 'output'", seen["body"]["type"] == "output")
    check("register_transaction() with different quantity returns different resulting_stock", result.resulting_stock == 3.0)


def main():
    check_is_inventory_port()
    check_get_item_calls_record_endpoint()
    check_get_item_different_id_triangulation()
    check_update_item_posts_with_id_and_patch()
    check_update_item_always_fetches_current_first_and_sends_full_payload()
    check_change_active_calls_correct_path_with_1()
    check_change_active_calls_correct_path_with_0()
    check_change_favorite_calls_correct_path()
    check_list_categories_parses_real_list()
    check_list_brands_parses_real_list()
    check_register_transaction_posts_required_fields()
    check_register_transaction_output_type_triangulation()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED CHECKS:")
        for name in FAIL:
            print(f"  - {name}")
        sys.exit(1)
    print("ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
