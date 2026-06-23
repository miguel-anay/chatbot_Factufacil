# Erp Agent Orchestration Specification

## Purpose

LangGraph supervisor that routes each incoming turn to exactly one of 5 ERP specialist agents (Inventario/Producto, Compras, Ventas, Logística, Contabilidad), using a frontend-supplied module hint when present and an LLM classifier otherwise.

## Requirements

### Requirement: Context Module Fast-Path Routing

The system MUST route a turn directly to the matching specialist node, with no LLM call, WHEN the request carries a non-empty `context_module` value in `{inventario, compras, ventas, logistica, contabilidad}`.

#### Scenario: Frontend supplies a known module

- GIVEN a request with `context_module: "ventas"`
- WHEN the supervisor processes the turn
- THEN it routes to the Ventas specialist node without invoking an LLM classification call

#### Scenario: All five modules route correctly

- GIVEN five separate requests, one per valid `context_module` value
- WHEN each is processed by the supervisor
- THEN each routes to its corresponding specialist (inventario→Inventario, compras→Compras, ventas→Ventas, logistica→Logística, contabilidad→Contabilidad)

### Requirement: LLM Fallback Classification

The system MUST classify the turn via a single structured-output LLM call against a closed `Literal` of the 5 module names WHEN `context_module` is absent, empty, or not one of the 5 valid values.

#### Scenario: No context_module provided

- GIVEN a request with no `context_module` field
- WHEN the supervisor processes the turn
- THEN it issues one LLM call with `.with_structured_output()` constrained to the 5 specialist labels
- AND routes to the returned specialist

#### Scenario: Unrecognized context_module value

- GIVEN a request with `context_module: "marketing"` (not one of the 5 valid values)
- WHEN the supervisor processes the turn
- THEN it falls back to LLM classification instead of failing or routing arbitrarily

### Requirement: No Direct Specialist-to-Specialist Edges

The graph MUST route every specialist node to `END` after completing its turn; specialists MUST NOT transition directly to another specialist node.

#### Scenario: Multi-domain request in one turn

- GIVEN a user message that references two domains (e.g. "create this purchase and tell me when it's ready to dispatch")
- WHEN the active specialist (Compras) completes its flow
- THEN the response ends the turn, suggesting the next step as a new user turn
- AND control does not automatically hand off to the Logística node

### Requirement: Credentials Excluded From Routing State

`AgentState` MUST NOT contain tenant credentials; routing decisions MUST be made using only `context_module`, message content, and non-sensitive state fields.

#### Scenario: Supervisor inspects state without credentials

- GIVEN a compiled graph processing any turn
- WHEN the supervisor reads `AgentState` to decide routing
- THEN no field of `AgentState` holds a Bearer token or tenant base_url
