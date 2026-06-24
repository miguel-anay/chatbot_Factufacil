"""
RED -> GREEN -> TRIANGULATE for adapters/facturadorpro7_api/purchases_adapter.py.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_purchases_adapter.py
"""
import asyncio
import json as _json
import sys

import httpx

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from adapters.facturadorpro7_api.purchases_adapter import PurchasesAdapter
from core.domain import Purchase
from core.ports import PurchasesPort

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


def make_adapter(handler) -> PurchasesAdapter:
    creds = TenantCredentials(base_url="https://fake.tenant.test", token="fake-token")
    client = FacturadorPro7Client(creds)
    client._client._transport = httpx.MockTransport(handler)
    return PurchasesAdapter(client)


def check_is_purchases_port():
    adapter = make_adapter(lambda r: httpx.Response(200, json={}))
    check("PurchasesAdapter implements PurchasesPort (ABC)", isinstance(adapter, PurchasesPort))


def check_create_purchase_posts_draft_and_parses_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {
            "id": 301, "number_full": "F001-301", "external_id": "uuid-2",
        }})

    adapter = make_adapter(handler)
    draft = {
        "document_type_id": "01", "series": "F001", "number": "301",
        "date_of_issue": "2026-06-23", "supplier_id": 64,
        "items": [{"item_id": 1229, "quantity": 1}], "total": 0.01,
    }
    result = asyncio.run(adapter.create_purchase(draft))
    check("create_purchase() calls POST /api/purchases", seen["path"] == "/api/purchases")
    check("create_purchase() sends required field supplier_id", seen["body"]["supplier_id"] == 64)
    check("create_purchase() sends required field items", seen["body"]["items"] == draft["items"])
    check("create_purchase() returns Purchase with real id from response", isinstance(result, Purchase) and result.id == 301)
    check("create_purchase() maps the real number_full into number", result.number == "F001-301")
    # Real-tenant discoveries (500 errors against live sandbox, not guessed):
    check("create_purchase() fills default 'time_of_issue' when omitted", "time_of_issue" in seen["body"])
    check("create_purchase() fills default 'currency_type_id' when omitted", seen["body"]["currency_type_id"] == "PEN")
    check("create_purchase() fills default 'exchange_rate_sale' when omitted", seen["body"]["exchange_rate_sale"] == 1.0)


def check_create_purchase_caller_values_override_defaults():
    """Triangulation: an explicit caller value for a defaulted field must win."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"id": 1, "number_full": "X-1"}})

    adapter = make_adapter(handler)
    draft = {
        "document_type_id": "01", "series": "F001", "number": "1",
        "date_of_issue": "2026-06-23", "supplier_id": 1, "items": [],
        "currency_type_id": "USD",
    }
    asyncio.run(adapter.create_purchase(draft))
    check("create_purchase() lets caller's explicit currency_type_id override the default", seen["body"]["currency_type_id"] == "USD")


def check_create_purchase_different_supplier_triangulation():
    """Triangulation: a different supplier/draft produces different real output."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"id": 909, "number_full": "F002-909"}})

    adapter = make_adapter(handler)
    draft = {
        "document_type_id": "01", "series": "F002", "number": "909",
        "date_of_issue": "2026-06-24", "supplier_id": 10,
        "items": [], "total": 99.0,
    }
    result = asyncio.run(adapter.create_purchase(draft))
    check("create_purchase() with a different draft returns a DIFFERENT real id", result.id == 909 and result.supplier_id == 10)


def main():
    check_is_purchases_port()
    check_create_purchase_posts_draft_and_parses_response()
    check_create_purchase_different_supplier_triangulation()
    check_create_purchase_caller_values_override_defaults()

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
