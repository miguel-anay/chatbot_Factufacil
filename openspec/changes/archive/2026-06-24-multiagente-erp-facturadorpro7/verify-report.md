# Verification Report: multiagente-erp-facturadorpro7

**Mode**: hybrid (openspec files + Engram)
**Verdict**: PASS WITH WARNINGS
**Verified**: 2026-06-24, against `main` (commit `86636a3`, all 7 PRs merged)

## Test Execution Evidence

- Booted real server (`PYTHONPATH=. uvicorn entrypoints.api.main:app`, port 8000), `/health` returned `agent_available: true`, `agent_error: null` — graph compiled cleanly on the final merged state.
- `python test_chatbot.py` (regression suite for the pre-existing `/chat` endpoint): **14/14 passed**, live, against the real server.
- Smoke-tested `POST /agent/chat` with `context_module: "inventario"`: HTTP 200, routed to Inventario specialist, returned a Spanish system response — confirms the HTTP contract and routing fast-path work end-to-end on `main`.
- Programmatic schema inspection (`tool.tool_call_schema.model_json_schema()`) on `crear_compra` and `abrir_caja`: zero credential-related fields in either schema — confirms the credential-injection claim with code execution, not just visual inspection.

## Completeness Table (tasks.md, Phases 0-7)

| Phase | Status | Notes |
|---|---|---|
| 0 — Smoke gate | Complete | qwen-plus tool-calling + interrupt cycle passed first try |
| 1 — Domain/Ports/HTTP | Complete | additive, verified |
| 2 — Adapters (+follow-up) | Complete, with documented open risks | 3 genuine unresolved server-side gaps (retentions/dispatch/sale-note), all explicitly flagged, time-boxed |
| 3 — Tools | Complete | 213/213 checks per task notes |
| 4 — Specialist agents | Complete | 45/45 checks; later corrected in PR7 (see CRITICAL-resolved below) |
| 5 — Orchestration | Complete | 22/22 checks |
| 6 — Entrypoint wiring | Complete | lifespan isolation live-verified twice |
| 7 — Integration tests | Complete, scoped deviations documented | 42/42 checks, several scenarios scoped down with explicit rationale (no live irreversible writes except 1 decline path) |

All 7 PRs confirmed merged to `main` via `git log` (`798fae9`→`86636a3`, with squash-merge PR commits `#1`-`#8`).

## Spec Compliance Matrix

| Spec Requirement | Status | Evidence |
|---|---|---|
| Context module fast-path routing | PASS | `supervisor.py::supervisor_node` — `VALID_MODULES` check, no LLM call when hinted; live-tested `/agent/chat` with `context_module: "inventario"` |
| LLM fallback classification | PASS | `supervisor.py::_classify_with_llm`, `.with_structured_output(RouteDecision)` over 5-value `Literal` |
| No direct specialist-to-specialist edges | PASS | `graph.py::build_graph` — every specialist `add_edge(name, END)`, no other edges added |
| Credentials excluded from routing state | PASS | `state.py::AgentState` has no credential field; `supervisor_node` only reads `context_module`/`messages` |
| **Irreversible writes require confirmation** | **CRITICAL — see below** | Spec's named "irreversible set" (12 tools) does not match the 8 tools actually gated by `interrupt()` |
| Draft writes bypass confirmation | PASS | `crear_preliminar_venta`, `crear_guia_remision` confirmed — no `interrupt()` call, immediate POST |
| Resume continues inside the same tool call | PASS | `confirmation.py::build_resume_command` + all 8 write-tools read `decision.get("approved")` after `interrupt()` |
| Pending confirmation survives process lifetime only | PASS | `InMemorySaver`, documented in `graph.py`/`agent_router.py` |
| Per-domain tool coverage (endpoint table) | PASS | spot-checked against `purchases_tools.py`, `finance_tools.py`, `dispatch_tools.py`, `sales_tools.py`, `inventory_tools.py`, `items_tools.py` — endpoints match |
| Single auth-aware HTTP client | PASS | `_shared.py::build_client` constructs `FacturadorPro7Client` per-request from injected creds, every adapter receives it |
| Credentials per-request, never persisted | PASS | `InjectedConfig` (`InjectedToolArg`) excludes `config` from JSON schema — confirmed programmatically; `AgentState` has no creds field; `_PENDING_CREDS` is in-process only, never logged/persisted |
| Agent Chat Endpoint Contract | PASS | `schemas.py::AgentChatResponse` matches; live-tested |
| Agent Confirm Endpoint Contract | PASS | `agent_router.py::agent_confirm` — unknown session_id returns 404 (error), never executes a write |
| Session State Read Endpoint | PASS | `agent_router.py::agent_session` uses `graph.aget_state()`, no mutation |
| Existing /chat endpoint unmodified | PASS | `git diff f6e58ef HEAD -- core/chatbot_service.py` is empty; `/chat` handler body untouched in `main.py` diff; live 14/14 regression pass |

## Design Coherence Table

| Design Decision | Code Match | Notes |
|---|---|---|
| Hexagonal boundary (`core/domain.py`/`core/ports.py` zero LangChain/LangGraph imports) | PASS | Confirmed via `rg` — only a comment mentions LangGraph, no import. `core/agents/*` and `core/orchestration/*` correctly do import them. |
| Confirmation placement (`interrupt()` inline, no wrapper) | PASS | `confirmation.py` correctly has no `require_confirmation()`; all 8 write tools call `interrupt()` as first statement before POST |
| Credential injection via `config.configurable` | PASS | Confirmed via code read + programmatic schema check |
| Routing 5-module Literal (post-Phase-4 correction) | PASS | `state.py::SpecialistModule`, `supervisor.py::RouteDecision.module` both use the corrected 5-value Literal |
| Lifespan failure isolation | PASS | `main.py::lifespan` — try/except wraps `build_graph()`, placed after `chatbot = ChatbotService(...)`, never re-raises, `yield` unconditional |
| New-dependency exact pinning | PASS | `langgraph==1.2.0`, `langchain-core==1.4.0`, `langchain-openai==1.2.1`, `httpx==0.28.1` all exact pins in `requirements.txt` |
| Agent prompt directive confirmation wording (PR7 fix) | PASS | All 5 agent files (`inventario`/`ventas`/`compras`/`logistica`/`contabilidad`) contain "LLAMÁ a la tool DIRECTAMENTE ... NUNCA le pidas confirmación... por chat antes de invocarla" |
| Zero regression on `core/chatbot_service.py`, 3 sync ports, FAISSAdapter, WindowMemoryAdapter, `/chat` route | PASS | Confirmed via `git log`/`git diff` per-file; `faiss_adapter.py` only touched by unrelated pre-existing bugfix commit |

## Issues

### CRITICAL

**1. `erp-confirmation-flow/spec.md`'s documented "irreversible set" does not match the actual `interrupt()`-gated tool set in code.**

The spec states: *"The irreversible set is: `crear_producto`, `actualizar_producto`, `activar_o_desactivar_producto`, `marcar_favorito`, `registrar_movimiento_stock`, `crear_compra`, `confirmar_y_generar_cpe`, `enviar_guia_sunat`, `crear_retencion`, `crear_percepcion`, `abrir_caja`, `cerrar_caja`"* — 12 tools.

The actual code (`core/agents/tools/items_tools.py`, `inventory_tools.py`) implements **no `interrupt()` call** for `crear_producto`, `actualizar_producto`, `activar_o_desactivar_producto`, or `marcar_favorito`. Only `registrar_movimiento_stock` is interrupt-gated within `inventory_tools.py`; `items_tools.py` has zero interrupt-gated tools. The code's own docstrings explicitly state the opposite of the spec ("No es un movimiento financiero ni un paso ante SUNAT — no requiere confirmación humana").

This means only 8 of the 12 spec-named tools are actually interrupt-gated. The 8 that ARE gated (`crear_compra`, `confirmar_y_generar_cpe`, `enviar_guia_sunat`, `crear_retencion`, `crear_percepcion`, `abrir_caja`, `cerrar_caja`, `registrar_movimiento_stock`) are exactly the ones independently verified correct in this report and match every other artifact's framing of "the 8 write tools." But `erp-confirmation-flow/spec.md` itself was never updated to drop `crear_producto`/`actualizar_producto`/`activar_o_desactivar_producto`/`marcar_favorito` from its irreversible-set sentence, and — unlike every other deviation in this change (confirmation placement, 5-module Literal, lifespan isolation) — this narrowing was never called out as an explicit, reasoned deviation in `design.md`'s Architecture Decisions table. `tasks.md` line 74 silently reflects the narrower scope (only `registrar_movimiento_stock` tagged "(interrupt)") without flagging the spec mismatch.

Practical risk: a tenant user could create a catalog product, deactivate/activate it, or change its favorite flag with zero human-in-the-loop confirmation, even though the formal spec for this change states those 4 actions require confirmation. Whether the code's narrower judgment call (catalog metadata writes aren't financially/SUNAT irreversible) is the *right* call is plausible — but the spec document was never reconciled with it, so anyone reading `erp-confirmation-flow/spec.md` today is misled about actual system behavior.

**Recommendation**: Either (a) update `erp-confirmation-flow/spec.md`'s irreversible-set list to the 8 actually-gated tools and add an explicit Architecture Decision row in `design.md` documenting why `crear_producto`/`actualizar_producto`/`activar_o_desactivar_producto`/`marcar_favorito` were excluded, or (b) if the broader scope was actually intended, add `interrupt()` to those 4 tools. This is a spec/code reconciliation issue, not necessarily a code bug — but it must be resolved before archive, since `sdd-archive` would otherwise certify a spec that misdescribes shipped behavior.

## WARNINGS (re-confirmed open risks — not new, accurately described in design.md/tasks.md)

1. `/api/retentions` — `crear_retencion` genuinely unresolved gap: minimal `totales`-only body without `documentos` hits a deep XML-generation foreach error. Code treats `documentos` as required in its Pydantic schema as a mitigation; still time-boxed/unresolved end-to-end. Still accurately described.
2. `/api/dispatches` — both transport modes (`01`/`02`) require an undocumented nested `driver`/`dispatcher` object not resolvable via any lookup endpoint. Modeled as an `extra: dict` escape hatch in `crear_guia_remision`'s schema, explicitly documented as a known limitation, not silently swallowed. Still accurately described.
3. `create_sale_note()` — server-side `total` is a computed aggregate the bare API never computes; mitigated in the tools layer via `compute_igv_breakdown()`, but the deeper "is `mergeData()` assuming a richer web UI flow" question remains genuinely open. Still accurately described.
4. `registrar_movimiento_stock`'s `inventory_transaction_id` is a tenant-configured FK with no listing endpoint — tool exposes it as an explicit caller-supplied parameter rather than hardcoding, documented as an open risk for the agent/user to resolve. Still accurately described.
5. `requirements.txt` still has loose ranges for `langchain`/`langchain-community` (pre-existing, not introduced by this change, explicitly called out in design.md as a "pre-existing latent version of this same risk").
6. `_PENDING_CREDS` in-process dict in `agent_router.py` is an undocumented-in-original-task-text design addition (now documented inline) with the same lifetime/risk profile as `InMemorySaver` — lost on restart. Acceptable for dev, flagged for production hardening alongside the checkpointer migration.
7. Phase 7 test scope reductions (5.x sub-bullets in tasks.md) — e.g. only 1 of 8 interrupt-gated tools got a live decline-path HTTP test, only 1 draft tool got a full live cycle — are explicitly scoped and reasoned (never execute irreversible writes for real except the one decline path), not silent gaps.

## SUGGESTIONS

1. Consider adding a single automated test (even a static AST/source-grep check) that asserts the `erp-confirmation-flow` spec's irreversible-set list and the actual `interrupt()` call-sites in `core/agents/tools/*.py` stay in sync — would have caught the CRITICAL finding above automatically.
2. `core/agents/tools/items_tools.py` and `inventory_tools.py`'s docstrings already articulate the design rationale for excluding catalog-metadata writes from confirmation — promoting that rationale into `design.md`'s Architecture Decisions table (not just module docstrings) would have prevented the spec/code drift.
3. Minor: `agent_router.py`'s `_PENDING_CREDS` dict has no eviction/TTL — long-running dev processes will accumulate stale entries for abandoned sessions. Not a correctness issue (bounded by `InMemorySaver`'s own process-lifetime limitation) but worth a follow-up note before any production hardening pass.

## Conclusion

The implementation is materially faithful to the design and largely faithful to the specs. All 7 PRs are confirmed merged and live on `main`; the safety-critical propose-confirm mechanism, hexagonal boundary, credential isolation, and lifespan failure isolation are all verified by direct code inspection and live execution, not just by trusting prior reports. `test_chatbot.py` passes 14/14 live against the final state.

One CRITICAL finding blocks a clean archive: `erp-confirmation-flow/spec.md`'s stated "irreversible set" (12 tools) contradicts the actual 8-tool `interrupt()` coverage in code, and this gap was never reconciled or documented as an explicit deviation the way every other design departure in this change was. This should be resolved (spec update + design.md deviation note, or code change to add the missing 4 `interrupt()` calls) before `sdd-archive`, since archiving now would certify a spec that misdescribes shipped behavior on a security/safety-relevant requirement.

All previously-known open risks (retentions/dispatch/sale-note server gaps, inventory_transaction_id FK, loose langchain ranges) remain accurately described as WARNINGS, not new findings.
