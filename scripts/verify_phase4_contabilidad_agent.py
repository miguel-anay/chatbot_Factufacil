"""
Verify Phase 4 — core/agents/contabilidad_agent.py.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase4_contabilidad_agent.py
"""
import asyncio
import sys
from unittest.mock import AsyncMock, patch

from core.application.agents.contabilidad_agent import build_contabilidad_agent

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


EXPECTED_TOOL_NAMES = {
    "crear_retencion",
    "crear_percepcion",
    "abrir_caja",
    "cerrar_caja",
    "reporte_del_dia",
    "reporte_general_ventas",
}


def check_construction_and_tools():
    agent = build_contabilidad_agent()
    check("agent name is 'contabilidad'", agent.name == "contabilidad")
    check("agent has exactly 6 tools", len(agent.tools) == 6)
    check("agent tool names match spec exactly", set(agent.tool_names) == EXPECTED_TOOL_NAMES)
    check(
        "system prompt mentions Contabilidad/Finanzas domain",
        "Contabilidad" in agent.system_prompt and "Finanzas" in agent.system_prompt,
    )
    check("system prompt is in Spanish", "Respondé" in agent.system_prompt)
    check("system prompt mentions documentos requirement for retención", "documentos" in agent.system_prompt)


def check_live_llm_invokes_expected_tool():
    from adapters.facturadorpro7_api.auth import TenantCredentials

    agent = build_contabilidad_agent()
    fake_config = {
        "configurable": {"creds": TenantCredentials(base_url="https://fake.test", token="fake-token")}
    }

    with patch("core.application.agents.tools.finance_tools.FinanceAdapter") as MockAdapter:
        from core.domain import Report

        instance = MockAdapter.return_value
        instance.get_daily_report = AsyncMock(return_value=Report(data={"total": 1234.56}))
        from langchain_core.messages import HumanMessage

        try:
            new_messages = asyncio.run(
                agent.ainvoke(
                    [HumanMessage(content="Dame el reporte de caja/ventas de hoy")],
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
        "live LLM call invoked 'reporte_del_dia' as expected",
        "reporte_del_dia" in tool_call_names,
    )

    final_text = " ".join(getattr(m, "content", "") or "" for m in new_messages)
    check("final response mentions the mocked report total", "1234.56" in final_text)


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
