"""
Verify Phase 5 — core/orchestration/{state,supervisor,graph,confirmation}.py.

Covers:
  1. Graph compiles, has the expected node topology (supervisor + 5
     specialists), no specialist-to-specialist edges, every specialist
     routes to END.
  2. Routing fast-path: a context_module hint for each of the 5 valid
     modules routes `active_specialist` to that exact module, with NO LLM
     call (specialist tool/LLM execution is mocked so this never hits the
     live FacturadorPro7 sandbox nor burns an LLM call for plain routing
     checks).
  3. ONE live LLM call to verify `.with_structured_output()` fallback
     classification actually works end-to-end (real Qwen/DashScope
     credentials from .env — same ones already used by /chat, Phase 0's
     spike, and Phase 4's verify scripts. No new credentials).
  4. Real interrupt()+resume round trip THROUGH THE COMPILED GRAPH (not the
     Phase 0 toy spike) — invoke with a message that drives the `ventas`
     specialist to call `confirmar_y_generar_cpe` (interrupt-gated),
     confirm the graph pauses and surfaces `__interrupt__` info via
     `parse_interrupt_payload()`, then resume via
     `build_resume_command(approved=True)` and confirm the graph completes.
     The adapter's real HTTP call is mocked (no live FacturadorPro7
     sandbox hit) — only the LLM (qwen-plus) and the LangGraph
     interrupt/resume machinery are real.

Graph nodes are async (`specialist_node` wraps `SpecialistAgent.ainvoke()`),
so every invocation in this script uses `graph.ainvoke(...)` via
`asyncio.run(...)`, matching how the real entrypoint (PR7) will call it.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase5_orchestration.py
"""
import asyncio
import sys
import uuid
from unittest.mock import AsyncMock, patch

from langchain_core.messages import HumanMessage

from adapters.facturadorpro7_api.auth import TenantCredentials
from core.application.orchestration.confirmation import build_resume_command, parse_interrupt_payload
from core.application.orchestration.graph import SPECIALIST_BUILDERS, build_graph

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


FAKE_CREDS = TenantCredentials(base_url="https://fake.test", token="fake-token")


def _base_config(thread_id: str) -> dict:
    return {"configurable": {"creds": FAKE_CREDS, "thread_id": thread_id}}


def _initial_state(message: str, *, context_module, thread_id: str) -> dict:
    return {
        "messages": [HumanMessage(content=message)],
        "context_module": context_module,
        "session_id": thread_id,
        "pending_confirmation": None,
        "handoff_reason": None,
    }


def check_graph_topology():
    graph = build_graph()
    nodes = set(graph.get_graph().nodes.keys())
    expected_specialists = set(SPECIALIST_BUILDERS)
    check("graph has all 5 specialist nodes", expected_specialists.issubset(nodes))
    check("graph has the supervisor node", "supervisor" in nodes)
    check(
        "graph has exactly 5 specialist builders (incl. 'inventario')",
        expected_specialists == {"inventario", "compras", "ventas", "logistica", "contabilidad"},
    )

    edges = graph.get_graph().edges
    specialist_to_specialist = [
        e for e in edges if e.source in expected_specialists and e.target in expected_specialists
    ]
    check("no direct specialist-to-specialist edges", len(specialist_to_specialist) == 0)

    every_specialist_to_end = all(
        any(e.source == name and e.target == "__end__" for e in edges) for name in expected_specialists
    )
    check("every specialist routes to END", every_specialist_to_end)


async def _check_routing_fast_path():
    """All 5 context_module hints route correctly, NO LLM call — mocks
    `SpecialistAgent.ainvoke` so the fast-path test never even reaches an
    LLM, proving the supervisor's fast-path genuinely skips the
    classifier."""
    graph = build_graph()

    for module in SPECIALIST_BUILDERS:
        fake_response_messages = [HumanMessage(content=f"respuesta simulada de {module}")]
        with patch(
            "core.application.agents.base.SpecialistAgent.ainvoke",
            new=AsyncMock(return_value=fake_response_messages),
        ):
            thread_id = f"routing-test-{module}-{uuid.uuid4()}"
            result = await graph.ainvoke(
                _initial_state("hola", context_module=module, thread_id=thread_id),
                config=_base_config(thread_id),
            )
        check(
            f"context_module='{module}' routes active_specialist to '{module}'",
            result.get("active_specialist") == module,
        )
        check(
            f"context_module='{module}' fast-path sets a handoff_reason mentioning the hint",
            "context_module hint" in (result.get("handoff_reason") or ""),
        )


async def _check_live_llm_fallback_classification():
    """No context_module hint -> fallback `.with_structured_output()` call.
    REAL LLM call (qwen-plus via .env credentials) — the only classification
    LLM call in this script. Specialist execution itself is mocked
    (routing-only check, the specialist does not need to actually run)."""
    graph = build_graph()
    thread_id = f"fallback-test-{uuid.uuid4()}"

    fake_response_messages = [HumanMessage(content="respuesta simulada")]
    with patch(
        "core.application.agents.base.SpecialistAgent.ainvoke",
        new=AsyncMock(return_value=fake_response_messages),
    ):
        try:
            result = await graph.ainvoke(
                _initial_state(
                    "Quiero registrar una retención a un proveedor por una factura "
                    "de compra que ya pagamos.",
                    context_module=None,
                    thread_id=thread_id,
                ),
                config=_base_config(thread_id),
            )
        except Exception as exc:  # noqa: BLE001 — surface raw error, this is a live call
            check(f"live structured-output fallback call did not raise (got {exc!r})", False)
            return

    check(
        "fallback classified a retention request as 'contabilidad'",
        result.get("active_specialist") == "contabilidad",
    )
    check(
        "fallback handoff_reason mentions LLM classification",
        "LLM classification" in (result.get("handoff_reason") or ""),
    )


async def _check_real_interrupt_resume_through_compiled_graph():
    """The real deal: drive the COMPILED GRAPH (supervisor -> ventas node ->
    SpecialistAgent.ainvoke -> bind_tools -> qwen-plus decides to call
    `confirmar_y_generar_cpe` -> interrupt() pauses) end to end, then resume
    via Command through the SAME compiled graph + checkpointer.

    Mocks ONLY `SalesAdapter`/`build_client` (the real HTTP call to
    FacturadorPro7/SUNAT) — everything else (graph routing, the real
    SpecialistAgent loop, the real qwen-plus LLM call deciding to invoke the
    tool, the real langgraph.types.interrupt()/Command(resume=...)
    machinery, the real InMemorySaver checkpointer) is genuine, not mocked.
    This is the first time interrupt() is exercised through the actual
    production graph instead of Phase 0's standalone toy spike."""
    graph = build_graph()
    thread_id = f"interrupt-resume-test-{uuid.uuid4()}"
    config = _base_config(thread_id)

    from core.domain import Cpe

    with patch("core.application.agents.tools.sales_tools.SalesAdapter") as MockAdapter, patch(
        "core.application.agents.tools.sales_tools.build_client"
    ) as mock_build_client:
        instance = MockAdapter.return_value
        instance.generate_cpe = AsyncMock(
            return_value=Cpe(
                id=999,
                sale_note_id=555,
                document_type_id="03",
                series="F001",
                number="123",
                sunat_status="ACEPTADO",
            )
        )
        mock_build_client.return_value.aclose = AsyncMock()

        try:
            first_result = await graph.ainvoke(
                _initial_state(
                    "Ya confirmé que quiero generar el CPE para el preliminar de "
                    "venta sale_note_id=555, ejecutá confirmar_y_generar_cpe ahora.",
                    context_module="ventas",
                    thread_id=thread_id,
                ),
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 — surface raw error, this is a live LLM call
            check(f"first invoke (pre-pause) did not raise (got {exc!r})", False)
            return

        pending = parse_interrupt_payload(first_result)
        check("graph paused with a pending confirmation (__interrupt__ present)", pending is not None)
        if pending is None:
            return  # cannot test resume if there was no pause to resume from

        check(
            "pending confirmation is for 'confirmar_y_generar_cpe'",
            pending.get("tool_name") == "confirmar_y_generar_cpe",
        )
        check(
            "pending confirmation tool_args references sale_note_id=555",
            (pending.get("tool_args") or {}).get("sale_note_id") == 555,
        )

        resume_command = build_resume_command(approved=True)
        try:
            second_result = await graph.ainvoke(resume_command, config=config)
        except Exception as exc:  # noqa: BLE001
            check(f"resume invoke did not raise (got {exc!r})", False)
            return

    check(
        "resume completed with no further pending interrupt",
        parse_interrupt_payload(second_result) is None,
    )
    final_text = " ".join(str(getattr(m, "content", "") or "") for m in second_result.get("messages", []))
    check("final state mentions the mocked CPE id (999)", "999" in final_text)
    instance.generate_cpe.assert_awaited_once()


async def _run_async_checks():
    await _check_routing_fast_path()
    await _check_live_llm_fallback_classification()
    await _check_real_interrupt_resume_through_compiled_graph()


def main():
    check_graph_topology()
    asyncio.run(_run_async_checks())

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
