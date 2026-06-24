# Tasks: Multi-agent ERP co-pilot for FacturadorPro7

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 3200-4200 (≈30 new files: 8 ports+entities additive in 2 files, 8 adapters, ~8 tool modules covering ~25 tools, 5 agents+base, 4 orchestration files, 1 router, 2 modified entrypoint files, requirements.txt, smoke test, integration tests) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (smoke gate) → PR 2 (domain/ports/http/auth) → PR 3 (8 adapters) → PR 4 (tools) → PR 5 (5 specialist agents) → PR 6 (orchestration) → PR 7 (entrypoint wiring + integration tests) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | qwen-plus tool-calling + async `interrupt()` smoke test | PR 1 | Throwaway echo tool + trivial interrupt tool; ~100-150 lines; GATE — must pass before Unit 3+ |
| 2 | Domain entities + 8 async port interfaces + http_client/auth | PR 2 | `core/domain.py`, `core/ports.py` (additive), `adapters/facturadorpro7_api/{http_client,auth}.py`; manual smoke test vs `desa.facturadorpro7.test`; ~400-500 lines |
| 3 | 8 adapters (Items, Inventory, Customers, Suppliers, Sales, Purchases, Dispatch, Finance) | PR 3 | One commit per adapter inside the PR; ~600-800 lines; can split into 2 PRs if reviewer load is a concern |
| 4 | ~25 tools across 8 tool modules with Pydantic schemas + credential injection | PR 4 | `core/agents/tools/*`; ~700-900 lines |
| 5 | 5 specialist agents + base | PR 5 | `core/agents/base.py` + 5 agent files; built in order Inventario→Ventas→Compras→Logística→Contabilidad; ~400-500 lines |
| 6 | Orchestration: state, confirmation, supervisor, graph | PR 6 | `core/orchestration/*.py`; ~400-500 lines |
| 7 | Entrypoint wiring + integration tests + requirements.txt | PR 7 | `agent_router.py`, `schemas.py`, `main.py` mods, integration test suite, `/chat` regression check; ~500-700 lines |

Each unit above is independently mergeable, has a clear verification step (smoke test, manual adapter call, unit test, or e2e flow), and a rollback boundary (delete the new folder/file; PR 7's `main.py` change is the only one touching existing code and reverts with one line removal).

## Phase 0: Smoke Test Gate (blocking — do not skip)

- [x] 0.1 Write throwaway async echo tool bound to existing `ChatOpenAI`/`OpenAICompatibleAdapter`, call `.bind_tools()`, confirm `tool_calls` appear in qwen-plus response — PASSED, see `scripts/spike_smoke_test_toolcalling.py`
- [x] 0.2 Write throwaway async tool that calls `interrupt()`, confirm full cycle: `invoke` → pending → `Command(resume=...)` → final result — PASSED, same script
- [x] 0.3 If qwen-plus tool-calling fails: swap model to `qwen-max` or `gpt-4o-mini` via config only, re-run 0.1/0.2 — do not proceed to Phase 1 until both pass — N/A: qwen-plus passed both checks on first run, no fallback needed
- [x] 0.4 Delete throwaway smoke-test files once gate passes (or keep as `tests/smoke/test_tool_calling_interrupt.py` if useful long-term) — kept as `scripts/spike_smoke_test_toolcalling.py` (useful as a regression spike if the LLM model/provider ever changes)

## Phase 1: Domain + Ports + HTTP Foundation

- [ ] 1.1 Add ERP entities to `core/domain.py` (additive): `Item`, `ItemDraft`, `Category`, `Brand`, `StockTxn`, `StockMovement`, `Customer`, `Supplier`, `SaleNote`, `Cpe`, `Purchase`, `Dispatch`, `DispatchTables`, `Retention`, `Perception`, `Cash`, `Report`
- [ ] 1.2 Add 8 async port ABCs to `core/ports.py` (additive): `ItemsPort`, `InventoryPort`, `CustomersPort`, `SuppliersPort`, `SalesPort`, `PurchasesPort`, `DispatchPort`, `FinancePort` — per interfaces in design.md
- [ ] 1.3 Create `adapters/facturadorpro7_api/auth.py` with `TenantCredentials(base_url, token)` dataclass
- [ ] 1.4 Create `adapters/facturadorpro7_api/http_client.py` with `FacturadorPro7Client(creds)` — per-request instantiation, never global/singleton, Bearer auth, no token logging
- [ ] 1.5 Manual smoke test: instantiate `FacturadorPro7Client` against `desa.facturadorpro7.test` with a real dev token, confirm a basic authenticated GET succeeds

## Phase 2: Adapters (one at a time, against dev tenant)

- [ ] 2.1 `items_adapter.py` implementing `ItemsPort` (search via `/api/document/search-items`, create via `POST /api/item`) — manual call against dev tenant
- [ ] 2.2 `inventory_adapter.py` implementing `InventoryPort` (`/api/items/records`, `/record/{id}`, `/items/update`, `/change-active`, `/change-favorite`, `/categories`, `/brands-records`, `/inventory/transaction`) — manual call against dev tenant
- [ ] 2.3 `customers_adapter.py` implementing `CustomersPort` (`GET /api/document/search-customers`) — manual call against dev tenant
- [ ] 2.4 `suppliers_adapter.py` implementing `SuppliersPort` (`GET /api/purchases/search-suppliers`) — manual call against dev tenant
- [ ] 2.5 `sales_adapter.py` implementing `SalesPort` (`POST /api/sale-note`, `POST /api/sale-note/{id}/generate-cpe`) — manual call against dev tenant
- [ ] 2.6 `purchases_adapter.py` implementing `PurchasesPort` (`POST /api/purchases`) — manual call against dev tenant
- [ ] 2.7 `dispatch_adapter.py` implementing `DispatchPort` (`/dispatches/tables`, `POST /dispatches`, `POST /dispatches/send`, `GET /dispatches/records`) — verify required fields via `GET /api/dispatches/tables` first, use `extra: dict` escape per design open question
- [ ] 2.8 `finance_adapter.py` implementing `FinancePort` (`/retentions`, `/perceptions`, `/cash/open`, `/cash/close/{cash}`, `GET /report`, `POST /reports/general-sale`) — inspect Laravel controller or force a 422 in sandbox to resolve undocumented `/retentions`/`/perceptions` schema and `/cash/open` required fields before finalizing

## Phase 3: Tools (Pydantic schemas, explicit — not docstring-inferred)

- [ ] 3.1 `core/agents/tools/items_tools.py`: `buscar_producto`, `crear_producto` (shared Compras+Ventas subset)
- [ ] 3.2 `core/agents/tools/inventory_tools.py`: `obtener_producto`, `actualizar_producto`, `activar_o_desactivar_producto`, `marcar_favorito`, `listar_categorias`, `listar_marcas`, `registrar_movimiento_stock` (interrupt)
- [ ] 3.3 `core/agents/tools/customers_tools.py`: `buscar_cliente`
- [ ] 3.4 `core/agents/tools/suppliers_tools.py`: `buscar_proveedor`
- [ ] 3.5 `core/agents/tools/sales_tools.py`: `crear_preliminar_venta` (no interrupt), `confirmar_y_generar_cpe` (interrupt)
- [ ] 3.6 `core/agents/tools/purchases_tools.py`: `crear_compra` (interrupt)
- [ ] 3.7 `core/agents/tools/dispatch_tools.py`: `obtener_tablas_despacho`, `crear_guia_remision` (no interrupt), `enviar_guia_sunat` (interrupt), `listar_guias_remision`
- [ ] 3.8 `core/agents/tools/finance_tools.py`: `crear_retencion`, `crear_percepcion`, `abrir_caja`, `cerrar_caja` (all interrupt), `reporte_del_dia`, `reporte_general_ventas`
- [ ] 3.9 Verify every tool receives `TenantCredentials` via `config.configurable`/`InjectedToolArg`/`get_config()` — never as a normal parameter; confirm no token/base_url field appears in any tool's serialized JSON schema

## Phase 4: Specialist Agents (build order: Inventario → Ventas → Compras → Logística → Contabilidad)

- [ ] 4.1 `core/agents/base.py`: `SpecialistAgent` — system prompt assembly + `bind_tools` + bounded loop
- [ ] 4.2 `core/agents/inventario_agent.py` — wires `inventory_tools` + `items_tools` subset
- [ ] 4.3 `core/agents/ventas_agent.py` — wires `sales_tools`, `customers_tools`, `items_tools`
- [ ] 4.4 `core/agents/compras_agent.py` — wires `purchases_tools`, `suppliers_tools`, `items_tools` (proves shared `ItemsPort` across two agents)
- [ ] 4.5 `core/agents/logistica_agent.py` — wires `dispatch_tools`
- [ ] 4.6 `core/agents/contabilidad_agent.py` — wires `finance_tools`

## Phase 5: Orchestration

- [ ] 5.1 `core/orchestration/state.py`: `AgentState` TypedDict (`messages` w/ `add_messages`, `context_module`, `active_specialist`, `session_id`, `pending_confirmation`, `handoff_reason`) — no credential field
- [ ] 5.2 `core/orchestration/confirmation.py`: `require_confirmation(tool_name, tool_args, summary)` wrapping `interrupt()`
- [ ] 5.3 `core/orchestration/supervisor.py`: `context_module` fast-path (no LLM) + `.with_structured_output()` fallback over `Literal` of 5 module names
- [ ] 5.4 `core/orchestration/graph.py`: `StateGraph` wiring supervisor + 5 specialist nodes, all routing to `END`, no direct specialist-to-specialist edges; compile with `InMemorySaver`

## Phase 6: Entrypoint Wiring

- [ ] 6.1 `entrypoints/api/schemas.py`: add `AgentChatRequest`/`AgentChatResponse`, `ConfirmationPayload` (additive, no changes to existing schemas)
- [ ] 6.2 `entrypoints/api/agent_router.py`: `POST /agent/chat`, `POST /agent/confirm`, `GET /agent/session/{id}` per HTTP contract in design.md
- [ ] 6.3 `entrypoints/api/main.py`: `include_router(agent_router)`, compile graph once in existing lifespan — verify zero changes to `/chat` route registration
  - [ ] 6.3a MANDATORY: wrap graph compilation in `try/except` inside `lifespan()`, AFTER the existing `chatbot = ChatbotService(...)` line. On exception: set `app.state.agent_graph = None` + `app.state.agent_error = str(exc)`, log, do NOT re-raise — `yield` must always execute. See design.md "Lifespan failure isolation" decision. Verify manually: force a bad tool schema, confirm `/chat` and `/health` still respond, `/agent/chat` returns 503.
- [ ] 6.4 Update `requirements.txt`: declare `langgraph`, `langchain-core`, `langchain-openai`, `httpx` pinned to the EXACT versions verified in the Phase 0 smoke test (`==`, not ranges) — see design.md "New-dependency pinning" decision

## Phase 7: Integration Tests / Verification

- [ ] 7.1 Per-specialist e2e test: propose → `awaiting_confirmation` → `POST /agent/confirm` → real write verified in FacturadorPro7 (API or UI) — one test per specialist (5 total)
- [ ] 7.2 Decline-path tests: `crear_compra`, `registrar_movimiento_stock`, `abrir_caja` declined via `approved: false` — confirm no POST issued (per confirmation-flow spec scenarios)
- [ ] 7.3 Routing test: 5 requests with each valid `context_module` value route to the correct specialist with no LLM call; 1 request with no `context_module` exercises the structured-output fallback; 1 request with an invalid value (`"marketing"`) also falls back
- [ ] 7.4 Credential-leak tests: assert no tool's JSON schema contains a token/base_url field; assert no Bearer token appears in logs for a completed request; assert checkpointer's stored state contains no tenant credential
- [ ] 7.5 Regression test: existing `/chat` endpoint full request/response contract unchanged after `agent_router` is registered
- [ ] 7.6 Rollback test: remove `include_router` call, restart, confirm `/chat` still works and `/agent/*` routes are gone
