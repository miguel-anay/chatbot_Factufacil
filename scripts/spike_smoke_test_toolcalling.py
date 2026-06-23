"""
SPIKE — Phase 0 smoke-test gate (multiagente-erp-facturadorpro7).

Throwaway script, NOT part of the production architecture. Proves (or
disproves) two hard assumptions before investing in the ~25 production ERP
tools:

  CHECK 1 — tool-calling: does qwen-plus (via DashScope's OpenAI-compatible
            endpoint, through the SAME ChatOpenAI client config the existing
            OpenAICompatibleAdapter uses) return proper `tool_calls` when
            bound to a trivial async tool via `.bind_tools()`?

  CHECK 2 — interrupt + resume: does `langgraph.types.interrupt()` called
            inside an ASYNC tool actually pause a compiled `StateGraph`
            (with `InMemorySaver`), and does `Command(resume=...)` correctly
            resume and complete it?

This is a REAL smoke test against the live DashScope endpoint — no mocking
the LLM call. Run with the venv active:

    python scripts/spike_smoke_test_toolcalling.py

Exit code 0 only if BOTH checks pass.
"""
import asyncio
import sys

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from infrastructure.config import Config


def build_llm_client() -> ChatOpenAI:
    """Reuses the exact same construction logic as OpenAICompatibleAdapter
    (adapters/llm/openai_compatible.py) — same Qwen/DashScope credentials
    already configured for the existing /chat endpoint. No new config."""
    kwargs: dict = {
        "model": Config.LLM_MODEL,
        "api_key": Config.LLM_API_KEY,
        "temperature": Config.LLM_TEMPERATURE,
    }
    if Config.LLM_BASE_URL:
        kwargs["base_url"] = Config.LLM_BASE_URL
    return ChatOpenAI(**kwargs)


# ── CHECK 1: tool-calling ───────────────────────────────────────────────────

class EchoInput(BaseModel):
    text: str = Field(description="Texto exacto que el usuario quiere que se repita.")


@tool("echo", args_schema=EchoInput)
async def echo_tool(text: str) -> str:
    """Repite exactamente el texto recibido. Usá esta tool SIEMPRE que el
    usuario pida explícitamente que se repita o haga eco de un texto."""
    return text


async def check_tool_calling() -> bool:
    print("\n" + "=" * 70)
    print("CHECK 1 — tool-calling (qwen-plus + .bind_tools())")
    print("=" * 70)

    llm = build_llm_client()
    llm_with_tools = llm.bind_tools([echo_tool])

    prompt = (
        "Usá la tool 'echo' para repetir exactamente el texto: "
        "'PRUEBA-SMOKE-TEST-12345'. Es obligatorio que uses la tool, "
        "no respondas en texto plano."
    )

    try:
        response = await llm_with_tools.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:  # noqa: BLE001 — smoke test, surface raw error
        print(f"❌ FAIL — request to DashScope raised an exception: {exc!r}")
        return False

    print(f"Raw response.content: {response.content!r}")
    print(f"Raw response.tool_calls: {response.tool_calls!r}")

    if not response.tool_calls:
        print("❌ FAIL — model did not return any tool_calls.")
        return False

    call = response.tool_calls[0]
    if call.get("name") != "echo":
        print(f"❌ FAIL — tool_calls present but wrong tool name: {call.get('name')!r}")
        return False

    if "text" not in call.get("args", {}):
        print(f"❌ FAIL — tool call missing expected 'text' arg: {call.get('args')!r}")
        return False

    print(f"✅ PASS — model returned tool_calls correctly: {call}")
    return True


# ── CHECK 2: interrupt() + resume inside an async tool, via a real graph ──

class InterruptingInput(BaseModel):
    payload: str = Field(description="Valor de prueba a confirmar antes de continuar.")


@tool("confirm_then_echo", args_schema=InterruptingInput)
async def confirm_then_echo_tool(payload: str) -> str:
    """Tool de prueba que SIEMPRE pide confirmación humana (interrupt) antes
    de devolver el payload recibido."""
    decision = interrupt({"tool_name": "confirm_then_echo", "payload": payload})
    approved = decision.get("approved") if isinstance(decision, dict) else False
    if not approved:
        return f"REJECTED:{payload}"
    return f"CONFIRMED:{payload}"


class SpikeState(TypedDict):
    payload: str
    result: str


async def run_tool_node(state: SpikeState) -> SpikeState:
    result = await confirm_then_echo_tool.ainvoke({"payload": state["payload"]})
    return {"result": result}


def build_spike_graph():
    graph = StateGraph(SpikeState)
    graph.add_node("run_tool", run_tool_node)
    graph.set_entry_point("run_tool")
    graph.add_edge("run_tool", END)
    return graph.compile(checkpointer=InMemorySaver())


async def check_interrupt_resume() -> bool:
    print("\n" + "=" * 70)
    print("CHECK 2 — interrupt() + Command(resume=...) inside an async tool")
    print("=" * 70)

    app = build_spike_graph()
    config = {"configurable": {"thread_id": "spike-thread-1"}}

    try:
        first_result = await app.ainvoke({"payload": "hola-mundo"}, config=config)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ FAIL — graph.ainvoke raised an exception: {exc!r}")
        return False

    print(f"First ainvoke() result: {first_result!r}")

    if "__interrupt__" not in first_result:
        print(
            "❌ FAIL — graph did NOT pause. Expected '__interrupt__' key in "
            f"result, got keys: {list(first_result.keys())!r}"
        )
        return False

    interrupt_payload = first_result["__interrupt__"]
    print(f"✅ Graph paused as expected. Interrupt payload: {interrupt_payload!r}")

    try:
        resumed_result = await app.ainvoke(
            Command(resume={"approved": True}), config=config
        )
    except Exception as exc:  # noqa: BLE001
        print(f"❌ FAIL — resume ainvoke() raised an exception: {exc!r}")
        return False

    print(f"Resumed ainvoke() result: {resumed_result!r}")

    expected = "CONFIRMED:hola-mundo"
    if resumed_result.get("result") != expected:
        print(
            f"❌ FAIL — resumed result mismatch. Expected result={expected!r}, "
            f"got {resumed_result.get('result')!r}"
        )
        return False

    print(f"✅ PASS — graph resumed and completed correctly: {resumed_result}")
    return True


# ── Entry point ─────────────────────────────────────────────────────────────

async def main() -> int:
    print("Phase 0 smoke-test gate — multiagente-erp-facturadorpro7")
    print(f"LLM_MODEL = {Config.LLM_MODEL!r}  base_url = {Config.LLM_BASE_URL!r}")

    check1_ok = await check_tool_calling()
    check2_ok = await check_interrupt_resume()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"CHECK 1 (tool-calling)     : {'✅ PASS' if check1_ok else '❌ FAIL'}")
    print(f"CHECK 2 (interrupt+resume) : {'✅ PASS' if check2_ok else '❌ FAIL'}")

    if check1_ok and check2_ok:
        print("\n🟢 GATE PASSED — safe to proceed to Phase 1.")
        return 0

    print("\n🔴 GATE FAILED — do NOT proceed to Phase 1.")
    if not check1_ok:
        print(
            "  Fallback per design.md: swap LLM_MODEL to 'qwen-max' or an "
            "OpenAI model (config-only change in .env), then re-run this "
            "script. Do not change production config without sign-off."
        )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
