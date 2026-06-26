"""
Verify Phase 4 — core/agents/logistica_agent.py.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase4_logistica_agent.py
"""
import asyncio
import sys
from unittest.mock import AsyncMock, patch

from core.application.agents.logistica_agent import build_logistica_agent

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


EXPECTED_TOOL_NAMES = {
    "obtener_tablas_despacho",
    "crear_guia_remision",
    "enviar_guia_sunat",
    "listar_guias_remision",
}


def check_construction_and_tools():
    agent = build_logistica_agent()
    check("agent name is 'logistica'", agent.name == "logistica")
    check("agent has exactly 4 tools", len(agent.tools) == 4)
    check("agent tool names match spec exactly", set(agent.tool_names) == EXPECTED_TOOL_NAMES)
    check("system prompt mentions Logística domain", "Logística" in agent.system_prompt)
    check("system prompt is in Spanish", "Respondé" in agent.system_prompt)
    check("system prompt mentions SUNAT confirmation", "SUNAT" in agent.system_prompt)


def check_live_llm_invokes_expected_tool():
    from adapters.facturadorpro7_api.auth import TenantCredentials

    agent = build_logistica_agent()
    fake_config = {
        "configurable": {"creds": TenantCredentials(base_url="https://fake.test", token="fake-token")}
    }

    with patch("core.application.agents.tools.dispatch_tools.DispatchAdapter") as MockAdapter:
        from core.domain import DispatchTables

        instance = MockAdapter.return_value
        instance.get_tables = AsyncMock(
            return_value=DispatchTables(
                transfer_reasons=[{"code": "01", "name": "VENTA"}],
                transport_modes=[{"code": "01", "name": "PUBLICO"}],
            )
        )
        from langchain_core.messages import HumanMessage

        try:
            new_messages = asyncio.run(
                agent.ainvoke(
                    [HumanMessage(content="Qué motivos de traslado están disponibles para una guía de remisión?")],
                    config=fake_config,
                )
            )
        except Exception as exc:  # noqa: BLE001
            check(f"live LLM call did not raise an exception (got {exc!r})", False)
            return

    tool_call_names = []
    for msg in new_messages:
        if getattr(msg, "tool_calls", None):
            tool_call_names.extend(c["name"] for c in msg.tool_calls)

    check("live LLM call produced at least one tool_call", len(tool_call_names) > 0)
    check(
        "live LLM call invoked 'obtener_tablas_despacho' as expected",
        "obtener_tablas_despacho" in tool_call_names,
    )

    final_text = " ".join(getattr(m, "content", "") or "" for m in new_messages)
    check("final response mentions the mocked transfer reason", "VENTA" in final_text)


def main():
    check_construction_and_tools()
    check_live_llm_invokes_expected_tool()

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
