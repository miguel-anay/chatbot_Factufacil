"""
RED -> GREEN -> TRIANGULATE for core/agents/tools/sales_tools.py.

`crear_preliminar_venta` is NOT interrupt-gated (draft step) — verifies the
IGV/total computation decision (design.md Open Questions: sale-note `total`
NOT-NULL gap, the bare API never computes it server-side). `confirmar_y_generar_cpe`
IS interrupt-gated (irreversible SUNAT step) — verified via a real one-node
graph, same pattern as inventory_tools.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase3_sales_tools.py
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
from core.application.agents.tools.sales_tools import SALES_TOOLS, confirmar_y_generar_cpe, crear_preliminar_venta
from core.domain import Cpe, SaleNote

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
    for t in SALES_TOOLS:
        schema_str = json.dumps(t.tool_call_schema.model_json_schema()).lower()
        for forbidden in ("token", "base_url", "creds", "secret-token-xyz", "configurable"):
            check(f"{t.name} schema does NOT leak '{forbidden}'", forbidden not in schema_str)


def check_crear_preliminar_venta_computes_igv_for_affectation_10():
    """118.0 unit_price (IGV-inclusive) x 2 units, afectacion '10' (Gravado):
    unitValue = 118/1.18 = 100; total_taxed=200; total_igv=36; total=236."""
    captured = {}

    async def fake_create_sale_note(draft):
        captured["draft"] = draft
        return SaleNote(id=1, customer_id=draft["customer_id"], total=draft["total"])

    with patch("core.application.agents.tools.sales_tools.SalesAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create_sale_note = AsyncMock(side_effect=fake_create_sale_note)
        result = asyncio.run(
            crear_preliminar_venta.ainvoke(
                {
                    "series_id": 10,
                    "customer_id": 5,
                    "date_of_issue": "2026-06-23",
                    "items": [
                        {"item_id": 1, "quantity": 2, "unit_price": 118.0, "affectation_type_id": "10"},
                    ],
                },
                config=FAKE_CONFIG,
            )
        )
    draft = captured["draft"]
    check("crear_preliminar_venta computes total=236.0 for IGV-inclusive line", draft["total"] == 236.0)
    check("crear_preliminar_venta computes total_igv=36.0", draft["total_igv"] == 36.0)
    check("crear_preliminar_venta computes total_taxed=200.0", draft["total_taxed"] == 200.0)
    check("crear_preliminar_venta does NOT rely on server-side total computation", "total" in draft and isinstance(draft["total"], float))
    check("crear_preliminar_venta result echoes computed total", "total=236.0" in result)


def check_crear_preliminar_venta_no_igv_for_affectation_20():
    captured = {}

    async def fake_create_sale_note(draft):
        captured["draft"] = draft
        return SaleNote(id=2, customer_id=draft["customer_id"], total=draft["total"])

    with patch("core.application.agents.tools.sales_tools.SalesAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create_sale_note = AsyncMock(side_effect=fake_create_sale_note)
        asyncio.run(
            crear_preliminar_venta.ainvoke(
                {
                    "series_id": 10,
                    "customer_id": 5,
                    "date_of_issue": "2026-06-23",
                    "items": [
                        {"item_id": 2, "quantity": 3, "unit_price": 100.0, "affectation_type_id": "20"},
                    ],
                },
                config=FAKE_CONFIG,
            )
        )
    draft = captured["draft"]
    check("crear_preliminar_venta: afectacion '20' has zero IGV", draft["total_igv"] == 0.0)
    check("crear_preliminar_venta: afectacion '20' total equals gross (no IGV subtracted)", draft["total"] == 300.0)
    check("crear_preliminar_venta: afectacion '20' total_exempt equals gross", draft["total_exempt"] == 300.0)


def check_crear_preliminar_venta_multi_line_aggregates():
    captured = {}

    async def fake_create_sale_note(draft):
        captured["draft"] = draft
        return SaleNote(id=3, customer_id=draft["customer_id"], total=draft["total"])

    with patch("core.application.agents.tools.sales_tools.SalesAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create_sale_note = AsyncMock(side_effect=fake_create_sale_note)
        asyncio.run(
            crear_preliminar_venta.ainvoke(
                {
                    "series_id": 10,
                    "customer_id": 5,
                    "date_of_issue": "2026-06-23",
                    "items": [
                        {"item_id": 1, "quantity": 2, "unit_price": 118.0, "affectation_type_id": "10"},
                        {"item_id": 2, "quantity": 1, "unit_price": 50.0, "affectation_type_id": "30"},
                    ],
                },
                config=FAKE_CONFIG,
            )
        )
    draft = captured["draft"]
    check("crear_preliminar_venta aggregates total across multiple lines", draft["total"] == 286.0)
    check("crear_preliminar_venta aggregates total_unaffected for afectacion '30'", draft["total_unaffected"] == 50.0)


# ── confirmar_y_generar_cpe — interrupt-gated ───────────────────────────────


class _State(TypedDict):
    result: str


def _build_cpe_graph():
    async def run_node(state, config: RunnableConfig):
        r = await confirmar_y_generar_cpe.ainvoke({"sale_note_id": 42}, config=config)
        return {"result": r}

    graph = StateGraph(_State)
    graph.add_node("run_tool", run_node)
    graph.set_entry_point("run_tool")
    graph.add_edge("run_tool", END)
    return graph.compile(checkpointer=InMemorySaver())


def check_confirmar_y_generar_cpe_pauses():
    app = _build_cpe_graph()
    cfg = {"configurable": {"thread_id": "t-cpe-1", **FAKE_CONFIG["configurable"]}}
    first = asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    check("confirmar_y_generar_cpe pauses via interrupt()", "__interrupt__" in first)
    if "__interrupt__" in first:
        payload = first["__interrupt__"][0].value
        check("interrupt payload carries sale_note_id", payload.get("tool_args", {}).get("sale_note_id") == 42)


def check_confirmar_y_generar_cpe_decline_path():
    app = _build_cpe_graph()
    cfg = {"configurable": {"thread_id": "t-cpe-2", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    with patch("core.application.agents.tools.sales_tools.SalesAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.generate_cpe = AsyncMock()
        resumed = asyncio.run(app.ainvoke(Command(resume={"approved": False}), config=cfg))
    check("declined CPE returns rejection message", "RECHAZADA" in resumed.get("result", ""))
    check("declined CPE NEVER calls adapter.generate_cpe", instance.generate_cpe.await_count == 0)


def check_confirmar_y_generar_cpe_approve_path():
    app = _build_cpe_graph()
    cfg = {"configurable": {"thread_id": "t-cpe-3", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    fake_cpe = Cpe(id=1, sale_note_id=42, document_type_id="03", series="B001", number="42", sunat_status="ACEPTADO")
    with patch("core.application.agents.tools.sales_tools.SalesAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.generate_cpe = AsyncMock(return_value=fake_cpe)
        resumed = asyncio.run(app.ainvoke(Command(resume={"approved": True}), config=cfg))
    check("approved CPE calls adapter.generate_cpe exactly once", instance.generate_cpe.await_count == 1)
    check("approved CPE returns SUNAT status", "ACEPTADO" in resumed.get("result", ""))


def main():
    check_no_credential_leak_in_schema()
    check_crear_preliminar_venta_computes_igv_for_affectation_10()
    check_crear_preliminar_venta_no_igv_for_affectation_20()
    check_crear_preliminar_venta_multi_line_aggregates()
    check_confirmar_y_generar_cpe_pauses()
    check_confirmar_y_generar_cpe_decline_path()
    check_confirmar_y_generar_cpe_approve_path()

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
