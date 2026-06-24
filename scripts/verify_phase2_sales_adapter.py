"""
RED -> GREEN -> TRIANGULATE for adapters/facturadorpro7_api/sales_adapter.py.

create_sale_note() is verified both via mock AND live (it's a draft, not
SUNAT-submitted). generate_cpe() is the IRREVERSIBLE SUNAT-facing step —
per safety rules it is verified ONLY via mock here (httpx.MockTransport),
NEVER executed for real. A real-tenant 422 attempt for generate_cpe is
separately exercised in verify_phase2_sales_live.py using an intentionally
incomplete/invalid sale_note_id to force a safe validation error without
ever completing a real SUNAT submission.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_sales_adapter.py
"""
import asyncio
import json as _json
import sys

import httpx

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from adapters.facturadorpro7_api.sales_adapter import SalesAdapter
from core.domain import Cpe, SaleNote
from core.ports import SalesPort

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


def make_adapter(handler) -> SalesAdapter:
    creds = TenantCredentials(base_url="https://fake.tenant.test", token="fake-token")
    client = FacturadorPro7Client(creds)
    client._client._transport = httpx.MockTransport(handler)
    return SalesAdapter(client)


def check_is_sales_port():
    adapter = make_adapter(lambda r: httpx.Response(200, json={}))
    check("SalesAdapter implements SalesPort (ABC)", isinstance(adapter, SalesPort))


def check_create_sale_note_posts_draft_and_parses_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {
            "id": 501, "number": "BV01-501", "external_id": "uuid-1",
        }})

    adapter = make_adapter(handler)
    draft = {"series_id": 1, "customer_id": 28, "date_of_issue": "2026-06-23", "items": [{"item_id": 704, "quantity": 1}]}
    result = asyncio.run(adapter.create_sale_note(draft))
    check("create_sale_note() calls POST /api/sale-note", seen["path"] == "/api/sale-note")
    check("create_sale_note() sends the draft's required fields", seen["body"]["customer_id"] == 28 and seen["body"]["series_id"] == 1)
    check("create_sale_note() returns SaleNote with real id from response", isinstance(result, SaleNote) and result.id == 501)
    check("create_sale_note() carries through customer_id and items from the draft", result.customer_id == 28 and result.items == draft["items"])


def check_create_sale_note_different_draft_triangulation():
    """Triangulation: a different customer/series produces different real output."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"id": 777, "number": "BV01-777"}})

    adapter = make_adapter(handler)
    draft = {"series_id": 2, "customer_id": 99, "date_of_issue": "2026-06-24", "items": []}
    result = asyncio.run(adapter.create_sale_note(draft))
    check("create_sale_note() with a different draft returns a DIFFERENT real id", result.id == 777 and result.customer_id == 99)


def check_generate_cpe_posts_to_correct_path_mocked_only():
    """SUNAT-facing irreversible step — mock ONLY per safety rules, never
    executed against the real sandbox in this verification pass."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {
            "id": 12, "number": "B001-12", "state_type_description": "ACEPTADO",
        }})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.generate_cpe(501))
    check("generate_cpe() calls POST /api/sale-note/{id}/generate-cpe", seen["path"] == "/api/sale-note/501/generate-cpe")
    check("generate_cpe() sends required field codigo_tipo_documento", "codigo_tipo_documento" in seen["body"])
    check("generate_cpe() sends required field fecha_de_emision", "fecha_de_emision" in seen["body"])
    check("generate_cpe() sends required field hora_de_emision", "hora_de_emision" in seen["body"])
    check("generate_cpe() returns Cpe entity with real sunat_status", isinstance(result, Cpe) and result.sunat_status == "ACEPTADO")


def main():
    check_is_sales_port()
    check_create_sale_note_posts_draft_and_parses_response()
    check_create_sale_note_different_draft_triangulation()
    check_generate_cpe_posts_to_correct_path_mocked_only()

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
