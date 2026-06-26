"""
Verify Phase 4 — core/agents/compras_agent.py.

Also proves the shared `ItemsPort` subset (items_tools) is correctly wired
into a SECOND agent (Ventas already covers the first) — design.md
"ItemsPort sharing".

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase4_compras_agent.py
"""
import asyncio
import sys
from unittest.mock import AsyncMock, patch

from core.application.agents.compras_agent import build_compras_agent

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
    "buscar_proveedor",
    "crear_compra",
}


def check_construction_and_tools():
    agent = build_compras_agent()
    check("agent name is 'compras'", agent.name == "compras")
    check("agent has exactly 4 tools", len(agent.tools) == 4)
    check("agent tool names match spec exactly", set(agent.tool_names) == EXPECTED_TOOL_NAMES)
    check("system prompt mentions Compras domain", "Compras" in agent.system_prompt)
    check("system prompt is in Spanish", "Respondé" in agent.system_prompt)
    check("system prompt mentions item_snapshot requirement", "item_snapshot" in agent.system_prompt)


def check_live_llm_invokes_expected_tool():
    from adapters.facturadorpro7_api.auth import TenantCredentials

    agent = build_compras_agent()
    fake_config = {
        "configurable": {"creds": TenantCredentials(base_url="https://fake.test", token="fake-token")}
    }

    with patch("core.application.agents.tools.suppliers_tools.SuppliersAdapter") as MockAdapter:
        from core.domain import Supplier

        instance = MockAdapter.return_value
        instance.search = AsyncMock(
            return_value=[Supplier(id=3, document_number="20123456789", name="PROVEEDOR ACME SAC")]
        )
        from langchain_core.messages import HumanMessage

        try:
            new_messages = asyncio.run(
                agent.ainvoke(
                    [HumanMessage(content="Buscá el proveedor ACME")],
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
        "live LLM call invoked 'buscar_proveedor' as expected",
        "buscar_proveedor" in tool_call_names,
    )

    final_text = " ".join(getattr(m, "content", "") or "" for m in new_messages)
    check("final response mentions the mocked supplier name", "ACME" in final_text)


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
