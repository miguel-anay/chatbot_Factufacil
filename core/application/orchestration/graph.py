"""
`build_graph()` — compila el `StateGraph(AgentState)` que cablea el
supervisor con los 5 especialistas (design.md, "Data Flow"; plan,
"Orquestador / Supervisor").

Topología (design.md, "Multi-domain requests" — sin excepciones):

    supervisor --(edge condicional sobre active_specialist)--> {
        inventario, compras, ventas, logistica, contabilidad
    } --> END

Cada especialista enruta DIRECTO a `END` — NO hay aristas entre
especialistas. Un pedido multi-dominio en un mismo turno no se encadena
automáticamente (Compras→Logística, etc.): el especialista responde y
sugiere el siguiente paso como un turno nuevo del usuario. Esto es una
decisión explícita de design.md, no un olvido — encadenar escrituras
automáticas multiplicaría el blast radius de una sola confirmación humana.

Checkpointer: `InMemorySaver` (design.md ya marca migrar a
sqlite/postgres antes de salir a usuarios reales — no es trabajo de esta
PR). `build_graph()` es el único punto de entrada exportado — el futuro
entrypoint (PR7) lo llama una vez al arrancar (`lifespan()`), nunca importa
los internals de este módulo.
"""
from __future__ import annotations

from typing import Any, Dict

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from core.application.agents.compras_agent import build_compras_agent
from core.application.agents.contabilidad_agent import build_contabilidad_agent
from core.application.agents.inventario_agent import build_inventario_agent
from core.application.agents.logistica_agent import build_logistica_agent
from core.application.agents.ventas_agent import build_ventas_agent
from core.application.orchestration.state import AgentState
from core.application.orchestration.supervisor import supervisor_node

SUPERVISOR_NODE = "supervisor"

# Nombre de nodo == valor de `active_specialist` == nombre que usan los 5
# `build_<x>_agent()` de Phase 4 — se reusa el mismo string en los 3 lugares
# para que la arista condicional (`path_map`) sea una identidad trivial.
SPECIALIST_BUILDERS = {
    "inventario": build_inventario_agent,
    "compras": build_compras_agent,
    "ventas": build_ventas_agent,
    "logistica": build_logistica_agent,
    "contabilidad": build_contabilidad_agent,
}


def _route_to_specialist(state: AgentState) -> str:
    """Arista condicional desde `supervisor` — lee `active_specialist` (ya
    seteado por `supervisor_node`) y devuelve el nombre del nodo destino.

    No debería poder devolver un valor fuera de `SPECIALIST_BUILDERS` porque
    `supervisor_node` solo asigna valores de `SpecialistModule`/`VALID_MODULES`
    — se valida explícitamente para fallar con un mensaje claro en vez de un
    KeyError críptico si algún día eso deja de ser cierto."""
    specialist = state.get("active_specialist")
    if specialist not in SPECIALIST_BUILDERS:
        raise ValueError(
            f"active_specialist inválido o ausente tras supervisor_node: {specialist!r}. "
            f"Esperado uno de {sorted(SPECIALIST_BUILDERS)}."
        )
    return specialist


def _make_specialist_node(name: str):
    """Crea el callable de nodo para un especialista — envuelve
    `SpecialistAgent.ainvoke()` (Phase 4, `core/agents/base.py`) y devuelve
    solo los mensajes NUEVOS bajo la clave `messages`, que el reducer
    `add_messages` del estado (ver `state.py`) acumula sobre el historial.

    Construye el `SpecialistAgent` UNA SOLA VEZ por nodo, en tiempo de
    `build_graph()` (closure), no en cada invocación — los tools, no el
    agente, son quienes arman su adapter por-request a partir de
    `config.configurable.creds` (Phase 3, `_shared.py::build_client`). El
    agente en sí (su LLM cliente + bind_tools) no contiene credenciales de
    tenant, así que es seguro construirlo una sola vez como singleton de
    larga vida, igual que el grafo compilado."""
    agent = SPECIALIST_BUILDERS[name]()

    async def specialist_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
        new_messages: list[BaseMessage] = await agent.ainvoke(state["messages"], config)
        return {"messages": new_messages}

    specialist_node.__name__ = f"{name}_node"
    return specialist_node


def build_graph() -> CompiledStateGraph:
    """Construye y compila el grafo completo. Punto de entrada único — el
    entrypoint (PR7) llama esto una vez en el `lifespan()` existente y
    guarda el resultado compilado; no importa nada más de este paquete."""
    graph = StateGraph(AgentState)

    graph.add_node(SUPERVISOR_NODE, supervisor_node)
    for name in SPECIALIST_BUILDERS:
        graph.add_node(name, _make_specialist_node(name))
        # Cada especialista enruta directo a END — design.md, "Multi-domain
        # requests": sin aristas directas entre especialistas, nunca.
        graph.add_edge(name, END)

    graph.set_entry_point(SUPERVISOR_NODE)
    graph.add_conditional_edges(
        SUPERVISOR_NODE,
        _route_to_specialist,
        {name: name for name in SPECIALIST_BUILDERS},
    )

    return graph.compile(checkpointer=InMemorySaver())
