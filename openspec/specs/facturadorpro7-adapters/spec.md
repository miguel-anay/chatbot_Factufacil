# Facturadorpro7 Adapters Specification

## Purpose

8 new async hexagonal ports and their HTTP adapters against FacturadorPro7's real REST API, covering the verified endpoint set for all 5 specialists, with per-request tenant credentials and zero credential persistence.

## Requirements

### Requirement: Per-Domain Search/Create/Read Tool Coverage

Each specialist MUST expose only the tools mapped to its verified endpoint, per this table (no invented endpoints):

| Agent | Tool | Endpoint | Write? |
|---|---|---|---|
| Inventario | `buscar_producto` | `GET /api/items/records` | no |
| Inventario | `obtener_producto` | `GET /api/items/record/{id}` | no |
| Inventario | `crear_producto` | `POST /api/item` | yes |
| Inventario | `actualizar_producto` | `POST /api/items/update` | yes |
| Inventario | `activar_o_desactivar_producto` | `POST /api/items/change-active/{id}/{active}` | yes |
| Inventario | `marcar_favorito` | `POST /api/items/change-favorite/{id}/{favorite}` | yes |
| Inventario | `listar_categorias` | `GET /api/categories` | no |
| Inventario | `listar_marcas` | `GET /api/brands-records` | no |
| Inventario | `registrar_movimiento_stock` | `POST /api/inventory/transaction` | yes |
| Compras/Ventas | `buscar_producto`/`crear_producto` | `GET /api/document/search-items` / `POST /api/item` | mixed |
| Compras | `buscar_proveedor` | `GET /api/purchases/search-suppliers` | no |
| Compras | `crear_compra` | `POST /api/purchases` | yes |
| Ventas | `buscar_cliente` | `GET /api/document/search-customers` | no |
| Ventas | `crear_preliminar_venta` | `POST /api/sale-note` | yes |
| Ventas | `confirmar_y_generar_cpe` | `POST /api/sale-note/{id}/generate-cpe` | yes |
| Logística | `obtener_tablas_despacho` | `GET /api/dispatches/tables` | no |
| Logística | `crear_guia_remision` | `POST /api/dispatches` | yes |
| Logística | `enviar_guia_sunat` | `POST /api/dispatches/send` | yes |
| Logística | `listar_guias_remision` | `GET /api/dispatches/records` | no |
| Contabilidad | `crear_retencion`/`crear_percepcion` | `POST /api/retentions` / `/api/perceptions` | yes |
| Contabilidad | `abrir_caja`/`cerrar_caja` | `POST /api/cash/open` / `POST /api/cash/close/{cash}` | yes |
| Contabilidad | `reporte_del_dia` | `GET /api/report` | no |
| Contabilidad | `reporte_general_ventas` | `POST /api/reports/general-sale` | no |

#### Scenario: Inventario search returns matching items

- GIVEN a tenant with at least one product matching the search term
- WHEN `buscar_producto` is invoked with `input` set to that term
- THEN the adapter calls `GET /api/items/records` and returns the matching items

#### Scenario: Stock transfer is not exposed as a tool

- GIVEN the Inventario specialist's tool set
- WHEN listing available tools
- THEN no tool maps to `/api/transfers/*` (out of scope, unverified against the spec)

### Requirement: Single Auth-Aware HTTP Client

All 8 adapters MUST receive a shared `FacturadorPro7Client` instance constructed from per-request `TenantCredentials`; no adapter MUST implement its own HTTP/auth logic.

#### Scenario: Two adapters share one client instance per request

- GIVEN a single incoming request with valid `TenantCredentials`
- WHEN both `ItemsPort` and `InventoryPort` adapters are used in the same turn
- THEN both receive the same `FacturadorPro7Client` instance built for that request

### Requirement: Multi-Tenant Credentials Are Per-Request and Never Persisted

`TenantCredentials` (base_url + Bearer token) MUST travel via `config.configurable` using `InjectedToolArg`/`get_config()`; credentials MUST NOT appear as a normal tool parameter, in `AgentState`, in the checkpointer, on disk, or in logs.

#### Scenario: Tool schema hides credentials from the LLM

- GIVEN any write or read tool's Pydantic input schema
- WHEN the LLM receives the tool's JSON schema for tool-calling
- THEN no field of that schema represents a token or base_url

#### Scenario: Token never appears in logs

- GIVEN a completed request that exercised an adapter call
- WHEN application logs for that request are inspected
- THEN no Bearer token value appears in any log line

#### Scenario: Checkpointer never stores credentials

- GIVEN a session with a pending confirmation persisted by `InMemorySaver`
- WHEN the checkpointer's stored state is inspected
- THEN no tenant credential is present in `AgentState` or the checkpoint payload
