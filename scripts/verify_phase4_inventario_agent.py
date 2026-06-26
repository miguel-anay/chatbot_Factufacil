"""
Verify Phase 4 — core/agents/inventario_agent.py + core/agents/base.py.

This phase is about WIRING correctness (right tools, right prompt, runnable
construction succeeds) — not re-proving the FacturadorPro7 API works
(Phase 2/3) or re-proving qwen-plus tool-calling works in isolation
(Phase 0). Structural checks run always; ONE live LLM call confirms the
agent actually invokes the expected tool (tool's real execution is mocked
to avoid hitting the live FacturadorPro7 sandbox unnecessarily).

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase4_inventario_agent.py
"""
import asyncio
import sys
from unittest.mock import AsyncMock, patch

from core.application.agents.inventario_agent import build_inventario_agent

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
    "obtener_producto",
    "actualizar_producto",
    "activar_o_desactivar_producto",
    "marcar_favorito",
    "listar_categorias",
    "listar_marcas",
    "registrar_movimiento_stock",
}


def check_construction_and_tools():
    agent = build_inventario_agent()
    check("agent name is 'inventario'", agent.name == "inventario")
    check("agent has exactly 9 tools", len(agent.tools) == 9)
    check("agent tool names match spec exactly", set(agent.tool_names) == EXPECTED_TOOL_NAMES)
    check(
        "system prompt mentions Inventario/Producto domain",
        "Inventario" in agent.system_prompt and "Producto" in agent.system_prompt,
    )
    check("system prompt is in Spanish", "Respondé" in agent.system_prompt)
    check("system prompt mentions stock movement confirmation", "stock" in agent.system_prompt.lower())


def check_live_llm_invokes_expected_tool():
    """ONE live LLM call (real Qwen/DashScope credentials from .env, no new
    credentials needed) — confirms the agent actually decides to call
    `buscar_producto`. The tool's adapter execution is mocked so this does
    NOT hit the live FacturadorPro7 sandbox (already proven in PR3/PR4)."""
    from adapters.facturadorpro7_api.auth import TenantCredentials

    agent = build_inventario_agent()
    fake_config = {
        "configurable": {"creds": TenantCredentials(base_url="https://fake.test", token="fake-token")}
    }

    with patch("core.application.agents.tools.inventory_tools.InventoryAdapter") as MockAdapter:
        from core.domain import Item

        instance = MockAdapter.return_value
        instance.get_item = AsyncMock(
            return_value=Item(id=42, description="TORNILLO 1/4", price=1.5, stock=100)
        )
        from langchain_core.messages import HumanMessage

        try:
            new_messages = asyncio.run(
                agent.ainvoke(
                    [HumanMessage(content="Dame el detalle del producto con id 42")],
                    config=fake_config,
                )
            )
        except Exception as exc:  # noqa: BLE001 — surface raw error, this is a live call
            check(f"live LLM call did not raise an exception (got {exc!r})", False)
            return

    tool_call_names = []
    for msg in new_messages:
        if getattr(msg, "tool_calls", None):
            tool_call_names.extend(c["name"] for c in msg.tool_calls)

    check("live LLM call produced at least one tool_call", len(tool_call_names) > 0)
    check(
        "live LLM call invoked 'obtener_producto' as expected",
        "obtener_producto" in tool_call_names,
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
