"""
RED -> GREEN -> TRIANGULATE for core/agents/tools/finance_tools.py.

All write tools (`crear_retencion`, `crear_percepcion`, `abrir_caja`,
`cerrar_caja`) are interrupt-gated. `reporte_del_dia`/`reporte_general_ventas`
are read-only.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase3_finance_tools.py
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
from core.application.agents.tools.finance_tools import (
    FINANCE_TOOLS,
    abrir_caja,
    cerrar_caja,
    crear_percepcion,
    crear_retencion,
    reporte_del_dia,
    reporte_general_ventas,
)
from core.domain import Cash, Perception, Report, Retention

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


FAKE_CONFIG = {"configurable": {"creds": TenantCredentials(base_url="https://fake.test", token="secret-token-xyz")}}

SAMPLE_SUPPLIER_IDENTITY = {
    "codigo_tipo_documento_identidad": "6",
    "numero_documento": "20123456789",
    "apellidos_y_nombres_o_razon_social": "ABHER S.A.C.",
    "codigo_pais": "PE",
}
SAMPLE_DOCUMENTOS = [{"document_type_id": "01", "series": "F001", "number": "999", "total": 100.0}]
SAMPLE_CUSTOMER_IDENTITY = {
    "codigo_tipo_documento_identidad": "1",
    "numero_documento": "12345678",
    "apellidos_y_nombres_o_razon_social": "JUAN PEREZ",
    "codigo_pais": "PE",
}


def check_no_credential_leak_in_schema():
    for t in FINANCE_TOOLS:
        schema_str = json.dumps(t.tool_call_schema.model_json_schema()).lower()
        for forbidden in ("token", "base_url", "creds", "secret-token-xyz", "configurable"):
            check(f"{t.name} schema does NOT leak '{forbidden}'", forbidden not in schema_str)


class _State(TypedDict):
    result: str


def _build_graph(coro_factory):
    async def run_node(state, config: RunnableConfig):
        r = await coro_factory(config)
        return {"result": r}

    graph = StateGraph(_State)
    graph.add_node("run_tool", run_node)
    graph.set_entry_point("run_tool")
    graph.add_edge("run_tool", END)
    return graph.compile(checkpointer=InMemorySaver())


# ── crear_retencion ──────────────────────────────────────────────────────────


def check_crear_retencion_pauses_decline_approve():
    async def call(config):
        return await crear_retencion.ainvoke(
            {
                "establishment_fiscal_code": "0000",
                "supplier_identity": SAMPLE_SUPPLIER_IDENTITY,
                "documentos": SAMPLE_DOCUMENTOS,
                "total": 100.0,
            },
            config=config,
        )

    app = _build_graph(call)
    cfg = {"configurable": {"thread_id": "t-retencion-1", **FAKE_CONFIG["configurable"]}}
    first = asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    check("crear_retencion pauses via interrupt()", "__interrupt__" in first)

    cfg2 = {"configurable": {"thread_id": "t-retencion-2", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg2))
    with patch("core.application.agents.tools.finance_tools.FinanceAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create_retention = AsyncMock()
        declined = asyncio.run(app.ainvoke(Command(resume={"approved": False}), config=cfg2))
    check("declined retención returns rejection message", "RECHAZADA" in declined.get("result", ""))
    check("declined retención NEVER calls adapter.create_retention", instance.create_retention.await_count == 0)

    cfg3 = {"configurable": {"thread_id": "t-retencion-3", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg3))
    captured = {}

    async def fake_create_retention(d, *, establishment_fiscal_code, supplier_identity):
        captured["d"] = d
        captured["establishment_fiscal_code"] = establishment_fiscal_code
        captured["supplier_identity"] = supplier_identity
        return Retention(id=1, amount=100.0)

    with patch("core.application.agents.tools.finance_tools.FinanceAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create_retention = AsyncMock(side_effect=fake_create_retention)
        approved = asyncio.run(app.ainvoke(Command(resume={"approved": True}), config=cfg3))
    check("approved retención calls adapter.create_retention exactly once", instance.create_retention.await_count == 1)
    check("approved retención forwards establishment_fiscal_code", captured["establishment_fiscal_code"] == "0000")
    check("approved retención forwards supplier_identity", captured["supplier_identity"]["numero_documento"] == "20123456789")
    check("approved retención forwards documentos as effectively-required input", captured["d"]["documentos"] == SAMPLE_DOCUMENTOS)
    check("approved retención result echoes amount", "monto=100.0" in approved.get("result", ""))


def check_crear_retencion_requires_documentos_field():
    """`documentos` is a required Pydantic field — omitting it must reject
    before the tool body (and before interrupt()) ever runs."""
    try:
        asyncio.run(
            crear_retencion.ainvoke(
                {
                    "establishment_fiscal_code": "0000",
                    "supplier_identity": SAMPLE_SUPPLIER_IDENTITY,
                    "total": 100.0,
                },
                config=FAKE_CONFIG,
            )
        )
        check("crear_retencion rejects missing 'documentos'", False)
    except Exception:
        check("crear_retencion rejects missing 'documentos'", True)


# ── crear_percepcion ─────────────────────────────────────────────────────────


def check_crear_percepcion_pauses_decline_approve():
    async def call(config):
        return await crear_percepcion.ainvoke(
            {"customer_identity": SAMPLE_CUSTOMER_IDENTITY, "total": 50.0}, config=config
        )

    app = _build_graph(call)
    cfg = {"configurable": {"thread_id": "t-percepcion-1", **FAKE_CONFIG["configurable"]}}
    first = asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    check("crear_percepcion pauses via interrupt()", "__interrupt__" in first)

    cfg2 = {"configurable": {"thread_id": "t-percepcion-2", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg2))
    captured = {}

    async def fake_create_perception(d, *, customer_identity):
        captured["d"] = d
        captured["customer_identity"] = customer_identity
        return Perception(id=2, amount=50.0)

    with patch("core.application.agents.tools.finance_tools.FinanceAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.create_perception = AsyncMock(side_effect=fake_create_perception)
        approved = asyncio.run(app.ainvoke(Command(resume={"approved": True}), config=cfg2))
    check("approved percepción calls adapter.create_perception exactly once", instance.create_perception.await_count == 1)
    check(
        "approved percepción does NOT send establishment_fiscal_code (perception needs none — Phase 2 correction)",
        "establishment_fiscal_code" not in captured,
    )
    check("approved percepción forwards customer_identity", captured["customer_identity"]["numero_documento"] == "12345678")
    check("approved percepción result echoes amount", "monto=50.0" in approved.get("result", ""))


# ── abrir_caja / cerrar_caja ─────────────────────────────────────────────────


def check_abrir_caja_pauses_decline_approve():
    async def call(config):
        return await abrir_caja.ainvoke({"beginning_balance": 200.0}, config=config)

    app = _build_graph(call)
    cfg = {"configurable": {"thread_id": "t-caja-1", **FAKE_CONFIG["configurable"]}}
    first = asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    check("abrir_caja pauses via interrupt()", "__interrupt__" in first)

    cfg2 = {"configurable": {"thread_id": "t-caja-2", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg2))
    with patch("core.application.agents.tools.finance_tools.FinanceAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.open_cash = AsyncMock(return_value=Cash(id=9, state=True, beginning_balance=200.0))
        approved = asyncio.run(app.ainvoke(Command(resume={"approved": True}), config=cfg2))
    check("approved abrir_caja calls adapter.open_cash exactly once", instance.open_cash.await_count == 1)
    check("approved abrir_caja returns cash id", "id=9" in approved.get("result", ""))


def check_cerrar_caja_pauses_decline_approve():
    async def call(config):
        return await cerrar_caja.ainvoke({"cash_id": 9}, config=config)

    app = _build_graph(call)
    cfg = {"configurable": {"thread_id": "t-cierre-1", **FAKE_CONFIG["configurable"]}}
    first = asyncio.run(app.ainvoke({"result": ""}, config=cfg))
    check("cerrar_caja pauses via interrupt()", "__interrupt__" in first)

    cfg2 = {"configurable": {"thread_id": "t-cierre-2", **FAKE_CONFIG["configurable"]}}
    asyncio.run(app.ainvoke({"result": ""}, config=cfg2))
    with patch("core.application.agents.tools.finance_tools.FinanceAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.close_cash = AsyncMock(return_value=Cash(id=9, state=False, beginning_balance=0.0))
        approved = asyncio.run(app.ainvoke(Command(resume={"approved": True}), config=cfg2))
    check("approved cerrar_caja calls adapter.close_cash exactly once", instance.close_cash.await_count == 1)
    check("approved cerrar_caja reports closed state", "estado_abierta=False" in approved.get("result", ""))


# ── read-only reports ────────────────────────────────────────────────────────


def check_reporte_del_dia():
    with patch("core.application.agents.tools.finance_tools.FinanceAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.get_daily_report = AsyncMock(return_value=Report(data={"total": 32572.64}))
        result = asyncio.run(reporte_del_dia.ainvoke({"filters": {}}, config=FAKE_CONFIG))
    check("reporte_del_dia returns report data", "32572.64" in result)


def check_reporte_general_ventas():
    with patch("core.application.agents.tools.finance_tools.FinanceAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.get_general_sale_report = AsyncMock(return_value=Report(data={"total": 1000.0}))
        result = asyncio.run(
            reporte_general_ventas.ainvoke(
                {"date_start": "2026-06-01", "date_end": "2026-06-23", "establishment_id": None}, config=FAKE_CONFIG
            )
        )
    check("reporte_general_ventas returns report data", "1000.0" in result)


def main():
    check_no_credential_leak_in_schema()
    check_crear_retencion_pauses_decline_approve()
    check_crear_retencion_requires_documentos_field()
    check_crear_percepcion_pauses_decline_approve()
    check_abrir_caja_pauses_decline_approve()
    check_cerrar_caja_pauses_decline_approve()
    check_reporte_del_dia()
    check_reporte_general_ventas()

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
