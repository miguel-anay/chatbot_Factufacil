"""
Verify Phase 4 — core/agents/ventas_agent.py.

Structural checks always run; ONE live LLM call confirms the agent invokes
the expected tool (`buscar_producto`), with the tool's adapter execution
mocked to avoid hitting the live FacturadorPro7 sandbox unnecessarily.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase4_ventas_agent.py
"""
import asyncio
import sys
from unittest.mock import AsyncMock, patch

from core.application.agents.ventas_agent import build_ventas_agent

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


EXPECTED_TOOL_NAMES = {
    "buscar_producto",
    "crear_producto",
    "buscar_cliente",
    "crear_preliminar_venta",
    "confirmar_y_generar_cpe",
}


def check_construction_and_tools():
    agent = build_ventas_agent()
    check("agent name is 'ventas'", agent.name == "ventas")
    check("agent has exactly 5 tools", len(agent.tools) == 5)
    check("agent tool names match spec exactly", set(agent.tool_names) == EXPECTED_TOOL_NAMES)
    check("system prompt mentions Ventas domain", "Ventas" in agent.system_prompt)
    check("system prompt is in Spanish", "Respondé" in agent.system_prompt)
    check("system prompt mentions CPE/SUNAT confirmation", "SUNAT" in agent.system_prompt)


def check_live_llm_invokes_expected_tool():
    from adapters.facturadorpro7_api.auth import TenantCredentials

    agent = build_ventas_agent()
    fake_config = {
        "configurable": {"creds": TenantCredentials(base_url="https://fake.test", token="fake-token")}
    }

    with patch("core.application.agents.tools.items_tools.ItemsAdapter") as MockAdapter:
        from core.domain import Item

        instance = MockAdapter.return_value
        instance.search = AsyncMock(
            return_value=[Item(id=7, description="TORNILLO 1/4", price=2.0, stock=50)]
        )
        from langchain_core.messages import HumanMessage

        try:
            new_messages = asyncio.run(
                agent.ainvoke(
                    [HumanMessage(content="Buscá el producto tornillo en el catálogo")],
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
        "live LLM call invoked 'buscar_producto' as expected",
        "buscar_producto" in tool_call_names,
    )

    final_text = " ".join(getattr(m, "content", "") or "" for m in new_messages)
    check("final response mentions the mocked product description", "TORNILLO" in final_text)


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
