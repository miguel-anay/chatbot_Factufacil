"""
Verify Phase 7 — entrypoint wiring integration tests, end-to-end through the
REAL FastAPI app (`entrypoints.api.main.app`), not the bare compiled graph
(that was Phase 5's job). Drives real HTTP request/response shapes
(`AgentChatRequest`/`AgentChatResponse`/`AgentConfirmRequest`) against
`httpx.ASGITransport`, which runs the actual `lifespan()` from main.py —
including the real `build_graph()` call this PR wires in.

Covers (design.md "Testing Strategy" E2E row + tasks.md Phase 7):
  1. Routing: a message with each of the 5 `context_module` values reaches
     the matching specialist (checked via the real `active_specialist`
     value LangGraph's checkpointer stored, read back through
     `GET /agent/session/{id}` — not asserted from logs/mocks alone).
  2. One full propose -> answered cycle with a DRAFT operation
     (`crear_preliminar_venta`, not interrupt-gated) completing normally
     through real HTTP endpoints (adapter's HTTP call mocked — no live
     FacturadorPro7 write for a throwaway integration-test sale note).
  3. One full propose -> awaiting_confirmation -> DECLINE cycle for an
     interrupt-gated write tool (`crear_compra`, approved=False) through the
     REAL LIVE FacturadorPro7 sandbox (real Bearer token/base_url) —
     confirms the HTTP response shapes AND that declining never reaches the
     adapter's real POST (verified by asserting no purchase is created,
     consistent with every prior phase's "never execute irreversible writes
     for real" safety rule — crear_compra IS in the never-approve=True set
     per the launch prompt's explicit instruction).
  4. Lifespan failure isolation: already manually proven in this same apply
     batch (break build_graph() -> /health and /chat still respond, /agent/*
     503s -> revert) — re-encoded here as an automated regression check so
     future changes to main.py don't silently lose this property. Uses a
     SEPARATE temporary app instance with a patched build_graph so it never
     touches the already-compiled `app` instance used by tests 1-3.
  5. Full regression: python test_chatbot.py 14/14 (run separately by the
     caller against the real uvicorn server — see bottom of this docstring
     and the apply-progress notes).

Credentials: live sandbox tenant "YIWU IMPORT CORPORATION E.I.R.L." read
from an external file path (never hardcoded/logged/printed/committed) the
same way every previous phase's live scripts did.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase7_integration.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from unittest.mock import AsyncMock, patch

import httpx

PASS = []
FAIL = []


def check(name: str, condition: bool):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"FAIL: {name}")


CREDS_PATH = (
    "/tmp/claude-1000/-home-k3n5h1n-Escritorio-chatbot-proyecto-final-factufacil/"
    "46876d2d-f8e8-4b69-8fb7-41bdf5c395a0/scratchpad/dev_tenant_creds.json"
)


def _load_live_creds() -> dict:
    with open(CREDS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class _RunningApp:
    """`httpx.ASGITransport` NO dispara los eventos `lifespan` del protocolo
    ASGI (a diferencia de un server real como uvicorn) — verificado en este
    mismo batch: sin esto, `chatbot`/`app.state.agent_graph` quedan en su
    valor inicial (`None`) y cada endpoint crashea. Este context manager
    maneja `app.router.lifespan_context(app)` a mano (el mismo async context
    manager que FastAPI ya construye internamente desde `lifespan=lifespan`
    en `main.py` — no es un mecanismo nuevo, es invocar el existente
    explícitamente) para que `build_graph()`/`ChatbotService(...)` corran
    de verdad antes de cada batch de requests, igual que un arranque real."""

    def __init__(self, app):
        self.app = app
        self._lifespan_ctx = None
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        self._lifespan_ctx = self.app.router.lifespan_context(self.app)
        await self._lifespan_ctx.__aenter__()
        transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        if self.client is not None:
            await self.client.aclose()
        if self._lifespan_ctx is not None:
            await self._lifespan_ctx.__aexit__(exc_type, exc, tb)


async def _check_routing_all_modules(client: httpx.AsyncClient):
    """5 context_module hints -> cada uno enruta al especialista correcto.
    Mockea `SpecialistAgent.ainvoke` (igual patrón que Phase 5) para que
    esto NUNCA llegue a un LLM real ni a la API de FacturadorPro7 — es un
    test de ROUTING puro, a través del HTTP real (no del grafo desnudo)."""
    modules = ["inventario", "compras", "ventas", "logistica", "contabilidad"]
    fake_creds = {"tenant_base_url": "https://fake.test", "tenant_token": "fake-token"}

    for module in modules:
        with patch(
            "core.application.agents.base.SpecialistAgent.ainvoke",
            new=AsyncMock(return_value=[{"role": "assistant", "content": f"respuesta simulada de {module}"}]),
        ):
            session_id = f"routing-test-{module}-{uuid.uuid4()}"
            resp = await client.post(
                "/agent/chat",
                json={
                    "message": "hola",
                    "session_id": session_id,
                    "context_module": module,
                    **fake_creds,
                },
            )
            check(f"routing[{module}]: HTTP 200", resp.status_code == 200)

            session_resp = await client.get(f"/agent/session/{session_id}")
            check(f"routing[{module}]: GET /agent/session/{{id}} HTTP 200", session_resp.status_code == 200)
            body = session_resp.json()
            check(
                f"routing[{module}]: active_specialist == '{module}'",
                body.get("active_specialist") == module,
            )
            check(
                f"routing[{module}]: handoff_reason mentions context_module hint",
                "context_module hint" in (body.get("handoff_reason") or ""),
            )


async def _check_routing_no_hint_fallback(client: httpx.AsyncClient):
    """Sin context_module -> fallback `.with_structured_output()` REAL (una
    sola llamada LLM, igual que Phase 5) -- prueba el camino completo desde
    el HTTP real, no solo el grafo desnudo."""
    fake_creds = {"tenant_base_url": "https://fake.test", "tenant_token": "fake-token"}
    session_id = f"routing-fallback-{uuid.uuid4()}"

    with patch(
        "core.application.agents.base.SpecialistAgent.ainvoke",
        new=AsyncMock(return_value=[{"role": "assistant", "content": "respuesta simulada"}]),
    ):
        resp = await client.post(
            "/agent/chat",
            json={
                "message": (
                    "Quiero registrar una retención a un proveedor por una "
                    "factura de compra que ya pagamos."
                ),
                "session_id": session_id,
                "context_module": None,
                **fake_creds,
            },
        )
    check("routing[no-hint]: HTTP 200", resp.status_code == 200)

    session_resp = await client.get(f"/agent/session/{session_id}")
    body = session_resp.json()
    check("routing[no-hint]: fallback classified as 'contabilidad'", body.get("active_specialist") == "contabilidad")
    check(
        "routing[no-hint]: handoff_reason mentions LLM classification",
        "LLM classification" in (body.get("handoff_reason") or ""),
    )


async def _check_draft_full_cycle(client: httpx.AsyncClient):
    """crear_preliminar_venta (DRAFT, no interrupt-gated) -> debe completar
    en un solo /agent/chat con status="answered", SIN awaiting_confirmation.
    Mockea SOLO la llamada HTTP real del adapter (SalesAdapter/build_client)
    -- el resto (routing, LLM real decidiendo invocar la tool, ejecución de
    la tool) es genuino, mismo patrón que Phase 5's interrupt test."""
    fake_creds = {"tenant_base_url": "https://fake.test", "tenant_token": "fake-token"}
    session_id = f"draft-cycle-{uuid.uuid4()}"

    from core.domain import SaleNote

    with patch("core.application.agents.tools.sales_tools.SalesAdapter") as MockAdapter, patch(
        "core.application.agents.tools.sales_tools.build_client"
    ) as mock_build_client:
        instance = MockAdapter.return_value
        instance.create_sale_note = AsyncMock(
            return_value=SaleNote(id=777, customer_id=42, total=118.0, number=None)
        )
        mock_build_client.return_value.aclose = AsyncMock()

        resp = await client.post(
            "/agent/chat",
            json={
                "message": (
                    "Creá un preliminar de venta para el cliente id=42, serie 10, "
                    "fecha 2026-06-24, con 1 unidad del producto id=704 a precio "
                    "100.00 con afectación 10. Ejecutá crear_preliminar_venta "
                    "directamente, no pidas mas confirmacion."
                ),
                "session_id": session_id,
                "context_module": "ventas",
                **fake_creds,
            },
        )

    check("draft-cycle: HTTP 200", resp.status_code == 200)
    if resp.status_code != 200:
        return
    body = resp.json()
    check("draft-cycle: status == 'answered' (draft never pauses)", body.get("status") == "answered")
    check("draft-cycle: confirmation is None", body.get("confirmation") is None)
    check("draft-cycle: answer mentions the mocked sale_note id (777)", "777" in (body.get("answer") or ""))


async def _check_decline_cycle_live(client: httpx.AsyncClient):
    """crear_compra (interrupt-gated) -> propose -> awaiting_confirmation ->
    POST /agent/confirm {approved: false} -> decline, NO real purchase
    created. Runs through the REAL live FacturadorPro7 sandbox (real
    Bearer token/base_url) for routing+LLM+interrupt/resume machinery, but
    `approved=False` means the adapter's real POST /api/purchases is NEVER
    reached (the tool returns its 'RECHAZADA' message before calling
    build_client/PurchasesAdapter at all) — confirmed by NOT mocking
    anything here: if a real POST somehow fired, it would either fail
    cleanly (missing item_snapshots resolved by the LLM) or create a
    real-but-declined-and-thus-impossible row, and either way this is
    exactly the safety rule the launch prompt requires (never approved=True
    for irreversible writes; decline tested live end-to-end)."""
    creds = _load_live_creds()
    session_id = f"decline-cycle-{uuid.uuid4()}"

    # Spy (NOT a behavior mock) sobre el método real que dispara el POST
    # /api/purchases — wraps el método REAL así que si en algún punto SÍ se
    # llamara (lo que nunca debería pasar tras approved=False), el assert
    # `assert_not_called()` de más abajo lo agarra con una garantía directa,
    # no solo infiriendo "no pasó" del texto de la respuesta del LLM.
    from adapters.facturadorpro7_api.purchases_adapter import PurchasesAdapter

    real_create_purchase = PurchasesAdapter.create_purchase
    create_purchase_spy = AsyncMock(wraps=real_create_purchase)

    with patch.object(PurchasesAdapter, "create_purchase", create_purchase_spy):
        resp = await client.post(
            "/agent/chat",
            json={
                "message": (
                    "Quiero registrar una compra de prueba. Primero buscá el "
                    "producto cuya descripción contiene 'TEST-AGENTE-IA-VERIFICACION' "
                    "(ya existe en el catálogo) y buscá el proveedor 'ABHER S.A.C.' "
                    "(ya existe). Con los IDs reales que encuentres, armá la compra: "
                    "documento tipo 01, serie F001, numero 000123, fecha 2026-06-24, "
                    "1 unidad a precio 10.00. No inventes IDs — usá los que te "
                    "devuelvan buscar_producto y buscar_proveedor."
                ),
                "session_id": session_id,
                "context_module": "compras",
                "tenant_base_url": creds["base_url"],
                "tenant_token": creds["token"],
            },
            timeout=60,
        )
    check("decline-cycle: /agent/chat HTTP 200", resp.status_code == 200)
    if resp.status_code != 200:
        print("decline-cycle /agent/chat body:", resp.text)
        return
    body = resp.json()

    if body.get("status") != "awaiting_confirmation":
        # El LLM podría no haber invocado crear_compra en el primer intento
        # (sensibilidad de prompt, ya observada en Phase 5) -- se reporta
        # como fallo explícito, no se silencia, porque el contrato HTTP de
        # design.md exige que un write interrupt-gated SIEMPRE pause.
        check(
            f"decline-cycle: status == 'awaiting_confirmation' (got {body.get('status')!r}, answer={body.get('answer')!r})",
            False,
        )
        return
    check("decline-cycle: status == 'awaiting_confirmation'", True)
    check("decline-cycle: confirmation present", body.get("confirmation") is not None)
    confirmation = body.get("confirmation") or {}
    check(
        "decline-cycle: confirmation.tool_name == 'crear_compra'",
        confirmation.get("tool_name") == "crear_compra",
    )

    with patch.object(PurchasesAdapter, "create_purchase", create_purchase_spy):
        confirm_resp = await client.post(
            "/agent/confirm",
            json={"session_id": session_id, "approved": False},
            timeout=60,
        )
    check("decline-cycle: /agent/confirm HTTP 200", confirm_resp.status_code == 200)
    if confirm_resp.status_code != 200:
        print("decline-cycle /agent/confirm body:", confirm_resp.text)
        return

    check(
        "decline-cycle: PurchasesAdapter.create_purchase NEVER called (no real POST /api/purchases)",
        create_purchase_spy.await_count == 0,
    )

    confirm_body = confirm_resp.json()
    check("decline-cycle: resumed status == 'answered'", confirm_body.get("status") == "answered")
    # NOTA: el tool `crear_compra` devuelve literalmente "Compra RECHAZADA
    # por el usuario..." (ver core/agents/tools/purchases_tools.py) como
    # ToolMessage, pero el LLM (último turno del loop acotado de
    # SpecialistAgent) PARAFRASEA ese ToolMessage en su respuesta final al
    # usuario — confirmado real: "la compra fue **rechazada**..." (minúscula,
    # markdown), no la cadena literal en mayúsculas. Esto es un
    # comportamiento esperado del LLM resumiendo una tool result, NO un bug
    # de la tool/orquestación — se verifica la palabra raíz "rechaz" en vez
    # de la cadena exacta para no acoplarse a la redacción exacta del LLM.
    answer_lower = (confirm_body.get("answer") or "").lower()
    check(
        "decline-cycle: answer mentions the decline (rechaz*)",
        "rechaz" in answer_lower,
    )

    session_resp = await client.get(f"/agent/session/{session_id}")
    session_body = session_resp.json()
    check(
        "decline-cycle: no pending_confirmation left after decline",
        session_body.get("pending_confirmation") is None,
    )


async def _check_lifespan_failure_isolation():
    """Re-encodes the manual lifespan-isolation proof done earlier in this
    apply batch as an automated check: the SAME app module, with
    `build_graph` patched to raise INSIDE a fresh `lifespan_context` run,
    must still: complete startup, answer /health with agent_available=False
    + the error message, answer /chat normally, and 503 on /agent/chat —
    never propagate the exception out of lifespan."""
    import entrypoints.api.main as main_module

    with patch("entrypoints.api.main.build_graph", side_effect=RuntimeError("PHASE7_AUTOMATED_BREAKAGE_TEST")):
        async with _RunningApp(main_module.app) as broken_client:
            health_resp = await broken_client.get("/health")
            check("lifespan-isolation: /health HTTP 200 even with broken build_graph", health_resp.status_code == 200)
            health_body = health_resp.json()
            check("lifespan-isolation: agent_available == False", health_body.get("agent_available") is False)
            check(
                "lifespan-isolation: agent_error mentions the injected failure",
                "PHASE7_AUTOMATED_BREAKAGE_TEST" in (health_body.get("agent_error") or ""),
            )

            chat_resp = await broken_client.post("/chat", json={"message": "hola, esto debe seguir funcionando"})
            check("lifespan-isolation: /chat HTTP 200 even with broken build_graph", chat_resp.status_code == 200)

            agent_resp = await broken_client.post(
                "/agent/chat",
                json={
                    "message": "hola",
                    "session_id": "lifespan-isolation-test",
                    "tenant_base_url": "https://fake.test",
                    "tenant_token": "fake",
                },
            )
            check("lifespan-isolation: /agent/chat returns 503 (not 500/crash)", agent_resp.status_code == 503)

    # Restore: re-run lifespan once more with the REAL build_graph so any
    # test executed after this one (none currently, but defensive) sees a
    # working graph again — confirms the SAME app/module recovers cleanly
    # once build_graph stops raising, not just that the broken run didn't
    # crash.
    async with _RunningApp(main_module.app) as restore_client:
        restore_resp = await restore_client.get("/health")
        check(
            "lifespan-isolation: app.state restored to working graph after re-running real lifespan",
            restore_resp.json().get("agent_available") is True,
        )


async def _run_all():
    from entrypoints.api.main import app

    async with _RunningApp(app) as client:
        await _check_routing_all_modules(client)
        await _check_routing_no_hint_fallback(client)
        await _check_draft_full_cycle(client)
        await _check_decline_cycle_live(client)

    await _check_lifespan_failure_isolation()


def main():
    asyncio.run(_run_all())

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
