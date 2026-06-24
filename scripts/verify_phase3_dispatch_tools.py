"""
RED -> GREEN -> TRIANGULATE for core/agents/tools/dispatch_tools.py.

`crear_guia_remision` (borrador) is NOT interrupt-gated. `enviar_guia_sunat`
(irreversible SUNAT step) IS interrupt-gated. `obtener_tablas_despacho`/
`listar_guias_remision` are read-only.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase3_dispatch_tools.py
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
from core.agents.tools.dispatch_tools import (
    DISPATCH_TOOLS,
    crear_guia_remision,
    enviar_guia_sunat,
    listar_guias_remision,
    obtener_tablas_despacho,
)
from core.domain import Dispatch, DispatchTables

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
    for t in DISPATCH_TOOLS:
        schema_str = json.dumps(t.tool_call_schema.model_json_schema()).lower()
        for forbidden in ("token", "base_url", "creds", "secret-token-xyz", "configurable"):
            check(f"{t.name} schema does NOT leak '{forbidden}'", forbidden not in schema_str)


def check_obtener_tablas_despacho():
    fake_tables = DispatchTables(transfer_reasons=[{"id": 1, "name": "Venta"}], transport_modes=[{"id": "01", "name": "Publico"}])
    with patch("core.agents.tools.dispatch_tools.DispatchAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.get_tables = AsyncMock(return_value=fake_tables)
        result = asyncio.run(obtener_tablas_despacho.ainvoke({}, config=FAKE_CONFIG))
    check("obtener_tablas_despacho returns transfer reasons", "Venta" in result)
    check("obtener_tablas_despacho returns transport modes", "Publico" in result)


def check_crear_guia_remision_not_interrupt_gated():
    """Draft step — calling it directly (no graph context) must NOT raise
    KeyError('__pregel_scratchpad'), proving it does not call interrupt()."""
    fake_dispatch = Dispatch(id=10, origin_address="Av. Origen 123", delivery_address="Av. Destino 456")
    with patch("core.agents.tools.dispatch_tools.DispatchAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create_dispatch = AsyncMock(return_value=fake_dispatch)
        result = asyncio.run(
            crear_guia_remision.ainvoke(
                {
                    "establishment_fiscal_code": "0000",
                    "origin_location_id": "150101",
                    "delivery_location_id": "150102",
                    "origin_address": "Av. Origen 123",
                    "delivery_address": "Av. Destino 456",
                    "transfer_reason_type_id": "01",
                    "transport_mode_type_id": "02",
                    "extra": {},
                },
                config=FAKE_CONFIG,
            )
        )
    check("crear_guia_remision runs WITHOUT a graph context (no interrupt)", "id=10" in result)
    check("crear_guia_remision forwards establishment_fiscal_code kwarg", instance.create_dispatch.await_args.kwargs["establishment_fiscal_code"] == "0000")
    check("crear_guia_remision forwards origin_location_id kwarg", instance.create_dispatch.await_args.kwargs["origin_location_id"] == "150101")
    check("crear_guia_remision forwards delivery_location_id kwarg", instance.create_dispatch.await_args.kwargs["delivery_location_id"] == "150102")


def check_listar_guias_remision():
    fake_dispatches = [Dispatch(id=1, origin_address="A", delivery_address="B", state="06")]
    with patch("core.agents.tools.dispatch_tools.DispatchAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.list_dispatches = AsyncMock(return_value=fake_dispatches)
        result = asyncio.run(listar_guias_remision.ainvoke({"filters": {}}, config=FAKE_CONFIG))
    check("listar_guias_remision returns dispatch id", "id=1" in result)


def check_listar_guias_remision_empty():
    with patch("core.agents.tools.dispatch_tools.DispatchAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.list_dispatches = AsyncMock(return_value=[])
        result = asyncio.run(listar_guias_remision.ainvoke({"filters": {}}, config=FAKE_CONFIG))
    check("listar_guias_remision handles empty list", "No hay guías" in result)


# ── enviar_guia_sunat — interrupt-gated ─────────────────────────────────────


class _State(TypedDict):
    result: str


def _build_send_graph():
    async def run_node(state, config: RunnableConfig):
        r = await enviar_guia_sunat.ainvoke({"id": 10}, config=config)
        return {"result": r}

    graph = StateGraph(_State)
    graph.add_node("run_tool", run_node)
    graph.set_entry_point("run_tool")
    graph.add_edge("run_tool", END)
    return graph.compile(checkpointer=InMemorySaver())


def check_enviar_guia_sunat_pauses():
    app = _build_send_graph()
    cfg = {"configurable": {"thread_id": "t-dispatch-1", **FAKE_CONFIG["configurable"]}}
    first = asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    check("enviar_guia_sunat pauses via interrupt()", "__interrupt__" in first)


def check_enviar_guia_sunat_decline_path():
    app = _build_send_graph()
    cfg = {"configurable": {"thread_id": "t-dispatch-2", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    with patch("core.agents.tools.dispatch_tools.DispatchAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.send_dispatch = AsyncMock()
        resumed = asyncio.run(app.ainvoke(Command(resume={"approved": False}), config=cfg))
    check("declined envío returns rejection message", "RECHAZADO" in resumed.get("result", ""))
    check("declined envío NEVER calls adapter.send_dispatch", instance.send_dispatch.await_count == 0)


def check_enviar_guia_sunat_approve_path():
    app = _build_send_graph()
    cfg = {"configurable": {"thread_id": "t-dispatch-3", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    fake_dispatch = Dispatch(id=10, origin_address="A", delivery_address="B", state="07", sunat_status="ACEPTADO")
    with patch("core.agents.tools.dispatch_tools.DispatchAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.send_dispatch = AsyncMock(return_value=fake_dispatch)
        resumed = asyncio.run(app.ainvoke(Command(resume={"approved": True}), config=cfg))
    check("approved envío calls adapter.send_dispatch exactly once", instance.send_dispatch.await_count == 1)
    check("approved envío returns SUNAT status", "ACEPTADO" in resumed.get("result", ""))


def main():
    check_no_credential_leak_in_schema()
    check_obtener_tablas_despacho()
    check_crear_guia_remision_not_interrupt_gated()
    check_listar_guias_remision()
    check_listar_guias_remision_empty()
    check_enviar_guia_sunat_pauses()
    check_enviar_guia_sunat_decline_path()
    check_enviar_guia_sunat_approve_path()

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
