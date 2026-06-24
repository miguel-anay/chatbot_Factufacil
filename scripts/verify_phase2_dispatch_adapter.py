"""
RED -> GREEN -> TRIANGULATE for adapters/facturadorpro7_api/dispatch_adapter.py.

send_dispatch() is the IRREVERSIBLE SUNAT-facing step — mocked ONLY here.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_dispatch_adapter.py
"""
import asyncio
import json as _json
import sys

import httpx

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from adapters.facturadorpro7_api.dispatch_adapter import DispatchAdapter
from core.domain import Dispatch, DispatchTables
from core.ports import DispatchPort

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


def make_adapter(handler) -> DispatchAdapter:
    creds = TenantCredentials(base_url="https://fake.tenant.test", token="fake-token")
    client = FacturadorPro7Client(creds)
    client._client._transport = httpx.MockTransport(handler)
    return DispatchAdapter(client)


def check_is_dispatch_port():
    adapter = make_adapter(lambda r: httpx.Response(200, json={}))
    check("DispatchAdapter implements DispatchPort (ABC)", isinstance(adapter, DispatchPort))


def check_get_tables_parses_real_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "transferReasonTypes": [{"id": "01", "description": "Venta"}],
            "transportModeTypes": [{"id": "01", "description": "Transporte público"}],
            "establishments": [{"id": 1, "description": "Oficina"}],
        })

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.get_tables())
    check("get_tables() returns a DispatchTables entity", isinstance(result, DispatchTables))
    check("get_tables() maps real transfer_reasons", result.transfer_reasons[0]["description"] == "Venta")
    check("get_tables() maps real transport_modes", result.transport_modes[0]["description"] == "Transporte público")
    check("get_tables() carries the rest in 'extra' (establishments)", "establishments" in result.extra)


def check_create_dispatch_posts_delivery_and_origin():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {
            "id": 201, "external_id": "uuid-disp-1", "number": "T001-201",
        }})

    adapter = make_adapter(handler)
    draft = {"delivery": {"address": "Av. Entrega 123"}, "origin": {"address": "Av. Origen 456"}}
    result = asyncio.run(adapter.create_dispatch(draft))
    check("create_dispatch() calls POST /api/dispatches", seen["path"] == "/api/dispatches")
    check("create_dispatch() sends required delivery.address", seen["body"]["delivery"]["address"] == "Av. Entrega 123")
    check("create_dispatch() sends required origin.address", seen["body"]["origin"]["address"] == "Av. Origen 456")
    check("create_dispatch() returns a real Dispatch entity with addresses", isinstance(result, Dispatch) and result.delivery_address == "Av. Entrega 123")
    check("create_dispatch() carries real external_id in extra", result.extra["external_id"] == "uuid-disp-1")


def check_create_dispatch_with_extra_fields_triangulation():
    """Triangulation: a draft WITH extra fields (per design's escape hatch) sends them through verbatim."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"id": 202, "external_id": "uuid-disp-2"}})

    adapter = make_adapter(handler)
    draft = {
        "delivery": {"address": "X"}, "origin": {"address": "Y"},
        "transfer_reason_type_id": "01", "transport_mode_type_id": "02",
    }
    asyncio.run(adapter.create_dispatch(draft))
    check("create_dispatch() passes through extra fields verbatim (transfer_reason_type_id)",
          seen["body"].get("transfer_reason_type_id") == "01")


def check_list_dispatches_parses_real_paginated_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"id": 1, "external_id": "uuid-x", "number": "T001-1", "state_type_id": "05", "state_type_description": "Aceptado", "customer_name": "ACME"},
        ], "links": {}})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.list_dispatches())
    check("list_dispatches() returns a list of real Dispatch entities", len(result) == 1 and isinstance(result[0], Dispatch))
    check("list_dispatches() maps real sunat_status", result[0].sunat_status == "Aceptado")


def check_send_dispatch_resolves_external_id_then_posts_mocked_only():
    """SUNAT-facing irreversible step — mock ONLY, never executed live."""
    calls = []
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/dispatches/records":
            return httpx.Response(200, json={"data": [
                {"id": 55, "external_id": "uuid-to-send", "number": "T001-55", "state_type_id": "01", "state_type_description": "Pendiente"},
            ]})
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"state_type_id": "05", "state_type_description": "Aceptado"})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.send_dispatch(55))
    check("send_dispatch() resolves external_id via list_dispatches() first", "/api/dispatches/records" in calls)
    check("send_dispatch() posts the resolved external_id, not the numeric id", seen["body"]["external_id"] == "uuid-to-send")
    check("send_dispatch() returns updated Dispatch with real sunat_status", result.sunat_status == "Aceptado")


def check_send_dispatch_raises_when_id_not_found():
    """Triangulation: a different (nonexistent) id must fail cleanly, not silently send garbage."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    adapter = make_adapter(handler)
    try:
        asyncio.run(adapter.send_dispatch(99999))
        check("send_dispatch() with unknown id raises ValueError instead of sending garbage", False)
    except ValueError:
        check("send_dispatch() with unknown id raises ValueError instead of sending garbage", True)


def main():
    check_is_dispatch_port()
    check_get_tables_parses_real_shape()
    check_create_dispatch_posts_delivery_and_origin()
    check_create_dispatch_with_extra_fields_triangulation()
    check_list_dispatches_parses_real_paginated_shape()
    check_send_dispatch_resolves_external_id_then_posts_mocked_only()
    check_send_dispatch_raises_when_id_not_found()

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
