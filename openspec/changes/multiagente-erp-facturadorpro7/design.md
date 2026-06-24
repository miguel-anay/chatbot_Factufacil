# Design: Multi-agent ERP co-pilot for FacturadorPro7

## Technical Approach

Additive layer over the proven hexagonal scaffold. A LangGraph supervisor routes to 5 specialist nodes (Inventario/Producto, Compras, Ventas, Logística, Contabilidad), each owning domain tools that call FacturadorPro7's real API through 8 new async ports. Irreversible/SUNAT-facing/stock writes pause via `interrupt()`. Existing `/chat`, `ChatbotService`, the 3 sync ports, FAISS/memory adapters are untouched. Formalizes `~/.claude/plans/si-hago-multiagente-lo-tingly-quilt.md` (ground truth).

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Hexagonal boundary | `core/domain.py` + `core/ports.py` never import LangGraph/LangChain; `core/agents/*` may (application-services layer) | Pure-core ban everywhere, or framework in domain | Agents wire domain↔LLM (`@tool`, `bind_tools`, Pydantic). Intentional documented distinction, not a leak. |
| ItemsPort sharing | `ItemsPort` (search/create, light subset) shared by Compras+Ventas for inline "create-if-missing"; `InventoryPort` owns deep catalog/stock | One mega-product port | Inline buy/sell flows need a thin product op; full maintenance (update/active/favorite/categories/brands/stock txn) is Inventario-exclusive. |
| Confirmation placement | `interrupt()` called INSIDE each write-tool body (first line, before POST) | Wrapper around node/agent execution | Only point that works identically under `create_react_agent` prebuilt (no interceptable step) and custom loop. `Command(resume=...)` continues right after `interrupt()`. **Phase 5 correction (verified against real Phase 3 code, not assumed): `confirmation.py` does NOT contain a `require_confirmation()` wrapper around `interrupt()` — that would be dead code, because all 8 write-tools across `sales_tools.py`/`purchases_tools.py`/`inventory_tools.py`/`dispatch_tools.py`/`finance_tools.py` already call `langgraph.types.interrupt({...})` directly inline, with a consistent `{tool_name, summary, tool_args}` payload. `core/orchestration/confirmation.py` instead provides graph/HTTP-boundary helpers: `parse_interrupt_payload(invoke_result)` (translates `graph.invoke()`'s `__interrupt__` tuple of `Interrupt` objects into the `{tool_name, summary, tool_args}` shape for `POST /agent/chat`'s response) and `build_resume_command(approved)` (translates `POST /agent/confirm`'s `{approved: bool}` into `Command(resume={"approved": approved})`, the exact shape every write-tool already reads via `decision.get("approved")`). Neither function calls `interrupt()` or a tool — both operate strictly before-invoke/after-pause, at the layer PR7's HTTP handlers will consume.** |
| Credential injection | `TenantCredentials` via `config.configurable` using `InjectedToolArg`/`get_config()`; tool builds adapter per-invocation | Normal tool argument; compile-time pre-bound adapter | Graph compiled once (singleton); creds are per-request. A normal arg leaks the Bearer token into the LLM-visible JSON schema. Never in `AgentState`/checkpointer/disk/logs. |
| Multi-domain requests | No direct edges between specialists; no auto-chaining in v1 | Auto Compras→Logística chain | Chaining auto-writes multiplies blast radius of one human confirmation. Specialist suggests next step as a fresh user turn. Deferred "plan mode". |
| Port sync/async split | 8 new ports async; 3 existing ports stay sync | Convert all to async | New ports do real remote I/O; existing path has no regression need. Intentional scoped split. |
| Routing | `context_module` fast-path (no LLM) + `.with_structured_output()` fallback over a `Literal` of 5 modules | LLM-always routing | Frontend already knows the module; skip a call when hinted. **Phase 5 correction: the 5 modules are `inventario, compras, ventas, logistica, contabilidad` — this decision row and earlier text in this document were originally written when only 4 specialists existed (compras/ventas/logistica/contabilidad); Phase 4 (merged) added `inventario` as a 5th specialist (`core/agents/inventario_agent.py`, `build_inventario_agent()`). `core/orchestration/state.py::SpecialistModule` and `supervisor.py::RouteDecision.module` both use the corrected 5-value `Literal`, verified against the real Phase 4 agent files, not assumed from this doc's older text.** |
| Lifespan failure isolation | Agent graph compilation wrapped in `try/except` inside the EXISTING `lifespan()` in `main.py`, after the existing `chatbot = ChatbotService(...)` line. On failure: `app.state.agent_graph = None`, `app.state.agent_error = str(exc)`, log, do NOT re-raise. `agent_router.py` endpoints check `app.state.agent_graph is None` → `503`. `/health` surfaces `agent_error` | Letting graph compilation exceptions propagate out of `lifespan()` | `lifespan()` is a single shared FastAPI startup hook — an uncaught exception anywhere in it fails `yield` and takes down the WHOLE app, including the unrelated existing `/chat` path. Hexagonal isolation is code-level, not process-level; this is the actual mechanism that prevents a broken new agent from killing a working old endpoint. MANDATORY for the PR that touches `main.py` (entrypoint wiring phase) — not optional polish. |
| New-dependency pinning | `langgraph`, `langchain-core`, `langchain-openai`, `httpx` pinned to the EXACT versions proven in the Phase 0 smoke test (`==`, not `>=`/ranges) when added to `requirements.txt` | Loose ranges (`>=X,<Y`) | A range lets `pip` resolve a version never tested, which can break `docker build` or install incompatible transitive deps — discovered only at deploy time without a CI build gate. Exact pin removes that variable; existing loose ranges (`langchain-core>=0.2.0` etc.) are a pre-existing latent version of this same risk, not introduced by this change. |
| Catalog-metadata writes excluded from confirmation | `crear_producto`, `actualizar_producto`, `activar_o_desactivar_producto`, `marcar_favorito` execute immediately, no `interrupt()` | Gate them too, matching `erp-confirmation-flow/spec.md`'s original literal "any single-POST write" rule | **Post-archive `sdd-verify` finding, CRITICAL, now resolved by correcting the spec, not the code**: the original spec's classification rule (c) — "irreversible if it's the only POST step, no separate draft" — is a flawed mechanical proxy for actual business irreversibility. These 4 tools have no SUNAT/financial/stock consequence and are trivially correctable by calling the same tool again with different arguments (edit the price again, toggle active back, re-favorite) — they are not irreversible in any sense that matters to a human approving a write. The code (built correctly from the start) never gated them; the spec's rule was overbroad and got corrected to match, per `erp-confirmation-flow/spec.md`'s new "Catalog Metadata Writes Bypass Confirmation" requirement. |

## Data Flow

    POST /agent/chat (msg, session_id, context_module, creds)
      → graph.invoke(state, config={"configurable": {creds, thread_id=session_id}})
        → supervisor: context_module fast-path | structured-output fallback
          → specialist node (bind_tools): read tools run; write tool → interrupt()
            ↳ pauses → /agent/chat returns {status: "awaiting_confirmation", confirmation}
    POST /agent/confirm (session_id, approved)
      → graph.invoke(Command(resume={"approved": approved}), same thread_id)
        → tool continues after interrupt(): builds adapter from creds → real POST → END

Specialists never edge to each other; all paths terminate at `END`.

## File Changes

| File | Action | Description |
|---|---|---|
| `core/domain.py` | Modify | +ERP entities (additive) |
| `core/ports.py` | Modify | +8 async ports (additive) |
| `core/agents/base.py`, `{inventario,compras,ventas,logistica,contabilidad}_agent.py` | Create | Specialist agents |
| `core/agents/tools/*` (items/inventory/customers/suppliers/sales/purchases/dispatch/finance) | Create | ~25 tools, explicit Pydantic schemas |
| `core/orchestration/{state,supervisor,graph,confirmation}.py` | Create | AgentState, routing, StateGraph, graph/HTTP-boundary confirmation helpers (`parse_interrupt_payload`/`build_resume_command` — NOT a `require_confirmation()` wrapper, see "Confirmation placement" correction below) |
| `adapters/facturadorpro7_api/{http_client,auth, 8 adapters}.py` | Create | Single auth-aware client; per-request instantiation |
| `entrypoints/api/agent_router.py` | Create | `/agent/chat`, `/agent/confirm`, `/agent/session/{id}` |
| `entrypoints/api/main.py`, `schemas.py` | Modify | `include_router` + compile graph in existing `lifespan()` WRAPPED IN try/except (see Lifespan failure isolation decision — failure must not block `yield`/take down `/chat`); +schemas |
| `requirements.txt` | Modify | Declare langgraph/langchain-core/-openai/httpx |
| `/chat`, `chatbot_service.py`, FAISS/memory adapters, 3 sync ports | NOT modified | Zero regression |

## Interfaces / Contracts

```python
# core/ports.py (async, additive)
class ItemsPort(ABC):       # shared Compras+Ventas
    async def search(self, query, *, by_barcode=False, page=1) -> list[Item]: ...
    async def create(self, item: ItemDraft) -> Item: ...
class InventoryPort(ABC):   # Inventario-exclusive deep catalog/stock
    async def get_item(self, id) -> Item: ...
    async def update_item(self, id, patch) -> Item: ...
    async def change_active(self, id, active: bool) -> None: ...
    async def change_favorite(self, id, favorite: bool) -> None: ...
    async def list_categories(self) -> list[Category]: ...
    async def list_brands(self) -> list[Brand]: ...
    async def register_transaction(self, txn: StockTxn) -> StockMovement: ...
class CustomersPort(ABC):  async def search(self, query) -> list[Customer]: ...
class SuppliersPort(ABC):  async def search(self, query) -> list[Supplier]: ...
class SalesPort(ABC):
    async def create_sale_note(self, draft) -> SaleNote: ...
    async def generate_cpe(self, sale_note_id) -> Cpe: ...        # interrupt
class PurchasesPort(ABC):
    # item_snapshots: REQUIRED, one dict per line item (description/
    # internal_id/unit_type_id/item_code/... subset). Real source read
    # (PurchaseController::store(), Phase 2 follow-up) showed
    # `$doc->items()->create($row)` is called directly off the caller's
    # payload — no server Transform/Input fills `purchase_items.item`
    # (NOT-NULL json column). Corrects the original signature, which only
    # had `draft: dict` per the incomplete openapi.yaml.
    async def create_purchase(self, draft, *, item_snapshots: list[dict]) -> Purchase: ...  # interrupt
class DispatchPort(ABC):
    async def get_tables(self) -> DispatchTables: ...
    # establishment_fiscal_code/origin_location_id/delivery_location_id:
    # REQUIRED, additive. Real source read (DispatchTransform.php +
    # DispatchValidation.php + DispatchInput.php, Phase 2 follow-up) showed
    # the API Transform layer needs `datos_del_emisor.
    # codigo_del_domicilio_fiscal` (an Establishment.code, e.g. "0000") and
    # BOTH origin/delivery need a 6-digit ubigeo `location_id` — none of
    # this is in openapi.yaml. Corrects the original signature, which only
    # had `draft: dict`.
    async def create_dispatch(
        self, draft, *, establishment_fiscal_code: str,
        origin_location_id: str, delivery_location_id: str,
    ) -> Dispatch: ...
    async def send_dispatch(self, id) -> Dispatch: ...            # interrupt
    async def list_dispatches(self, **f) -> list[Dispatch]: ...
class FinancePort(ABC):
    # establishment_fiscal_code/supplier_identity: REQUIRED, additive.
    # Real source read (RetentionTransform.php + RetentionValidation.php,
    # Phase 2 follow-up) showed retentions need BOTH datos_del_emisor (same
    # shape as Dispatch above) AND datos_del_proveedor (PersonTransform:
    # codigo_tipo_documento_identidad/numero_documento/
    # apellidos_y_nombres_o_razon_social required, codigo_pais also
    # effectively required — persons.country_id is NOT NULL).
    async def create_retention(
        self, d, *, establishment_fiscal_code: str, supplier_identity: dict,
    ) -> Retention: ...         # interrupt
    # customer_identity: REQUIRED, additive. IMPORTANT CORRECTION: real
    # source read of PerceptionTransform.php (the `establishment` line is
    # COMMENTED OUT) + PerceptionValidation.php (hardcodes establishment_id
    # from the authenticated user) confirmed perceptions do NOT need
    # datos_del_emisor at all, unlike retentions/dispatches — only
    # datos_del_cliente_o_receptor (same PersonTransform shape, customer).
    async def create_perception(self, d, *, customer_identity: dict) -> Perception: ...  # interrupt
    async def open_cash(self, d) -> Cash: ...                     # interrupt
    async def close_cash(self, cash_id) -> Cash: ...              # interrupt
    async def get_daily_report(self, **f) -> Report: ...
    async def get_general_sale_report(self, d) -> Report: ...
```

**Why these signatures changed (Phase 2 follow-up round, PR 3 same branch)**: the original signatures above were designed purely from `openapi.yaml`, which the Phase 2 implementation pass discovered is incomplete for 4 of the 8 adapters — several real Laravel-side required fields (nested issuer/person data, ubigeo codes, item snapshots) are validated deep in controller/Transform/Input classes and never documented in the spec at all. A live-sandbox-first approach surfaced 500-class errors instead of clean 422s, masking the true cause. A direct read of the FacturadorPro7 Laravel source (`app/CoreFacturalo/Requests/Api/Transform/*`, `Requests/Inputs/*`, `Requests/Api/Validation/*`) was required to find the exact required shape. These params are additive/keyword-only — no caller of `create_dispatch`/`create_retention` existed yet outside Phase 2's own verification scripts, so this is not a breaking change to any shipped tool/agent code (Phase 3+ not yet built).

`AgentState` (TypedDict): `messages` (`add_messages`), `context_module`, `active_specialist`, `session_id`, `pending_confirmation`, `handoff_reason`. Credentials NEVER in state. `context_module`/`active_specialist` type as `Literal["inventario","compras","ventas","logistica","contabilidad"] | None` (5 modules, Phase 5 correction above — `inventario` included).

HTTP: `/agent/chat` → `{session_id, status: answered|awaiting_confirmation, answer?, confirmation?:{tool_name, summary, tool_args}}`. `/agent/confirm` → resume via `Command(resume={"approved": bool})` on `thread_id=session_id`. Dev checkpointer: `InMemorySaver`.

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Smoke (gate) | qwen-plus tool_calls + async `interrupt()` cycle | Echo tool bind + trivial interrupt → resume, BEFORE building ~25 tools |
| Unit | Each tool/adapter schema | Pydantic schema, creds-injection path, no token in serialized schema |
| Integration | Each adapter vs `desa.facturadorpro7.test` | Real login/token before wrapping in a tool |
| E2E | Per specialist propose→`awaiting_confirmation`→`/agent/confirm`→real write | Verify doc created in ERP; routing for 5 `context_module` values + fallback |
| Regression | `/chat` unchanged | Pre-sales bot zero regression |

## Migration / Rollout

Purely additive. Rollback = remove `include_router` in `main.py`; new folders deletable with no impact. Before real users: migrate `InMemorySaver` → `langgraph-checkpoint-sqlite`/`-postgres` (pending confirmation lost on restart otherwise).

**CI gate (implemented, applies to every PR in this chain, not just this change)**: `deploy.yml` now has a `test` job (`docker build` → boot container → poll `/health` up to 90s, first boot loads the embedding model and takes ~50s → run `test_chatbot.py`) that `deploy` depends on via `needs: test`. A broken build or a regression on `/chat` blocks the SSM deploy automatically — this is what actually prevents production breakage; hexagonal/SDD reduce blast radius and ambiguity but don't execute code. Requires a GitHub Actions repo secret `ALIBABA_API_KEY` (separate from the existing AWS SSM parameter used at deploy time) for the regression-test step to get real LLM responses — not yet provisioned, pending user action (`gh secret set ALIBABA_API_KEY`).

## Open Questions

- [x] qwen-plus tool-calling + async `interrupt()` — RESOLVED in Phase 0: passed both checks first try, no model swap needed.
- [x] `/api/perceptions` schema — RESOLVED in Phase 2 follow-up via direct source read: `PerceptionTransform.php`'s `establishment` line is commented out and `PerceptionValidation.php` hardcodes `establishment_id` from the authenticated user. Perceptions need ONLY `datos_del_cliente_o_receptor` (customer identity, `codigo_pais` required too — `persons.country_id` is NOT NULL). `FinancePort.create_perception()` signature updated with `customer_identity: dict`. NOT live-verified end-to-end (no test attempt made — retention's analogous attempt revealed a `documentos` requirement deep in XML generation that likely applies here too; deferred to Phase 3, see below).
- [ ] `/api/retentions` schema — PARTIALLY RESOLVED in Phase 2 follow-up via direct source read: requires BOTH `datos_del_emisor` (`{"codigo_del_domicilio_fiscal": "<Establishment.code>"}`, e.g. `"0000"` for this tenant) AND `datos_del_proveedor` (PersonTransform shape, `codigo_pais` also required). `FinancePort.create_retention()` signature updated with `establishment_fiscal_code`/`supplier_identity`. Live-verified to get PAST the original gap (a real attempt with both fixes applied progressed through `Functions::establishment()`/`Functions::person()` resolution). GENUINELY UNRESOLVED (time-boxed): a minimal `totales`-only body with no `documentos` (the underlying purchase invoices being retained against) hits `UpstreamError: Invalid argument supplied for foreach()` deep inside `Facturalo->save()`/XML generation — not reached via source read in this pass (both `RetentionTransform::document()` and `RetentionInput::document()` guard their foreach with `key_exists`, so the unguarded one is further downstream). Phase 3 tool design for `crear_retencion` must treat `documentos` as effectively required, resolved from a real `Purchase` record.
- [ ] `/api/dispatches` required fields — PARTIALLY RESOLVED in Phase 2 follow-up via direct source read: requires `datos_del_emisor` (same shape as retentions) PLUS a 6-digit ubigeo `location_id` nested in BOTH `direccion_partida` (origin) and `direccion_llegada` (delivery), PLUS the Transform layer's Spanish field names (`serie_documento`/`numero_documento`, not `series`/`number`). `DispatchPort.create_dispatch()` signature updated with `establishment_fiscal_code`/`origin_location_id`/`delivery_location_id`. Live-verified to get PAST the original gap and series validation. GENUINELY UNRESOLVED (time-boxed): BOTH transport modes additionally require an undocumented nested person object — `transport_mode_type_id="02"` (privado) needs a full `driver` object (`DispatchInput::getDriverId()`), `transport_mode_type_id="01"` (público) needs a full `dispatcher`/transportista object (`DispatchInput::getDispatcherId()`) — confirmed live for both. No transport mode skips this requirement. Phase 3 tool design for `crear_guia_remision` must resolve dispatcher OR driver+vehicle data before calling the adapter.
- [x] `/api/cash/open` required fields — RESOLVED in Phase 2 via real 422 against sandbox: only `beginning_balance` is actually required. `date_opening`/`time_opening` are NOT required server-side (contradicts the original assumption). Also discovered: `/api/cash/close/{cash}` is a GET route in the real spec/app, not POST as design.md originally assumed.
- [ ] Stock transfer between warehouses (`/api/transfers/*`) not in spec — deferred, NOT built; `register_transaction` covers simple in/out only.
- [ ] NEW (Phase 2 discovery): `register_transaction`'s `inventory_transaction_id` is a tenant-configured foreign key (e.g. id=10/11 = valid "ingreso" types on the sandbox tenant) with NO listing endpoint anywhere in openapi.yaml. The tools/agents layer (Phase 3+) cannot safely hardcode these IDs across tenants — needs either a documented lookup endpoint or per-tenant configuration resolved at runtime before `registrar_movimiento_stock` ships.
- [x] `create_purchase()` NOT-NULL `purchase_items.item` gap — RESOLVED and LIVE-VERIFIED in Phase 2 follow-up: real source read of `PurchaseController::store()`/migration confirmed the gap; `PurchasesPort.create_purchase()` signature updated with required `item_snapshots: list[dict]`. A real end-to-end create succeeded (purchase id=122 on the sandbox, marked test data) after ALSO discovering the `purchase_a4` PDF template (called outside the DB transaction, after commit) reads `item.is_set` — the adapter now defaults `is_set: False` on every injected snapshot.
- [ ] `create_sale_note()` NOT-NULL gaps — PARTIALLY RESOLVED, GENUINELY UNRESOLVED SERVER BUG (time-boxed) in Phase 2 follow-up: real source read of `SaleNoteController::mergeData()`/`getDataSeries()` confirmed `prefix` is never populated; live attempts then found and fixed TWO MORE in the same class (`time_of_issue`, `exchange_rate_sale`) — all now defaulted in `sales_adapter.py`. After fixing all three, a FOURTH NOT-NULL violation appeared on `total`, a COMPUTED AGGREGATE (sum of line-item totals/taxes), not a simple scalar default — this suggests `mergeData()` assumes a richer web-UI flow that pre-computes all total/tax columns client-side, which the bare API endpoint never does server-side. Stopped here per time-box; Phase 3 tool design for `crear_preliminar_venta` should compute and send the full total/tax breakdown explicitly rather than relying on server-side computation.
- [x] **NEW (Phase 7 discovery): all 5 agent system prompts (Phase 4) had an ambiguous confirmation instruction that made the LLM ask for confirmation IN CHAT instead of calling the interrupt-gated tool at all.** Original wording was a variant of "si la tool pide confirmación, esperá la decisión del usuario antes de asumir que X se hizo" — this reads as permission to seek confirmation conversationally, and the live integration test caught it: for `crear_compra`, after correctly resolving real product/supplier IDs via search, the LLM responded "¿Confirmás que deseas registrar esta compra?" in natural language and never called the tool — so `interrupt()` never fired, `/agent/confirm` was never exercised, defeating the entire propose→confirm mechanism this change was built around. Reproduced reliably (not a one-off sampling fluke) before the fix; RESOLVED by rewording all 5 prompts' confirmation rule to be directive: "LLAMÁ a la tool DIRECTAMENTE ... NUNCA le pidas confirmación al usuario por chat antes de invocarla. La tool misma se pausa y gestiona la confirmación a través del mecanismo del sistema." Verified clean on 2 consecutive live runs post-fix (42/42 both times) — touches `core/agents/{compras,ventas,logistica,contabilidad,inventario}_agent.py`, shipped as part of PR 7 even though those files were introduced in PR 5, because this is where live HTTP-level verification first exercised the full propose→confirm path end-to-end and caught it.
