"""
RED -> GREEN -> TRIANGULATE for adapters/facturadorpro7_api/items_adapter.py.

Unit-level checks using httpx.MockTransport (no network). Confirms
ItemsAdapter.search()/create() build the right request and parse the real
API response shape (per openapi.yaml ItemSummary schema) into the Item
domain entity. The live counterpart is scripts/verify_phase2_items_live.py.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_items_adapter.py
"""
import asyncio
import sys

import httpx

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from adapters.facturadorpro7_api.items_adapter import ItemsAdapter
from core.domain import Item, ItemDraft
from core.ports import ItemsPort

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


def make_adapter(handler) -> ItemsAdapter:
    creds = TenantCredentials(base_url="https://fake.tenant.test", token="fake-token")
    client = FacturadorPro7Client(creds)
    client._client._transport = httpx.MockTransport(handler)
    return ItemsAdapter(client)


def check_is_items_port():
    adapter = make_adapter(lambda r: httpx.Response(200, json={}))
    check("ItemsAdapter implements ItemsPort (ABC)", isinstance(adapter, ItemsPort))


def check_search_hits_search_items_endpoint_with_query():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"success": True, "data": {"items": [
            {"id": 704, "description": "MACETERO X 3", "sale_unit_price": "12.50", "barcode": "78900"},
        ]}})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.search("macetero"))
    check("search() calls GET /api/document/search-items", seen["path"] == "/api/document/search-items")
    check("search() sends 'input' query param with the search text", seen["params"]["input"] == "macetero")
    check("search() returns a list of Item entities", isinstance(result, list) and len(result) == 1 and isinstance(result[0], Item))
    check("search() maps real ItemSummary fields (id, description, price)",
          result[0].id == 704 and result[0].description == "MACETERO X 3" and result[0].price == 12.50)


def check_search_with_by_barcode_sends_flag():
    """Triangulation: different input (by_barcode=True) -> different query param,
    and a DIFFERENT result shape (empty list) to prove emptiness comes from setup.

    Real API discovery (live sandbox): Laravel casts ANY present
    search_by_barcode value (including python bool False, string "false",
    or "0") to truthy EXCEPT the literal string "0" -- sending it at all
    when not explicitly requesting barcode mode silently broke normal text
    search and returned zero results. Fix: omit the key entirely unless
    by_barcode=True, and send "1" (not python True) when it IS true."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"success": True, "data": {"items": []}})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.search("0000000000017", by_barcode=True))
    check("search(by_barcode=True) sends search_by_barcode=1", seen["params"]["search_by_barcode"] == "1")
    check("search() with no matches returns an empty list (real empty, not trivial)", result == [])


def check_search_without_by_barcode_omits_param_entirely():
    """Regression guard for the real API quirk discovered live: when
    by_barcode is not requested, the param must be ABSENT, not False/0."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"success": True, "data": {"items": [
            {"id": 1, "description": "X", "sale_unit_price": "1.00"},
        ]}})

    adapter = make_adapter(handler)
    asyncio.run(adapter.search("texto libre"))
    check("search() (default by_barcode) sends NO search_by_barcode param at all",
          "search_by_barcode" not in seen["params"])


def check_create_posts_to_item_endpoint_with_required_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        import json as _json
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "msg": "ok", "data": {
            "id": 999, "description": "Producto Nuevo", "sale_unit_price": "20.00",
        }})

    adapter = make_adapter(handler)
    draft = ItemDraft(description="Producto Nuevo", price=20.0, barcode="123456")
    result = asyncio.run(adapter.create(draft))
    check("create() calls POST /api/item", seen["path"] == "/api/item")
    check("create() sends required field 'description'", seen["body"]["description"] == "Producto Nuevo")
    check("create() sends required field 'sale_unit_price'", seen["body"]["sale_unit_price"] == 20.0)
    check("create() sends required field 'unit_type_id'", seen["body"]["unit_type_id"] == "NIU")
    check("create() sends required field 'currency_type_id'", seen["body"]["currency_type_id"] == "PEN")
    check("create() sends required field 'sale_affectation_igv_type_id'", "sale_affectation_igv_type_id" in seen["body"])
    check("create() returns the created Item with real id from response", result.id == 999)
    # Fields required by the LIVE tenant's actual validation (discovered via
    # real 422 against the sandbox), not just the openapi.yaml-documented set.
    check("create() sends real-discovered required field 'internal_id'", "internal_id" in seen["body"] and seen["body"]["internal_id"])
    check("create() sends real-discovered required field 'purchase_unit_price'", "purchase_unit_price" in seen["body"])
    check("create() sends real-discovered required field 'purchase_affectation_igv_type_id'", "purchase_affectation_igv_type_id" in seen["body"])
    check("create() sends real-discovered required field 'stock'", seen["body"].get("stock") == 0)
    check("create() sends real-discovered required field 'stock_min'", seen["body"].get("stock_min") == 0)


def check_create_without_barcode_omits_optional_field():
    """Triangulation: a draft with NO barcode must NOT send a barcode key at all."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"id": 1000, "description": "X", "sale_unit_price": "1.00"}})

    adapter = make_adapter(handler)
    draft = ItemDraft(description="X", price=1.0)
    asyncio.run(adapter.create(draft))
    check("create() omits 'barcode' key when not provided in draft", "barcode" not in seen["body"])


def main():
    check_is_items_port()
    check_search_hits_search_items_endpoint_with_query()
    check_search_with_by_barcode_sends_flag()
    check_search_without_by_barcode_omits_param_entirely()
    check_create_posts_to_item_endpoint_with_required_fields()
    check_create_without_barcode_omits_optional_field()

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
