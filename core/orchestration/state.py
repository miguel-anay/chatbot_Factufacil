"""
`AgentState` — esquema de estado del grafo supervisor→especialista
(design.md, "Data Flow"; plan: "`state.py` — `AgentState` (TypedDict) —
schema de estado del grafo").

CORRECCIÓN vs. el diseño original (ver nota de la orquestación al invocar
este apply): design.md fue escrito cuando existían 4 módulos
(compras/ventas/logistica/contabilidad). Phase 4 (ya mergeada) agregó un
quinto especialista: **inventario** (`core/agents/inventario_agent.py`,
`build_inventario_agent()`). Los 5 valores válidos de `context_module` /
`active_specialist` son por lo tanto:
    "inventario", "compras", "ventas", "logistica", "contabilidad"
Verificado contra el código real (no asumido): los 5 archivos
`core/agents/{inventario,compras,ventas,logistica,contabilidad}_agent.py`
existen y cada uno expone `build_<nombre>_agent()`.

Las credenciales de tenant (`TenantCredentials`) NUNCA viven en este
TypedDict — design.md ("Credential injection") y el patrón ya establecido en
Phase 3 (`core/agents/tools/_shared.py::InjectedConfig`) las hacen viajar
exclusivamente por `config["configurable"]["creds"]`. El motivo es el mismo
en ambas capas: este estado es lo que el checkpointer (`InMemorySaver` por
ahora, sqlite/postgres más adelante) puede persistir — nunca debe contener
un Bearer token.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, TypedDict

from langgraph.graph.message import add_messages

# Los 5 módulos especialistas reales (Phase 4, ya mergeada) — NO los 4
# originales de design.md. Cualquier `Literal` de módulo en este paquete de
# orquestación debe usar este alias para no volver a perder "inventario".
SpecialistModule = Literal["inventario", "compras", "ventas", "logistica", "contabilidad"]


class AgentState(TypedDict):
    """Estado del grafo LangGraph (`StateGraph(AgentState)`, ver graph.py).

    Campos (design.md, "Data Flow" + plan, "Orquestador / Supervisor"):
      - messages: historial de conversación, acumulado vía `add_messages`
        (reducer estándar de LangGraph — cada nodo retorna solo los mensajes
        NUEVOS, igual que `SpecialistAgent.ainvoke()` en `core/agents/base.py`
        ya hace).
      - context_module: hint del frontend de FacturadorPro7 (en qué módulo
        está parado el usuario). Si viene seteado y es uno de los 5 valores
        válidos, el supervisor lo usa como fast-path sin LLM.
      - active_specialist: a qué especialista se enrutó efectivamente esta
        invocación (puede coincidir con `context_module` en el fast-path, o
        ser el resultado del fallback `.with_structured_output()`).
      - session_id: identifica la conversación / `thread_id` del
        checkpointer — el HTTP layer (PR7) lo usa también como
        `thread_id` en `config["configurable"]`.
      - pending_confirmation: payload de la última pausa por `interrupt()`
        (tool_name/summary/tool_args) cuando el grafo está esperando una
        decisión humana — `None` si no hay confirmación pendiente. Lo arma
        el HTTP layer (PR7) a partir de `graph.invoke()`'s `__interrupt__`;
        no es el mecanismo de pausa en sí (eso es nativo de LangGraph), es
        solo un lugar para que el estado refleje "hay algo pendiente" si el
        router (PR7) decide guardarlo ahí en vez de derivarlo siempre de
        `__interrupt__`.
      - handoff_reason: por qué el supervisor enrutó a este especialista —
        cadena corta legible (ej. "context_module hint" o "clasificación LLM:
        <razonamiento>"), útil para debugging/logging, nunca para lógica de
        negocio.
    """

    messages: Annotated[list, add_messages]
    context_module: Optional[SpecialistModule]
    active_specialist: Optional[SpecialistModule]
    session_id: str
    pending_confirmation: Optional[dict]
    handoff_reason: Optional[str]
