"""
Router del co-piloto ERP multiagente — `POST /agent/chat`, `POST
/agent/confirm`, `GET /agent/session/{id}` (design.md, "Data Flow" + HTTP
contract; spec `erp-agent-api`).

NO reimplementa el parsing de `__interrupt__`/`Command(resume=...)` — usa
`core.application.orchestration.confirmation.parse_interrupt_payload()`/
`build_resume_command()` (ya construidos en PR6), que son la fuente única de
verdad para esa traducción.

El grafo compilado vive en `request.app.state.agent_graph` — lo arma
`lifespan()` (main.py) UNA SOLA VEZ al arrancar, envuelto en try/except
(design.md "Lifespan failure isolation"). Si la compilación falló,
`app.state.agent_graph` es `None` y cada endpoint de este router devuelve
503 con `app.state.agent_error`, en vez de crashear o de tocar `/chat`.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from adapters.facturadorpro7_api.auth import TenantCredentials
from core.application.orchestration.confirmation import build_resume_command, parse_interrupt_payload
from entrypoints.api.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    AgentConfirmRequest,
)

# Cache en memoria, dentro del proceso, de las credenciales usadas en el
# último `/agent/chat` de cada session_id — NUNCA persistido a disco/log,
# vive y muere con el proceso igual que `InMemorySaver` (design.md
# "Pending Confirmation Survives Process Lifetime Only"). Es necesario
# porque el tool interrupt-gated reanuda DENTRO de su propio cuerpo
# (design.md "Resume Continues Inside Same Tool Call") y necesita
# `config.configurable.creds` otra vez para construir el adapter — el
# checkpointer de LangGraph persiste `AgentState`, no `config.configurable`
# (las credenciales nunca viven ahí, por diseño — "Credentials Excluded
# From Routing State"). `AgentConfirmRequest` no repite tenant_base_url/
# tenant_token porque el contrato HTTP de design.md solo pide
# {session_id, approved} para /agent/confirm — se resuelven las
# credenciales del `/agent/chat` que dejó la sesión pendiente.
_PENDING_CREDS: Dict[str, TenantCredentials] = {}

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_graph(request: Request):
    """Devuelve el grafo compilado o lanza 503 si `lifespan()` no pudo
    compilarlo — nunca un AttributeError/None crudo hacia el cliente."""
    graph = getattr(request.app.state, "agent_graph", None)
    if graph is None:
        error = getattr(request.app.state, "agent_error", "agent graph no disponible")
        raise HTTPException(
            status_code=503,
            detail=f"Co-piloto ERP no disponible — el grafo no compiló al arrancar: {error}",
        )
    return graph


def _base_config(thread_id: str, creds: TenantCredentials) -> Dict[str, Any]:
    return {"configurable": {"creds": creds, "thread_id": thread_id}}


def _final_answer_text(result: Dict[str, Any]) -> str:
    """Extrae el texto del último mensaje del estado final — mismo patrón
    que `scripts/verify_phase5_orchestration.py` usa para inspeccionar
    `result['messages']`."""
    messages = result.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    return str(getattr(last, "content", "") or "")


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(payload: AgentChatRequest, request: Request) -> AgentChatResponse:
    """Envía un mensaje al co-piloto ERP. Si el especialista invoca un tool
    interrupt-gated, el grafo pausa y la respuesta vuelve con
    `status="awaiting_confirmation"` — el cliente debe llamar
    `POST /agent/confirm` para aprobar/rechazar antes de seguir."""
    graph = _require_graph(request)

    creds = TenantCredentials(base_url=payload.tenant_base_url, token=payload.tenant_token)
    config = _base_config(payload.session_id, creds)

    state = {
        "messages": [{"role": "user", "content": payload.message}],
        "context_module": payload.context_module,
        "session_id": payload.session_id,
        "pending_confirmation": None,
        "handoff_reason": None,
    }

    try:
        result = await graph.ainvoke(state, config=config)
    except Exception as exc:  # noqa: BLE001 — nunca tirar 500 crudo al frontend de FacturadorPro7
        logger.exception("agent_chat: graph.ainvoke falló para session_id=%s", payload.session_id)
        raise HTTPException(status_code=502, detail=f"Error procesando el mensaje: {exc}") from exc

    pending = parse_interrupt_payload(result)
    if pending is not None:
        # Guarda las creds de ESTE request para que /agent/confirm pueda
        # reanudar dentro del mismo tool sin pedírselas de nuevo al cliente
        # (el contrato HTTP de design.md no las repite en
        # AgentConfirmRequest). Nunca se loguea ni se persiste a disco.
        _PENDING_CREDS[payload.session_id] = creds
        return AgentChatResponse(
            session_id=payload.session_id,
            status="awaiting_confirmation",
            answer=None,
            confirmation=pending,
        )

    _PENDING_CREDS.pop(payload.session_id, None)
    return AgentChatResponse(
        session_id=payload.session_id,
        status="answered",
        answer=_final_answer_text(result),
        confirmation=None,
    )


@router.post("/confirm", response_model=AgentChatResponse)
async def agent_confirm(payload: AgentConfirmRequest, request: Request) -> AgentChatResponse:
    """Resuelve una confirmación pendiente — aprueba o rechaza el último
    `interrupt()` del thread `payload.session_id` y reanuda el grafo justo
    después, dentro del mismo tool (design.md "Resume Continues Inside Same
    Tool Call")."""
    graph = _require_graph(request)

    creds = _PENDING_CREDS.get(payload.session_id)
    if creds is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"session_id '{payload.session_id}' no tiene una confirmación pendiente "
                "conocida por este proceso (puede haberse perdido en un restart — "
                "InMemorySaver no sobrevive al proceso, design.md "
                "'Pending Confirmation Survives Process Lifetime Only')."
            ),
        )

    resume_command = build_resume_command(approved=payload.approved)
    config = _base_config(payload.session_id, creds)

    try:
        result = await graph.ainvoke(resume_command, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent_confirm: graph.ainvoke(resume) falló para session_id=%s", payload.session_id)
        raise HTTPException(status_code=502, detail=f"Error reanudando la confirmación: {exc}") from exc

    pending = parse_interrupt_payload(result)
    if pending is not None:
        return AgentChatResponse(
            session_id=payload.session_id,
            status="awaiting_confirmation",
            answer=None,
            confirmation=pending,
        )

    _PENDING_CREDS.pop(payload.session_id, None)
    return AgentChatResponse(
        session_id=payload.session_id,
        status="answered",
        answer=_final_answer_text(result),
        confirmation=None,
    )


@router.get("/session/{session_id}")
async def agent_session(session_id: str, request: Request) -> Dict[str, Any]:
    """Lee el estado actual del thread sin mutarlo — `graph.aget_state()`,
    nunca `ainvoke`/`Command`, así no hay efecto secundario de resume/cancel
    (spec `erp-agent-api`, "Session State Read Endpoint")."""
    graph = _require_graph(request)

    snapshot = await graph.aget_state({"configurable": {"thread_id": session_id}})
    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail=f"session_id '{session_id}' no encontrada.")

    values = snapshot.values
    pending = None
    interrupts = getattr(snapshot, "interrupts", None) or ()
    if interrupts:
        pending = interrupts[0].value

    return {
        "session_id": session_id,
        "active_specialist": values.get("active_specialist"),
        "context_module": values.get("context_module"),
        "handoff_reason": values.get("handoff_reason"),
        "pending_confirmation": pending,
        "message_count": len(values.get("messages") or []),
    }
