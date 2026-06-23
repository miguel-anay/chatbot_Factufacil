# Erp Agent Api Specification

## Purpose

HTTP contract for the new ERP co-pilot endpoints, compiled once into the existing FastAPI lifespan, fully additive to the existing `/chat` endpoint.

## Requirements

### Requirement: Agent Chat Endpoint Contract

`POST /agent/chat` MUST accept a request carrying `session_id`, user message, and optional `context_module`, and MUST return `{session_id, status: "answered"|"awaiting_confirmation", answer?, confirmation?: {tool_name, summary, tool_args}}`.

#### Scenario: Answered without confirmation needed

- GIVEN a user message that only triggers read tools (e.g. `buscar_producto`)
- WHEN `POST /agent/chat` is called
- THEN the response has `status: "answered"` and a populated `answer`, with no `confirmation` field

#### Scenario: Awaiting confirmation for a write

- GIVEN a user message that triggers `crear_compra`
- WHEN `POST /agent/chat` is called
- THEN the response has `status: "awaiting_confirmation"` and a populated `confirmation` object with `tool_name`, `summary`, and `tool_args`

### Requirement: Agent Confirm Endpoint Contract

`POST /agent/confirm` MUST accept `{session_id, approved}` and MUST resume the graph identified by `thread_id == session_id` via `Command(resume={"approved": approved})`.

#### Scenario: Confirm resumes an existing session

- GIVEN a session_id with a pending confirmation from a prior `/agent/chat` call
- WHEN `POST /agent/confirm` is called with that session_id and `approved: true`
- THEN the graph resumes and returns a final `status: "answered"` response

#### Scenario: Confirm on unknown session_id

- GIVEN a session_id with no pending confirmation
- WHEN `POST /agent/confirm` is called with that session_id
- THEN the system MUST return an error response and MUST NOT execute any write

### Requirement: Session State Read Endpoint

`GET /agent/session/{id}` MUST return the current state of the session's graph thread without mutating it.

#### Scenario: Inspect a pending session

- GIVEN a session_id with a pending confirmation
- WHEN `GET /agent/session/{id}` is called
- THEN the response reflects the pending state without resuming or cancelling the graph

### Requirement: Existing /chat Endpoint Remains Unmodified

The pre-sales `/chat` endpoint, `core/chatbot_service.py`, existing `core/domain.py`/`core/ports.py` entries, and the RAG/memory adapters MUST continue to behave identically after this change is applied.

#### Scenario: Pre-sales chatbot unaffected by the new router

- GIVEN the existing `/chat` endpoint and its full request/response contract
- WHEN the new `agent_router` is registered via `include_router` in the lifespan
- THEN `/chat` requests produce the same responses as before this change, with no schema or behavior difference

#### Scenario: Rollback removes only new surface

- GIVEN the new `agent_router` is removed from `main.py`
- WHEN the application restarts
- THEN `/chat` continues to operate normally and no pre-sales behavior is affected
