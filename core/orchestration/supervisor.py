"""
`supervisor_node` — routing del grafo hacia uno de los 5 especialistas
(design.md, "Routing"; plan, "Orquestador / Supervisor").

Dos caminos, en este orden:
  1. Fast-path SIN LLM: si `state["context_module"]` ya viene seteado con uno
     de los 5 valores válidos (el frontend de FacturadorPro7 sabe en qué
     módulo está parado el usuario), se usa directo como `active_specialist`.
  2. Fallback CON un único LLM call: si no viene o es inválido, se clasifica
     el último mensaje humano con `.with_structured_output()` sobre un
     `Literal` de los 5 módulos — nunca se adivina ni se hardcodea un default
     fijo (ej. "ventas" a ciegas).

CORRECCIÓN aplicada (ver `state.py`): el `Literal` de routing tiene los 5
módulos reales post-Phase 4 (`inventario` incluido), no los 4 originales de
design.md.

Reuso explícito: `build_llm_client()` de `core/agents/base.py` (Phase 4) —
mismas credenciales Qwen/DashScope, mismo cliente `ChatOpenAI` ya probado
para tool-calling. No se construye un cliente nuevo acá; el supervisor no
necesita tools, solo `.with_structured_output()` sobre el mismo modelo.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from core.agents.base import build_llm_client
from core.orchestration.state import AgentState, SpecialistModule

VALID_MODULES = {"inventario", "compras", "ventas", "logistica", "contabilidad"}


class RouteDecision(BaseModel):
    """Esquema de salida estructurada para el fallback de clasificación.

    `module` está restringido al mismo `Literal` de 5 valores que
    `SpecialistModule` — Pydantic/`.with_structured_output()` rechaza
    cualquier otro valor que el LLM intente inventar."""

    module: SpecialistModule = Field(
        description=(
            "El módulo del ERP FacturadorPro7 al que corresponde la "
            "consulta del usuario: 'inventario' (catálogo/stock de "
            "productos), 'compras' (proveedores y compras), 'ventas' "
            "(clientes y notas de venta/CPE), 'logistica' (guías de "
            "remisión/despacho) o 'contabilidad' (retenciones, "
            "percepciones, caja, reportes)."
        )
    )
    reasoning: str = Field(description="Razón breve (una frase) de la clasificación elegida.")


def _last_human_text(messages: list[BaseMessage]) -> str:
    """Extrae el contenido del último `HumanMessage` de la conversación —
    insumo para el fallback de clasificación. Si no hay ninguno (estado
    inicial inusual), devuelve cadena vacía en vez de fallar."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def _classify_with_llm(messages: list[BaseMessage], config: Optional[RunnableConfig]) -> RouteDecision:
    """Único LLM call del fallback — `.with_structured_output()` sobre
    `RouteDecision`, reusando el mismo cliente que los especialistas
    (`build_llm_client()`, Phase 4). No se hand-rollea un cliente nuevo."""
    llm = build_llm_client()
    classifier = llm.with_structured_output(RouteDecision)
    user_text = _last_human_text(messages)
    prompt = (
        "Clasificá el siguiente mensaje de un usuario del ERP FacturadorPro7 "
        "en uno de los 5 módulos disponibles (inventario, compras, ventas, "
        "logistica, contabilidad). Mensaje del usuario:\n\n"
        f"{user_text}"
    )
    result = classifier.invoke(prompt, config=config)
    # `.with_structured_output()` con un modelo Pydantic devuelve una
    # instancia de ese modelo en esta versión de langchain-core (1.4.0) — se
    # valida defensivamente por si el proveedor alguna vez devuelve un dict.
    if isinstance(result, RouteDecision):
        return result
    return RouteDecision.model_validate(result)


def supervisor_node(state: AgentState, config: Optional[RunnableConfig] = None) -> Dict[str, Any]:
    """Nodo del grafo (`StateGraph(AgentState)`, ver graph.py) — decide
    `active_specialist` y `handoff_reason`. NO toca `messages` (devuelve un
    dict parcial; LangGraph mergea el resto del estado sin pisar lo que no
    se retorna acá).

    Firma `(state, config)` — la firma estándar de un nodo LangGraph; `config`
    se acepta aunque el fast-path no lo necesite, porque el fallback SÍ lo
    propaga al LLM call (mismo patrón que `SpecialistAgent.ainvoke()`)."""
    context_module = state.get("context_module")

    if context_module in VALID_MODULES:
        return {
            "active_specialist": context_module,
            "handoff_reason": f"context_module hint: '{context_module}'",
        }

    decision = _classify_with_llm(state["messages"], config)
    return {
        "active_specialist": decision.module,
        "handoff_reason": f"LLM classification: {decision.reasoning}",
    }
