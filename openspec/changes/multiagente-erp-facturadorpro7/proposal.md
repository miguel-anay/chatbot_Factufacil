# Proposal: Multi-agent ERP co-pilot for FacturadorPro7

## Intent

Today this project is a single-agent pre-sales chatbot (FAQ + RAG + LLM) on a proven hexagonal architecture with a working Docker/CI deploy. We need to add — without touching the existing pre-sales path — an **ERP co-pilot** embedded in FacturadorPro7 (Laravel 5.7 + Vue2, multi-tenant). The user stands in an ERP module and a domain specialist agent searches, drafts, and confirms documents against the real ERP API. Reuses the existing hexagonal scaffold and deploy pipeline instead of starting a new project.

## Scope

### In Scope
- 5 specialist agents (Inventario/Producto, Compras, Ventas, Logística, Contabilidad) + 1 supervisor, orchestrated via LangGraph.
- `interrupt()`-based propose→confirm UX for irreversible/SUNAT-facing/stock-affecting writes.
- 8 new async ports + `adapters/facturadorpro7_api/*` built from FacturadorPro7's `openapi.yaml`; single auth-aware `http_client.py`.
- ~25 tools mapped to verified real API endpoints; per-request `TenantCredentials` via `config.configurable` (`InjectedToolArg`).
- New `entrypoints/api/agent_router.py` (`/agent/chat`, `/agent/confirm`, `/agent/session/{id}`); graph compiled once in existing lifespan.
- Update `requirements.txt` to declare already-installed deps (langgraph, langchain-core/-openai, httpx).

### Out of Scope
- Any change to `/chat`, `core/chatbot_service.py`, existing `core/domain.py`/`core/ports.py` entries, RAG/memory adapters — zero regression.
- Voice / OCR input.
- Auto-chaining writes across specialists (e.g. Compras→Logística) — deferred to a future "plan mode".
- Stock transfer between warehouses (`/api/transfers/*` not in spec — see Risks).
- Production checkpointer (sqlite/postgres) migration — dev uses `InMemorySaver`.

## Capabilities

### New Capabilities
- `erp-agent-orchestration`: LangGraph supervisor routing (context_module fast-path + LLM fallback) over 5 specialist nodes.
- `erp-confirmation-flow`: `interrupt()` propose→confirm contract for irreversible writes, resumed via `Command(resume=...)`.
- `facturadorpro7-adapters`: 8 async ports + HTTP adapters against the real ERP API.
- `erp-agent-api`: `/agent/chat`, `/agent/confirm`, `/agent/session/{id}` HTTP contract.

### Modified Capabilities
- None. Existing pre-sales `/chat` capability is untouched (purely additive change).

## Approach

LangGraph (not heavy LangChain) for the supervisor→specialist graph and `interrupt()`. Hard boundary: `core/domain.py` and `core/ports.py` (pure domain) never import LangGraph/LangChain; `core/agents/*` is an application-services layer that wires domain↔LLM and may use `@tool`/`bind_tools`. New ports are async (real remote I/O); the 3 old ports stay sync — intentional split. `ItemsPort` is shared between Compras and Ventas for inline "create if missing"; deep catalog/stock lives in Inventario. `interrupt()` is called inside the write-tool itself (first line, before the POST), so it works under both prebuilt and custom agent loops. Credentials travel per-request in `config.configurable`, never in `AgentState`/checkpointer/logs.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `core/agents/`, `core/orchestration/` | New | Specialists, tools, supervisor, graph, confirmation |
| `core/ports.py`, `core/domain.py` | Modified | +8 async ports & entities (additive) |
| `adapters/facturadorpro7_api/` | New | http_client, auth, 8 adapters |
| `entrypoints/api/` | Modified | +agent_router, +schemas, lifespan wiring |
| `requirements.txt` | Modified | Declare langgraph/langchain/httpx |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `qwen-plus` tool-calling + `interrupt()` in async tools never proven | Med | Smoke-test echo tool + interrupt cycle before building ~25 tools; model swap is config-only |
| `/api/retentions`,`/api/perceptions` schemas undocumented (empty `type: object`) | High | Inspect Laravel controller or force a 422 in sandbox before fixing tool schema |
| `/api/dispatches` required fields incomplete in spec | Med | `extra: dict` escape field until verified via `GET /api/dispatches/tables`/sandbox |
| `/api/cash/open` no `required` listed | Med | Treat balance/date/time as required in practice; confirm in sandbox |
| New trust surface: per-request Bearer tokens | High | Never log token (audit `http_client.py`), never persist in checkpointer/disk |
| `InMemorySaver` loses pending confirmation on restart | Med | Acceptable for dev; migrate to sqlite/postgres before real users |

## Rollback Plan

Change is purely additive. Rollback = do not register `agent_router` in `main.py` (remove `include_router`); the existing `/chat` path is unaffected. New folders (`core/agents`, `core/orchestration`, `adapters/facturadorpro7_api`) can be deleted with no impact on the deployed pre-sales bot. `requirements.txt` revert restores prior declared deps (venv already has them).

## Dependencies

- FacturadorPro7 `openapi.yaml` (`/home/k3n5h1n/Escritorio/PRO7Final/FacturadorPro7/public/api-docs/openapi.yaml`).
- Dev tenant `desa.facturadorpro7.test` (real login/token) for adapter smoke tests.
- Frontend supplies per-request `TenantCredentials` (base_url + Bearer); chatbot stores none.
- Already in venv: `langgraph==1.2.0`, `langchain-core==1.4.0`, `langchain-openai==1.2.1`, `httpx==0.28.1`.

## Success Criteria

- [ ] Each of the 5 specialists completes a propose→`awaiting_confirmation`→`/agent/confirm`→real write cycle against the dev tenant.
- [ ] Existing `/chat` pre-sales bot works unchanged (zero regression).
- [ ] Routing: `context_module` fast-path hits correct specialist for all 5 values; LLM fallback classifies reasonably when absent.
- [ ] No tenant token appears in logs, checkpointer, or disk.
- [ ] `requirements.txt` matches the real venv.
