"""
RED -> GREEN -> TRIANGULATE for core/agents/tools/purchases_tools.py.

`crear_compra` is interrupt-gated. Verifies `item_snapshots` (design.md
"Interfaces / Contracts" — PurchasesPort.create_purchase) is built from the
explicit `item_snapshot` field on each line item and forwarded to the
adapter, per-line, in order.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase3_purchases_tools.py
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
from core.application.agents.tools.purchases_tools import PURCHASES_TOOLS, crear_compra
from core.domain import Purchase

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


FAKE_CONFIG = {"configurable": {"creds": TenantCredentials(base_url="https://fake.test", token="secret-token-xyz")}}

SAMPLE_ITEMS = [
    {
        "item_id": 1229,
        "quantity": 2,
        "unit_price": 10.0,
        "item_snapshot": {"description": "TEST ITEM", "internal_id": "AUTO-1", "unit_type_id": "NIU"},
    }
]


def check_no_credential_leak_in_schema():
    for t in PURCHASES_TOOLS:
        schema_str = json.dumps(t.tool_call_schema.model_json_schema()).lower()
        for forbidden in ("token", "base_url", "creds", "secret-token-xyz", "configurable"):
            check(f"{t.name} schema does NOT leak '{forbidden}'", forbidden not in schema_str)


class _State(TypedDict):
    result: str


def _build_purchase_graph():
    async def run_node(state, config: RunnableConfig):
        r = await crear_compra.ainvoke(
            {
                "document_type_id": "01",
                "series": "F001",
                "number": "999",
                "date_of_issue": "2026-06-23",
                "supplier_id": 64,
                "items": SAMPLE_ITEMS,
            },
            config=config,
        )
        return {"result": r}

    graph = StateGraph(_State)
    graph.add_node("run_tool", run_node)
    graph.set_entry_point("run_tool")
    graph.add_edge("run_tool", END)
    return graph.compile(checkpointer=InMemorySaver())


def check_crear_compra_pauses_for_confirmation():
    app = _build_purchase_graph()
    cfg = {"configurable": {"thread_id": "t-purchase-1", **FAKE_CONFIG["configurable"]}}
    first = asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    check("crear_compra pauses via interrupt()", "__interrupt__" in first)
    if "__interrupt__" in first:
        payload = first["__interrupt__"][0].value
        check("interrupt payload carries supplier_id", payload.get("tool_args", {}).get("supplier_id") == 64)


def check_crear_compra_decline_path_no_adapter_call():
    app = _build_purchase_graph()
    cfg = {"configurable": {"thread_id": "t-purchase-2", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    with patch("core.application.agents.tools.purchases_tools.PurchasesAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create_purchase = AsyncMock()
        resumed = asyncio.run(app.ainvoke(Command(resume={"approved": False}), config=cfg))
    check("declined compra returns rejection message", "RECHAZADA" in resumed.get("result", ""))
    check("declined compra NEVER calls adapter.create_purchase", instance.create_purchase.await_count == 0)


def check_crear_compra_approve_path_forwards_item_snapshots():
    app = _build_purchase_graph()
    cfg = {"configurable": {"thread_id": "t-purchase-3", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    captured = {}

    async def fake_create_purchase(draft, *, item_snapshots):
        captured["draft"] = draft
        captured["item_snapshots"] = item_snapshots
        return Purchase(
            id=122, supplier_id=draft["supplier_id"], doc_type_id=draft["document_type_id"],
            series=draft["series"], number="F001-999005", date_of_issue=draft["date_of_issue"],
            items=draft["items"], total=draft["total"],
        )

    with patch("core.application.agents.tools.purchases_tools.PurchasesAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create_purchase = AsyncMock(side_effect=fake_create_purchase)
        resumed = asyncio.run(app.ainvoke(Command(resume={"approved": True}), config=cfg))
    check("approved compra calls adapter.create_purchase exactly once", instance.create_purchase.await_count == 1)
    check(
        "approved compra forwards item_snapshots matching the input item_snapshot",
        captured["item_snapshots"] == [{"description": "TEST ITEM", "internal_id": "AUTO-1", "unit_type_id": "NIU"}],
    )
    check("approved compra computes total from quantity*unit_price", captured["draft"]["total"] == 20.0)
    check("approved compra result echoes real purchase number", "F001-999005" in resumed.get("result", ""))


def main():
    check_no_credential_leak_in_schema()
    check_crear_compra_pauses_for_confirmation()
    check_crear_compra_decline_path_no_adapter_call()
    check_crear_compra_approve_path_forwards_item_snapshots()

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
