"""
RED -> GREEN -> TRIANGULATE for core/agents/tools/items_tools.py.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase3_items_tools.py
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, patch

from adapters.facturadorpro7_api.auth import TenantCredentials
from core.application.agents.tools.items_tools import ITEMS_TOOLS, buscar_producto, crear_producto
from core.domain import Item

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
    for t in ITEMS_TOOLS:
        schema_str = json.dumps(t.tool_call_schema.model_json_schema()).lower()
        for forbidden in ("token", "base_url", "creds", "secret-token-xyz", "configurable"):
            check(f"{t.name} schema does NOT leak '{forbidden}'", forbidden not in schema_str)


def check_buscar_producto_schema_validation():
    # Required field missing -> Pydantic validation should reject before the tool body runs.
    try:
        asyncio.run(buscar_producto.ainvoke({"by_barcode": False, "page": 1}, config=FAKE_CONFIG))
        check("buscar_producto rejects missing required 'query'", False)
    except Exception:
        check("buscar_producto rejects missing required 'query'", True)


def check_buscar_producto_happy_path():
    fake_items = [Item(id=1, description="ZAPATO", price=50.0, barcode="123", stock=10)]
    with patch("core.application.agents.tools.items_tools.ItemsAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.search = AsyncMock(return_value=fake_items)
        result = asyncio.run(
            buscar_producto.ainvoke({"query": "zapato", "by_barcode": False, "page": 1}, config=FAKE_CONFIG)
        )
    check("buscar_producto returns formatted result with item id", "id=1" in result)
    check("buscar_producto returns formatted result with description", "ZAPATO" in result)


def check_buscar_producto_empty_result():
    with patch("core.application.agents.tools.items_tools.ItemsAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.search = AsyncMock(return_value=[])
        result = asyncio.run(buscar_producto.ainvoke({"query": "nada", "by_barcode": False, "page": 1}, config=FAKE_CONFIG))
    check("buscar_producto handles empty results gracefully", "No se encontraron" in result)


def check_crear_producto_happy_path():
    fake_item = Item(id=99, description="NUEVO", price=10.0)
    with patch("core.application.agents.tools.items_tools.ItemsAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create = AsyncMock(return_value=fake_item)
        result = asyncio.run(
            crear_producto.ainvoke(
                {
                    "description": "NUEVO",
                    "price": 10.0,
                    "barcode": None,
                    "has_igv": True,
                    "category_id": None,
                    "brand_id": None,
                    "image": None,
                },
                config=FAKE_CONFIG,
            )
        )
    check("crear_producto returns created item id", "id=99" in result)
    check("crear_producto called adapter.create exactly once", instance.create.await_count == 1)


def check_crear_producto_passes_itemdraft_fields():
    captured = {}

    async def fake_create(draft):
        captured["draft"] = draft
        return Item(id=1, description=draft.description, price=draft.price)

    with patch("core.application.agents.tools.items_tools.ItemsAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create = AsyncMock(side_effect=fake_create)
        asyncio.run(
            crear_producto.ainvoke(
                {
                    "description": "CAMISA",
                    "price": 25.5,
                    "barcode": "999",
                    "has_igv": False,
                    "category_id": 3,
                    "brand_id": 4,
                    "image": None,
                },
                config=FAKE_CONFIG,
            )
        )
    draft = captured["draft"]
    check("crear_producto maps description into ItemDraft", draft.description == "CAMISA")
    check("crear_producto maps has_igv=False into ItemDraft", draft.has_igv is False)
    check("crear_producto maps category_id into ItemDraft", draft.category_id == 3)


def main():
    check_no_credential_leak_in_schema()
    check_buscar_producto_schema_validation()
    check_buscar_producto_happy_path()
    check_buscar_producto_empty_result()
    check_crear_producto_happy_path()
    check_crear_producto_passes_itemdraft_fields()

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
