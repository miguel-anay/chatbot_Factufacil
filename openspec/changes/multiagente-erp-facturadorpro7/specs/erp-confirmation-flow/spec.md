# Erp Confirmation Flow Specification

## Purpose

Propose-then-confirm contract for ERP writes classified as irreversible, SUNAT-facing, or stock-affecting, implemented via LangGraph `interrupt()` called inside the write tool itself, resumed via `Command(resume=...)`.

## Requirements

### Requirement: Irreversible Writes Require Confirmation

A write tool MUST call `require_confirmation(tool_name, tool_args, summary)` as the first statement of its function body, before issuing the real POST, WHEN the tool is classified as irreversible. A tool is irreversible if it satisfies at least one of: (a) it is SUNAT-facing (generates or sends a fiscal/electronic document), (b) it changes real stock quantities, (c) it is the only POST step for that operation (no separate draft step exists). The irreversible set is: `crear_producto`, `actualizar_producto`, `activar_o_desactivar_producto`, `marcar_favorito`, `registrar_movimiento_stock`, `crear_compra`, `confirmar_y_generar_cpe`, `enviar_guia_sunat`, `crear_retencion`, `crear_percepcion`, `abrir_caja`, `cerrar_caja`.

#### Scenario: Tool execution pauses before the POST

- GIVEN the Logística specialist invokes `enviar_guia_sunat` with valid arguments
- WHEN the tool function executes
- THEN `interrupt()` fires before any HTTP POST is issued
- AND the graph run halts in a pending state, exposing `{tool_name, tool_args, summary}`

### Requirement: Draft Writes Bypass Confirmation

A write tool MUST execute its POST immediately, without calling `require_confirmation`, WHEN it is a draft-creation step that has a distinct, separate confirmation step later in the same workflow. The draft set is: `crear_preliminar_venta`, `crear_guia_remision`.

#### Scenario: Sale draft created without interruption

- GIVEN the Ventas specialist invokes `crear_preliminar_venta`
- WHEN the tool executes
- THEN the POST to `/api/sale-note` completes immediately
- AND no `awaiting_confirmation` state is produced for this call

### Requirement: Resume Continues Inside the Same Tool Call

WHEN the API receives `POST /agent/confirm` with `{session_id, approved}`, the system MUST resume the graph via `Command(resume={"approved": approved})` against the `thread_id` equal to `session_id`, continuing execution immediately after the `interrupt()` line inside the original tool function.

#### Scenario: User approves the pending write

- GIVEN a session with a pending `enviar_guia_sunat` confirmation
- WHEN `POST /agent/confirm` is called with `{session_id, approved: true}`
- THEN the graph resumes inside `enviar_guia_sunat`, issues the real POST, and returns the final answer with `status: "answered"`

#### Scenario: User declines the pending write

- GIVEN a session with a pending `crear_compra` confirmation
- WHEN `POST /agent/confirm` is called with `{session_id, approved: false}`
- THEN the graph resumes without issuing the POST to `/api/purchases`
- AND the response communicates the write was cancelled
- AND no purchase document is created against the tenant

#### Scenario: Decline a stock-affecting movement

- GIVEN a session with a pending `registrar_movimiento_stock` confirmation
- WHEN `POST /agent/confirm` is called with `{session_id, approved: false}`
- THEN no call to `/api/inventory/transaction` is made
- AND real stock quantities remain unchanged

#### Scenario: Decline a cash session open

- GIVEN a session with a pending `abrir_caja` confirmation
- WHEN `POST /agent/confirm` is called with `{session_id, approved: false}`
- THEN no call to `/api/cash/open` is made
- AND the response confirms no cash session was opened

### Requirement: Pending Confirmation Survives Process Lifetime Only

The system MUST persist pending confirmations via `InMemorySaver` for the dev environment; a pending confirmation MAY be lost if the process restarts.

#### Scenario: Process restart drops pending state

- GIVEN a pending `crear_retencion` confirmation exists for a session
- WHEN the API process restarts before `/agent/confirm` is called
- THEN the pending confirmation is no longer resumable for that session_id
