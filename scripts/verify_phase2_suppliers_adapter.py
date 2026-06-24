"""
RED -> GREEN -> TRIANGULATE for adapters/facturadorpro7_api/suppliers_adapter.py.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_suppliers_adapter.py
"""
import asyncio
import sys

import httpx

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from adapters.facturadorpro7_api.suppliers_adapter import SuppliersAdapter
from core.domain import Supplier
from core.ports import SuppliersPort

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


def make_adapter(handler) -> SuppliersAdapter:
    creds = TenantCredentials(base_url="https://fake.tenant.test", token="fake-token")
    client = FacturadorPro7Client(creds)
    client._client._transport = httpx.MockTransport(handler)
    return SuppliersAdapter(client)


def check_is_suppliers_port():
    adapter = make_adapter(lambda r: httpx.Response(200, json=[]))
    check("SuppliersAdapter implements SuppliersPort (ABC)", isinstance(adapter, SuppliersPort))


def check_search_parses_real_top_level_list_shape():
    """Real API discovery (live sandbox): /api/purchases/search-suppliers
    returns a PLAIN top-level JSON array, NOT wrapped in {success, data}
    like /api/document/search-customers is. Confirmed live, not guessed."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[
            {"id": 64, "number": "20545418135", "name": "ABHER S.A.C.", "address": "JR. PUNO 618", "email": None},
        ])

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.search("abher"))
    check("search() calls GET /api/purchases/search-suppliers", seen["path"] == "/api/purchases/search-suppliers")
    check("search() sends 'input' query param", seen["params"]["input"] == "abher")
    check("search() parses a plain top-level list response", isinstance(result, list) and len(result) == 1 and isinstance(result[0], Supplier))
    check("search() maps real fields (document_number, name)", result[0].document_number == "20545418135" and result[0].name == "ABHER S.A.C.")


def check_search_with_no_matches_returns_real_empty():
    """Triangulation: different query -> different (empty) real result."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.search("zzz-no-existe-zzz"))
    check("search() with no matches returns an empty list (real empty, not trivial)", result == [])


def main():
    check_is_suppliers_port()
    check_search_parses_real_top_level_list_shape()
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
