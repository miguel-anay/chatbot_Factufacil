"""
RED -> GREEN -> TRIANGULATE for adapters/facturadorpro7_api/finance_adapter.py.

create_retention()/create_perception() are mocked here ONLY (the real
required schema is partially undiscovered per the module docstring's OPEN
RISK note — pass-through behavior is verified, not the full real payload).
open_cash()/close_cash()/get_daily_report()/get_general_sale_report() are
verified both via mock here AND live in verify_phase2_finance_live.py.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_finance_adapter.py
"""
import asyncio
import json as _json
import sys

import httpx

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from adapters.facturadorpro7_api.finance_adapter import FinanceAdapter
from core.domain import Cash, Perception, Report, Retention
from core.ports import FinancePort

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


def make_adapter(handler) -> FinanceAdapter:
    creds = TenantCredentials(base_url="https://fake.tenant.test", token="fake-token")
    client = FacturadorPro7Client(creds)
    client._client._transport = httpx.MockTransport(handler)
    return FinanceAdapter(client)


def check_is_finance_port():
    adapter = make_adapter(lambda r: httpx.Response(200, json={}))
    check("FinanceAdapter implements FinancePort (ABC)", isinstance(adapter, FinancePort))


def check_create_retention_passes_through_and_parses_amount():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"id": 1}})

    adapter = make_adapter(handler)
    d = {"totales": {"total": 150.0}, "datos_del_emisor": {}}
    result = asyncio.run(adapter.create_retention(d))
    check("create_retention() calls POST /api/retentions", seen["path"] == "/api/retentions")
    check("create_retention() passes the caller's dict through verbatim", seen["body"] == d)
    check("create_retention() returns Retention with real amount parsed from totales", isinstance(result, Retention) and result.amount == 150.0)


def check_create_perception_different_amount_triangulation():
    """Triangulation: a different amount/dict -> different real output."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"id": 2}})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.create_perception({"totales": {"total": 88.5}}))
    check("create_perception() with a different amount returns a DIFFERENT real value", isinstance(result, Perception) and result.amount == 88.5)


def check_open_cash_fills_beginning_balance_default_and_merges():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"cash_id": 9}})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.open_cash({"beginning_balance": 100.0}))
    check("open_cash() sends beginning_balance", seen["body"]["beginning_balance"] == 100.0)
    check("open_cash() returns real cash_id from response", isinstance(result, Cash) and result.id == 9)
    check("open_cash() returns state=True (opened)", result.state is True)


def check_close_cash_calls_get_not_post():
    """Real discovery: openapi.yaml says POST, the real route is GET."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"success": True, "message": "Caja cerrada con éxito"})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.close_cash(9))
    check("close_cash() calls GET (not POST) /api/cash/close/{cash} -- real route, contradicts openapi.yaml", seen["method"] == "GET" and seen["path"] == "/api/cash/close/9")
    check("close_cash() returns state=False (closed)", result.state is False)


def check_get_daily_report_parses_real_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"general": {"totals": {"total": "100.00"}}}})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.get_daily_report())
    check("get_daily_report() returns a Report entity with real nested data", isinstance(result, Report) and result.data["general"]["totals"]["total"] == "100.00")


def check_get_general_sale_report_derives_period_fields():
    """Real discovery: this tenant requires period/month_start/month_end IN
    ADDITION to date_start/date_end. The adapter derives them when missing."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"data": {"totals": {"total": "32572.64"}}})

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.get_general_sale_report({"date_start": "2026-06-01", "date_end": "2026-06-23"}))
    check("get_general_sale_report() derives 'period' from date_start", seen["body"]["period"] == 2026)
    check("get_general_sale_report() derives 'month_start' from date_start", seen["body"]["month_start"] == 6)
    check("get_general_sale_report() derives 'month_end' from date_start", seen["body"]["month_end"] == 6)
    check("get_general_sale_report() still sends original date_start/date_end", seen["body"]["date_start"] == "2026-06-01")
    check("get_general_sale_report() returns real totals", result.data["totals"]["total"] == "32572.64")


def check_get_general_sale_report_caller_period_overrides():
    """Triangulation: explicit period in the caller's dict must not be overwritten."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"data": {}})

    adapter = make_adapter(handler)
    asyncio.run(adapter.get_general_sale_report({"date_start": "2026-01-01", "date_end": "2026-01-31", "period": 1999}))
    check("get_general_sale_report() does not overwrite an explicit caller-provided period", seen["body"]["period"] == 1999)


def main():
    check_is_finance_port()
    check_create_retention_passes_through_and_parses_amount()
    check_create_perception_different_amount_triangulation()
    check_open_cash_fills_beginning_balance_default_and_merges()
    check_close_cash_calls_get_not_post()
    check_get_daily_report_parses_real_shape()
    check_get_general_sale_report_derives_period_fields()
    check_get_general_sale_report_caller_period_overrides()

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
