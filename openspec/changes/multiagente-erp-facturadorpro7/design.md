# Design: Multi-agent ERP co-pilot for FacturadorPro7

## Technical Approach

Additive layer over the proven hexagonal scaffold. A LangGraph supervisor routes to 5 specialist nodes (Inventario/Producto, Compras, Ventas, Logística, Contabilidad), each owning domain tools that call FacturadorPro7's real API through 8 new async ports. Irreversible/SUNAT-facing/stock writes pause via `interrupt()`. Existing `/chat`, `ChatbotService`, the 3 sync ports, FAISS/memory adapters are untouched. Formalizes `~/.claude/plans/si-hago-multiagente-lo-tingly-quilt.md` (ground truth).

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Hexagonal boundary | `core/domain.py` + `core/ports.py` never import LangGraph/LangChain; `core/agents/*` may (application-services layer) | Pure-core ban everywhere, or framework in domain | Agents wire domain↔LLM (`@tool`, `bind_tools`, Pydantic). Intentional documented distinction, not a leak. |
| ItemsPort sharing | `ItemsPort` (search/create, light subset) shared by Compras+Ventas for inline "create-if-missing"; `InventoryPort` owns deep catalog/stock | One mega-product port | Inline buy/sell flows need a thin product op; full maintenance (update/active/favorite/categories/brands/stock txn) is Inventario-exclusive. |
| Confirmation placement | `interrupt()` called INSIDE each write-tool body (first line, before POST) | Wrapper around node/agent execution | Only point that works identically under `create_react_agent` prebuilt (no interceptable step) and custom loop. `Command(resume=...)` continues right after `interrupt()`. |
| Credential injection | `TenantCredentials` via `config.configurable` using `InjectedToolArg`/`get_config()`; tool builds adapter per-invocation | Normal tool argument; compile-time pre-bound adapter | Graph compiled once (singleton); creds are per-request. A normal arg leaks the Bearer token into the LLM-visible JSON schema. Never in `AgentState`/checkpointer/disk/logs. |
| Multi-domain requests | No direct edges between specialists; no auto-chaining in v1 | Auto Compras→Logística chain | Chaining auto-writes multiplies blast radius of one human confirmation. Specialist suggests next step as a fresh user turn. Deferred "plan mode". |
| Port sync/async split | 8 new ports async; 3 existing ports stay sync | Convert all to async | New ports do real remote I/O; existing path has no regression need. Intentional scoped split. |
| Routing | `context_module` fast-path (no LLM) + `.with_structured_output()` fallback over a `Literal` of 5 modules | LLM-always routing | Frontend already knows the module; skip a call when hinted. |
| Lifespan failure isolation | Agent graph compilation wrapped in `try/except` inside the EXISTING `lifespan()` in `main.py`, after the existing `chatbot = ChatbotService(...)` line. On failure: `app.state.agent_graph = None`, `app.state.agent_error = str(exc)`, log, do NOT re-raise. `agent_router.py` endpoints check `app.state.agent_graph is None` → `503`. `/health` surfaces `agent_error` | Letting graph compilation exceptions propagate out of `lifespan()` | `lifespan()` is a single shared FastAPI startup hook — an uncaught exception anywhere in it fails `yield` and takes down the WHOLE app, including the unrelated existing `/chat` path. Hexagonal isolation is code-level, not process-level; this is the actual mechanism that prevents a broken new agent from killing a working old endpoint. MANDATORY for the PR that touches `main.py` (entrypoint wiring phase) — not optional polish. |
| New-dependency pinning | `langgraph`, `langchain-core`, `langchain-openai`, `httpx` pinned to the EXACT versions proven in the Phase 0 smoke test (`==`, not `>=`/ranges) when added to `requirements.txt` | Loose ranges (`>=X,<Y`) | A range lets `pip` resolve a version never tested, which can break `docker build` or install incompatible transitive deps — discovered only at deploy time without a CI build gate. Exact pin removes that variable; existing loose ranges (`langchain-core>=0.2.0` etc.) are a pre-existing latent version of this same risk, not introduced by this change. |

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
| `core/orchestration/{state,supervisor,graph,confirmation}.py` | Create | AgentState, routing, StateGraph, `require_confirmation()` |
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
class PurchasesPort(ABC):  async def create_purchase(self, draft) -> Purchase: ...  # interrupt
class DispatchPort(ABC):
    async def get_tables(self) -> DispatchTables: ...
    async def create_dispatch(self, draft) -> Dispatch: ...
    async def send_dispatch(self, id) -> Dispatch: ...            # interrupt
    async def list_dispatches(self, **f) -> list[Dispatch]: ...
class FinancePort(ABC):
    async def create_retention(self, d) -> Retention: ...         # interrupt
    async def create_perception(self, d) -> Perception: ...       # interrupt
    async def open_cash(self, d) -> Cash: ...                     # interrupt
    async def close_cash(self, cash_id) -> Cash: ...              # interrupt
    async def get_daily_report(self, **f) -> Report: ...
    async def get_general_sale_report(self, d) -> Report: ...
```

`AgentState` (TypedDict): `messages` (`add_messages`), `context_module`, `active_specialist`, `session_id`, `pending_confirmation`, `handoff_reason`. Credentials NEVER in state.

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

- [ ] qwen-plus tool-calling + async `interrupt()` unproven — smoke-test gate; model swap (qwen-max/gpt-4o-mini) is config-only.
- [ ] `/api/retentions`, `/api/perceptions` schemas undocumented (empty `type: object`) — inspect controller or force 422 in sandbox before fixing tool schema.
- [ ] `/api/dispatches` required fields thin — `extra: dict` escape until verified via `GET /api/dispatches/tables`.
- [ ] `/api/cash/open` no `required` listed — treat balance/date/time as required, confirm in sandbox.
- [ ] Stock transfer between warehouses (`/api/transfers/*`) not in spec — deferred, NOT built; `register_transaction` covers simple in/out only.
