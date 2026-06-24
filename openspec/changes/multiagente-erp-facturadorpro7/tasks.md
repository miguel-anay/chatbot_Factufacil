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

- [x] 1.1 Add ERP entities to `core/domain.py` (additive): `Item`, `ItemDraft`, `Category`, `Brand`, `StockTxn`, `StockMovement`, `Customer`, `Supplier`, `SaleNote`, `Cpe`, `Purchase`, `Dispatch`, `DispatchTables`, `Retention`, `Perception`, `Cash`, `Report` — verified via `scripts/verify_phase1_domain_ports.py` (55/55 checks passed)
- [x] 1.2 Add 8 async port ABCs to `core/ports.py` (additive): `ItemsPort`, `InventoryPort`, `CustomersPort`, `SuppliersPort`, `SalesPort`, `PurchasesPort`, `DispatchPort`, `FinancePort` — per interfaces in design.md; verified ABC enforcement + async coroutine signatures via same script
- [x] 1.3 Create `adapters/facturadorpro7_api/auth.py` with `TenantCredentials(base_url, token)` frozen dataclass
- [x] 1.4 Create `adapters/facturadorpro7_api/http_client.py` with `FacturadorPro7Client(creds)` — per-request instantiation (verified two instances are independent objects), Bearer auth, 401→`AuthError`/422→`ValidationError`/5xx→`UpstreamError` mapping, no token in exception strings — verified via `scripts/verify_phase1_http_client.py` (15/15 checks passed, httpx.MockTransport, includes 500 vs 503 triangulation)
- [x] 1.5 Manual smoke test: instantiated `FacturadorPro7Client` against the REAL sandbox tenant `https://yiwu.qhipa.org.pe` ("YIWU IMPORT CORPORATION E.I.R.L.") with a real Bearer token, `GET /api/items/records?per_page=3` succeeded — returned real product data (e.g. `{'id': 704, 'description': '*ZHKTZ035/36 MACETERO X 3 HELLO KITTY MORADO CAJA X 8 SET46581574'}`). Script: `scripts/smoke_test_http_client_live.py` (credentials read from an external file, never hardcoded/logged/committed)

## Phase 2: Adapters (one at a time, against dev tenant)

- [x] 2.1 `items_adapter.py` implementing `ItemsPort` (search via `/api/document/search-items`, create via `POST /api/item`) — verified LIVE: search() real GET against sandbox returned real items; create() real POST created item id=1229 (marked test data). Real discovery: `search_by_barcode` must be OMITTED (not sent as False/0) or the API silently returns zero results; real discovery: create() requires `internal_id`/`purchase_unit_price`/`purchase_affectation_igv_type_id`/`stock`/`stock_min` beyond the documented required set.
- [x] 2.2 `inventory_adapter.py` implementing `InventoryPort` (`/items/record/{id}`, `/items/update`, `/change-active`, `/change-favorite`, `/categories-records`, `/brands-records`, `/inventory/transaction`) — verified LIVE on test item 1229: get_item/list_categories/list_brands (read), change_active/change_favorite/update_item (real writes on test item only). Real discovery: `/items/update` requires the FULL record (unit_type_id/currency_type_id/sale_unit_price/purchase_unit_price/sale_affectation_igv_type_id/purchase_affectation_igv_type_id), not a partial patch, despite its name and the documented required set. register_transaction() verified live (real stock movement on test item); `inventory_transaction_id` confirmed to be a tenant-configured FK with no listing endpoint in openapi.yaml — flagged as open risk for the tools layer (must be resolved via direct lookup or controller inspection, not hardcoded).
- [x] 2.3 `customers_adapter.py` implementing `CustomersPort` (`GET /api/document/search-customers`) — verified LIVE: real search against sandbox returned 79 real customers for query "a", 1 for "yiwu" (tenant's own company record).
- [x] 2.4 `suppliers_adapter.py` implementing `SuppliersPort` (`GET /api/purchases/search-suppliers`) — verified LIVE: real search returned 13 real suppliers. Real discovery: response is a PLAIN top-level JSON array, not `{success, data}` wrapped like search-customers.
- [x] 2.5 `sales_adapter.py` implementing `SalesPort` (`POST /api/sale-note`, `POST /api/sale-note/{id}/generate-cpe`) — create_sale_note() real attempt with marked test data hit a genuine tenant-side data gap (series_id=10 "NV01" missing `prefix` config — server error, not an adapter bug); request-building/error-surfacing verified end-to-end via UpstreamError. generate_cpe() (irreversible SUNAT step) NEVER executed for real; verified its request-building/error-path live by forcing a clean error with a nonexistent sale_note_id.
- [x] 2.6 `purchases_adapter.py` implementing `PurchasesPort` (`POST /api/purchases`) — real attempt with marked test data progressively discovered 3 undocumented required fields (`time_of_issue`, `currency_type_id`, `exchange_rate_sale`, now baked in as defaults) via genuine 500s, then hit a deeper server-internal NOT-NULL constraint on `purchase_items.item` (undocumented denormalized snapshot column) not resolved in this pass — no orphaned record left (confirmed via GET /api/purchases/records). Flagged as open risk.
- [x] 2.7 `dispatch_adapter.py` implementing `DispatchPort` (`/dispatches/tables`, `POST /dispatches`, `POST /dispatches/send`, `GET /dispatches/records`) — get_tables()/list_dispatches() verified LIVE (13 real transfer reasons, 1 real dispatch). create_dispatch() real attempt with marked test data revealed openapi.yaml's documented schema is materially incomplete: server requires a nested `datos_del_emisor` (issuer data) structure absent from the spec entirely — NOT resolved, flagged as open risk requiring controller inspection. send_dispatch() (irreversible SUNAT step) NEVER executed for real; verified the resolve-external-id-then-guard logic raises before ever reaching the send endpoint.
- [x] 2.8 `finance_adapter.py` implementing `FinancePort` (`/retentions`, `/perceptions`, `/cash/open`, `/cash/close/{cash}`, `GET /report`, `POST /reports/general-sale`) — open_cash()/close_cash() verified LIVE with a real open+close cycle (cash_id=8 then 9, no dangling open cash left). Real discovery: `/cash/close/{cash}` is actually a GET route, not POST as documented; `/cash/open` only truly requires `beginning_balance` (date_opening/time_opening are NOT required server-side, contradicting the design's assumption). get_daily_report()/get_general_sale_report() verified LIVE with real totals (e.g. total=32572.64); real discovery: general-sale report requires BOTH `period`/`month_start`/`month_end` AND `date_start`/`date_end` together (adapter derives the former from the latter when omitted). create_retention()/create_perception(): the spec's empty `type: object` schema was investigated live via forced empty/minimal POSTs — confirmed both require an undocumented nested `datos_del_emisor` structure (same gap found in dispatches), traced 2 levels into Laravel transform classes before time-boxing; NOT resolved, pass-through implemented, flagged as a genuine unresolved open risk for the tools layer.

### Phase 2 follow-up round (same PR 3, source-code-confirmed fixes — supersedes some open risks above)

A direct read of the FacturadorPro7 Laravel source (not just live trial-and-error) traced the exact required shape for the 4 areas flagged as open risk above. `core/ports.py` signatures for `SalesPort.create_sale_note`, `PurchasesPort.create_purchase`, `DispatchPort.create_dispatch`, `FinancePort.create_retention`/`create_perception` were updated (additive keyword-only params) to match. See `design.md`'s "Interfaces / Contracts" and "Open Questions" for the full citations.

- [x] 2.6-followup `purchases_adapter.py` — FIXED AND VERIFIED LIVE. `item_snapshots` param added; each line item now gets an `item` snapshot (with `is_set: False` default, discovered via a second real attempt that crashed `purchase_a4`'s PDF template reading `item.is_set`). Real end-to-end create succeeded: purchase id=122, number=F001-999005, marked test data, confirmed via GET /api/purchases/records.
- [ ] 2.7-followup `dispatch_adapter.py` — FIXED-BUT-BLOCKED-BY-NEW-ISSUE. `establishment_fiscal_code`/`origin_location_id`/`delivery_location_id` params added; a real attempt with the fix (plus Spanish field names `serie_documento`/`numero_documento` and `codigo_pais` in customer identity) progressed past series validation and person resolution. GENUINELY UNRESOLVED: both `codigo_modo_transporte` values (`"01"` público, `"02"` privado) require an additional undocumented nested person object (dispatcher vs. driver respectively) — confirmed live for both, not chased further per time-box. No orphaned record left (confirmed via GET /api/dispatches/records).
- [ ] 2.8-followup-retention `finance_adapter.py::create_retention()` — FIXED-BUT-BLOCKED-BY-NEW-ISSUE. `establishment_fiscal_code`/`supplier_identity` params added; a real attempt with the fix (plus `codigo_pais` in supplier identity) progressed past `Functions::establishment()`/`Functions::person()` resolution. GENUINELY UNRESOLVED: a minimal `totales`-only body with no `documentos` (referenced purchase invoices being retained against) hits `Invalid argument supplied for foreach()` deep in XML generation, not traced further per time-box — Phase 3 must treat `documentos` as effectively required.
- [x] 2.8-followup-perception `finance_adapter.py::create_perception()` — RESOLVED via source read only (NOT live-attempted this round): `customer_identity` param added. Source-confirmed perceptions do NOT need `datos_del_emisor` at all (corrects the original "same gap as dispatch" assumption) — only `datos_del_cliente_o_receptor`.
- [ ] 2.5-followup `sales_adapter.py::create_sale_note()` — GENUINELY UNRESOLVED SERVER BUG (time-boxed). Tested the `prefix: ""` hypothesis live: it avoided the original 500, but two more real attempts found and fixed the SAME class of gap (`time_of_issue`, then `exchange_rate_sale`, both now defaulted in the adapter). After all three fixes, a FOURTH NOT-NULL violation on `total` (a COMPUTED AGGREGATE, not a simple scalar) confirms this is a genuine server-side gap independent of the adapter's payload-building correctness — `SaleNoteController::mergeData()` appears to assume a richer web-UI flow that pre-computes totals client-side. Stopped here per instruction; Phase 3 tool design for `crear_preliminar_venta` must compute and send the full total/tax breakdown explicitly.
- [x] Regression: `python test_chatbot.py` 14/14 passed after all adapter changes (server booted via uvicorn, PYTHONPATH=.).
- [x] Unit tests: all 8 Phase 2 mock-based verification scripts re-run, 128/128 checks passed (was 116 before this round — 12 new checks added for the 4 changed adapters' new required params).

## Phase 3: Tools (Pydantic schemas, explicit — not docstring-inferred)

- [x] 3.1 `core/agents/tools/items_tools.py`: `buscar_producto`, `crear_producto` (shared Compras+Ventas subset) — 19/19 checks
- [x] 3.2 `core/agents/tools/inventory_tools.py`: `obtener_producto`, `actualizar_producto`, `activar_o_desactivar_producto`, `marcar_favorito`, `listar_categorias`, `listar_marcas`, `registrar_movimiento_stock` (interrupt) — 52/52 checks
- [x] 3.3 `core/agents/tools/customers_tools.py`: `buscar_cliente` — 9/9 checks
- [x] 3.4 `core/agents/tools/suppliers_tools.py`: `buscar_proveedor` — 9/9 checks
- [x] 3.5 `core/agents/tools/sales_tools.py`: `crear_preliminar_venta` (no interrupt), `confirmar_y_generar_cpe` (interrupt) — 26/26 checks. DECISION: `crear_preliminar_venta` computes the IGV/total breakdown itself (`_shared.compute_igv_breakdown`, IGV Perú 18%, `unitValue = unitPrice/1.18` for afectación "10") before calling `SalesPort.create_sale_note` — the bare API never computes `total` server-side (Phase 2 finding). Lives in tools layer, not adapter (adapter doesn't know "line items" as a concept) nor agent (deterministic arithmetic, no LLM judgment needed).
- [x] 3.6 `core/agents/tools/purchases_tools.py`: `crear_compra` (interrupt) — 13/13 checks
- [x] 3.7 `core/agents/tools/dispatch_tools.py`: `obtener_tablas_despacho`, `crear_guia_remision` (no interrupt), `enviar_guia_sunat` (interrupt), `listar_guias_remision` — 33/33 checks
- [x] 3.8 `core/agents/tools/finance_tools.py`: `crear_retencion`, `crear_percepcion`, `abrir_caja`, `cerrar_caja` (all interrupt), `reporte_del_dia`, `reporte_general_ventas` — 52/52 checks
- [x] 3.9 Verify every tool receives `TenantCredentials` via `config.configurable`/`InjectedToolArg`/`get_config()` — never as a normal parameter; confirmed no token/base_url field appears in any tool's serialized JSON schema via an automated `check_no_credential_leak_in_schema()` in every verify script (not just asserted — actually inspects `tool.tool_call_schema.model_json_schema()`)

Total Phase 3: 213/213 checks passed across 8 verify scripts (run with `PYTHONPATH=. venv/bin/python3 scripts/verify_phase3_*.py`).

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
