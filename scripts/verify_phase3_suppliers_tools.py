"""
RED -> GREEN -> TRIANGULATE for core/agents/tools/suppliers_tools.py.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase3_suppliers_tools.py
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, patch

from adapters.facturadorpro7_api.auth import TenantCredentials
from core.application.agents.tools.suppliers_tools import SUPPLIERS_TOOLS, buscar_proveedor
from core.domain import Supplier

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


FAKE_CONFIG = {"configurable": {"creds": TenantCredentials(base_url="https://fake.test", token="secret-token-xyz")}}


def check_no_credential_leak_in_schema():
    for t in SUPPLIERS_TOOLS:
        schema_str = json.dumps(t.tool_call_schema.model_json_schema()).lower()
        for forbidden in ("token", "base_url", "creds", "secret-token-xyz", "configurable"):
            check(f"{t.name} schema does NOT leak '{forbidden}'", forbidden not in schema_str)


def check_buscar_proveedor_happy_path():
    fake_suppliers = [Supplier(id=64, document_number="20123456789", name="ABHER S.A.C.")]
    with patch("core.application.agents.tools.suppliers_tools.SuppliersAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.search = AsyncMock(return_value=fake_suppliers)
        result = asyncio.run(buscar_proveedor.ainvoke({"query": "abher"}, config=FAKE_CONFIG))
    check("buscar_proveedor returns supplier name", "ABHER S.A.C." in result)
    check("buscar_proveedor returns document number", "20123456789" in result)


def check_buscar_proveedor_empty_result():
    with patch("core.application.agents.tools.suppliers_tools.SuppliersAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.search = AsyncMock(return_value=[])
        result = asyncio.run(buscar_proveedor.ainvoke({"query": "nadie"}, config=FAKE_CONFIG))
    check("buscar_proveedor handles empty results", "No se encontraron" in result)


def check_buscar_proveedor_missing_query_rejected():
    try:
        asyncio.run(buscar_proveedor.ainvoke({}, config=FAKE_CONFIG))
        check("buscar_proveedor rejects missing 'query'", False)
    except Exception:
        check("buscar_proveedor rejects missing 'query'", True)


def main():
    check_no_credential_leak_in_schema()
    check_buscar_proveedor_happy_path()
    check_buscar_proveedor_empty_result()
    check_buscar_proveedor_missing_query_rejected()

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
