"""
RED -> GREEN -> TRIANGULATE for core/agents/tools/inventory_tools.py.

`registrar_movimiento_stock` is interrupt-gated — its interrupt-cycle checks
build a minimal one-node `StateGraph` + `InMemorySaver`, mirroring the
Phase 0 spike script's verified pattern (the tool's own `config` parameter
must be the node's PROPAGATED `RunnableConfig`, not a freshly-built dict —
calling `interrupt()` outside that propagated config raises
`KeyError: '__pregel_scratchpad'`, confirmed live during this verification).

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase3_inventory_tools.py
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, patch

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command
from typing_extensions import TypedDict

from adapters.facturadorpro7_api.auth import TenantCredentials
from core.application.agents.tools.inventory_tools import (
    INVENTORY_TOOLS,
    activar_o_desactivar_producto,
    actualizar_producto,
    listar_categorias,
    listar_marcas,
    marcar_favorito,
    obtener_producto,
    registrar_movimiento_stock,
)
from core.domain import Brand, Category, Item, StockMovement

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
    for t in INVENTORY_TOOLS:
        schema_str = json.dumps(t.tool_call_schema.model_json_schema()).lower()
        for forbidden in ("token", "base_url", "creds", "secret-token-xyz", "configurable"):
            check(f"{t.name} schema does NOT leak '{forbidden}'", forbidden not in schema_str)


def check_obtener_producto_happy_path():
    fake_item = Item(id=5, description="MESA", price=100.0)
    with patch("core.application.agents.tools.inventory_tools.InventoryAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.get_item = AsyncMock(return_value=fake_item)
        result = asyncio.run(obtener_producto.ainvoke({"id": 5}, config=FAKE_CONFIG))
    check("obtener_producto returns item id", "id=5" in result)
    check("obtener_producto returns description", "MESA" in result)


def check_actualizar_producto_happy_path():
    fake_item = Item(id=5, description="MESA NUEVA", price=120.0)
    with patch("core.application.agents.tools.inventory_tools.InventoryAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.update_item = AsyncMock(return_value=fake_item)
        result = asyncio.run(
            actualizar_producto.ainvoke({"id": 5, "patch": {"description": "MESA NUEVA"}}, config=FAKE_CONFIG)
        )
    check("actualizar_producto returns updated description", "MESA NUEVA" in result)
    check("actualizar_producto passes patch dict through", instance.update_item.await_args.args[1] == {"description": "MESA NUEVA"})


def check_activar_desactivar_producto():
    with patch("core.application.agents.tools.inventory_tools.InventoryAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.change_active = AsyncMock(return_value=None)
        result = asyncio.run(activar_o_desactivar_producto.ainvoke({"id": 7, "active": False}, config=FAKE_CONFIG))
    check("activar_o_desactivar_producto reports desactivado", "desactivado" in result)
    check("activar_o_desactivar_producto calls adapter with active=False", instance.change_active.await_args.args == (7, False))


def check_marcar_favorito():
    with patch("core.application.agents.tools.inventory_tools.InventoryAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.change_favorite = AsyncMock(return_value=None)
        result = asyncio.run(marcar_favorito.ainvoke({"id": 7, "favorite": True}, config=FAKE_CONFIG))
    check("marcar_favorito reports marcado", "marcado como favorito" in result)


def check_listar_categorias():
    with patch("core.application.agents.tools.inventory_tools.InventoryAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.list_categories = AsyncMock(return_value=[Category(id=1, name="Ropa")])
        result = asyncio.run(listar_categorias.ainvoke({}, config=FAKE_CONFIG))
    check("listar_categorias returns category name", "Ropa" in result)


def check_listar_marcas():
    with patch("core.application.agents.tools.inventory_tools.InventoryAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.list_brands = AsyncMock(return_value=[Brand(id=2, name="Nike")])
        result = asyncio.run(listar_marcas.ainvoke({}, config=FAKE_CONFIG))
    check("listar_marcas returns brand name", "Nike" in result)


def check_listar_categorias_empty():
    with patch("core.application.agents.tools.inventory_tools.InventoryAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.list_categories = AsyncMock(return_value=[])
        result = asyncio.run(listar_categorias.ainvoke({}, config=FAKE_CONFIG))
    check("listar_categorias handles empty list", "No hay categor" in result)


# ── registrar_movimiento_stock — interrupt-gated, needs a real graph ───────


class _State(TypedDict):
    result: str


def _build_stock_graph():
    async def run_node(state, config: RunnableConfig):
        r = await registrar_movimiento_stock.ainvoke(
            {
                "item_code": "SKU-1",
                "type": "input",
                "warehouse_id": 1,
                "inventory_transaction_id": 10,
                "quantity": 5,
            },
            config=config,
        )
        return {"result": r}

    graph = StateGraph(_State)
    graph.add_node("run_tool", run_node)
    graph.set_entry_point("run_tool")
    graph.add_edge("run_tool", END)
    return graph.compile(checkpointer=InMemorySaver())


def check_registrar_movimiento_stock_pauses_for_confirmation():
    app = _build_stock_graph()
    cfg = {"configurable": {"thread_id": "t-stock-1", **FAKE_CONFIG["configurable"]}}
    first = asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    check("registrar_movimiento_stock pauses via interrupt()", "__interrupt__" in first)
    if "__interrupt__" in first:
        payload = first["__interrupt__"][0].value
        check("interrupt payload carries tool_name", payload.get("tool_name") == "registrar_movimiento_stock")
        check("interrupt payload carries tool_args", payload.get("tool_args", {}).get("item_code") == "SKU-1")


def check_registrar_movimiento_stock_decline_path_no_adapter_call():
    app = _build_stock_graph()
    cfg = {"configurable": {"thread_id": "t-stock-2", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    with patch("core.application.agents.tools.inventory_tools.InventoryAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.register_transaction = AsyncMock()
        resumed = asyncio.run(app.ainvoke(Command(resume={"approved": False}), config=cfg))
    check("declined movimiento returns rejection message", "RECHAZADO" in resumed.get("result", ""))
    check("declined movimiento NEVER calls adapter.register_transaction", instance.register_transaction.await_count == 0)


def check_registrar_movimiento_stock_approve_path_calls_adapter():
    app = _build_stock_graph()
    cfg = {"configurable": {"thread_id": "t-stock-3", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    fake_movement = StockMovement(id=1, item_code="SKU-1", type="input", warehouse_id=1, quantity=5, resulting_stock=20)
    with patch("core.application.agents.tools.inventory_tools.InventoryAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.register_transaction = AsyncMock(return_value=fake_movement)
        resumed = asyncio.run(app.ainvoke(Command(resume={"approved": True}), config=cfg))
    check("approved movimiento calls adapter.register_transaction exactly once", instance.register_transaction.await_count == 1)
    check("approved movimiento returns resulting stock", "stock resultante=20" in resumed.get("result", ""))


def main():
    check_no_credential_leak_in_schema()
    check_obtener_producto_happy_path()
    check_actualizar_producto_happy_path()
    check_activar_desactivar_producto()
    check_marcar_favorito()
    check_listar_categorias()
    check_listar_marcas()
    check_listar_categorias_empty()
    check_registrar_movimiento_stock_pauses_for_confirmation()
    check_registrar_movimiento_stock_decline_path_no_adapter_call()
    check_registrar_movimiento_stock_approve_path_calls_adapter()

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
