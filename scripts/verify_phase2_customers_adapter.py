"""
RED -> GREEN -> TRIANGULATE for adapters/facturadorpro7_api/customers_adapter.py.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_customers_adapter.py
"""
import asyncio
import sys

import httpx

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from adapters.facturadorpro7_api.customers_adapter import CustomersAdapter
from core.domain import Customer
from core.ports import CustomersPort

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


def make_adapter(handler) -> CustomersAdapter:
    creds = TenantCredentials(base_url="https://fake.tenant.test", token="fake-token")
    client = FacturadorPro7Client(creds)
    client._client._transport = httpx.MockTransport(handler)
    return CustomersAdapter(client)


def check_is_customers_port():
    adapter = make_adapter(lambda r: httpx.Response(200, json={}))
    check("CustomersAdapter implements CustomersPort (ABC)", isinstance(adapter, CustomersPort))


def check_search_calls_real_endpoint_with_query():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"success": True, "data": {"customers": [
            {"id": 5, "number": "20610448578", "name": "YIWU IMPORT CORPORATION E.I.R.L.", "address": "Av. Test 123", "email": "a@b.com"},
        ]}})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.search("yiwu"))
    check("search() calls GET /api/document/search-customers", seen["path"] == "/api/document/search-customers")
    check("search() sends 'input' query param", seen["params"]["input"] == "yiwu")
    check("search() returns a list of Customer entities", isinstance(result, list) and len(result) == 1 and isinstance(result[0], Customer))
    check("search() maps real fields (document_number, name)", result[0].document_number == "20610448578" and result[0].name == "YIWU IMPORT CORPORATION E.I.R.L.")


def check_search_with_no_matches_returns_real_empty():
    """Triangulation: different query -> different (empty) real result."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"customers": []}})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.search("zzz-no-existe-zzz"))
    check("search() with no matches returns an empty list (real empty, not trivial)", result == [])


def main():
    check_is_customers_port()
    check_search_calls_real_endpoint_with_query()
    check_search_with_no_matches_returns_real_empty()

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
