"""
RED -> GREEN -> TRIANGULATE for core/agents/tools/customers_tools.py.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase3_customers_tools.py
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, patch

from adapters.facturadorpro7_api.auth import TenantCredentials
from core.agents.tools.customers_tools import CUSTOMERS_TOOLS, buscar_cliente
from core.domain import Customer

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
    for t in CUSTOMERS_TOOLS:
        schema_str = json.dumps(t.tool_call_schema.model_json_schema()).lower()
        for forbidden in ("token", "base_url", "creds", "secret-token-xyz", "configurable"):
            check(f"{t.name} schema does NOT leak '{forbidden}'", forbidden not in schema_str)


def check_buscar_cliente_happy_path():
    fake_customers = [Customer(id=1, document_number="12345678", name="JUAN PEREZ")]
    with patch("core.agents.tools.customers_tools.CustomersAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.search = AsyncMock(return_value=fake_customers)
        result = asyncio.run(buscar_cliente.ainvoke({"query": "juan"}, config=FAKE_CONFIG))
    check("buscar_cliente returns customer name", "JUAN PEREZ" in result)
    check("buscar_cliente returns document number", "12345678" in result)


def check_buscar_cliente_empty_result():
    with patch("core.agents.tools.customers_tools.CustomersAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.search = AsyncMock(return_value=[])
        result = asyncio.run(buscar_cliente.ainvoke({"query": "nadie"}, config=FAKE_CONFIG))
    check("buscar_cliente handles empty results", "No se encontraron" in result)


def check_buscar_cliente_missing_query_rejected():
    try:
        asyncio.run(buscar_cliente.ainvoke({}, config=FAKE_CONFIG))
        check("buscar_cliente rejects missing 'query'", False)
    except Exception:
        check("buscar_cliente rejects missing 'query'", True)


def main():
    check_no_credential_leak_in_schema()
    check_buscar_cliente_happy_path()
    check_buscar_cliente_empty_result()
    check_buscar_cliente_missing_query_rejected()

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
