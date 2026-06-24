"""
Helpers de confirmación a nivel GRAFO — NO duplica `interrupt()`.

CORRECCIÓN DE DISEÑO (documentada también en design.md): el diseño original
de este archivo (plan + tasks.md 5.2) describía un
`require_confirmation(tool_name, tool_args, summary)` que ENVOLVERÍA
`interrupt()` para que cada tool de escritura lo llamara. Eso ya NO aplica —
se verificó leyendo el código real de Phase 3 (no se asumió):
`core/agents/tools/{sales,purchases,inventory,dispatch,finance}_tools.py`
ya llaman `langgraph.types.interrupt({...})` DIRECTO, inline, dentro del
cuerpo de cada tool de escritura (primera línea, antes del POST real), con
un payload `{"tool_name", "summary", "tool_args"}` consistente en las 8
ocurrencias. Construir un `require_confirmation()` en esta capa que
envolviera ese mismo `interrupt()` sería código muerto: ningún tool lo
llamaría (ya llaman a `interrupt()` directo) y reimplementar la misma firma
acá no le agrega nada al sistema — solo una capa de indirección que nadie
usa.

Lo que SÍ falta a nivel de grafo/orquestación, y es lo que este archivo
provee:

  1. `parse_interrupt_payload(invoke_result)`: cuando `graph.invoke(...)`
     pausa por un `interrupt()` dentro de un tool, LangGraph devuelve un
     dict de estado con una clave especial `"__interrupt__"` — una tupla de
     `langgraph.types.Interrupt` (cada uno con `.value` = el payload que el
     tool pasó a `interrupt(...)` y `.id` = id único de esa pausa). El HTTP
     layer (PR7, `POST /agent/chat`) necesita traducir eso a la forma
     `{tool_name, summary, tool_args}` documentada en el contrato HTTP de
     design.md ("Data Flow") sin reimplementar el parsing en cada handler.
  2. `build_resume_command(approved)`: traduce el payload `{"approved": bool}`
     que el futuro `POST /agent/confirm` recibe en el resume real de
     LangGraph — `Command(resume={"approved": approved})` — el mismo shape
     que cada tool ya espera leer vía `decision.get("approved")` (ver
     `sales_tools.py::confirmar_y_generar_cpe` y análogos). Esto es solo un
     constructor con nombre — `Command(resume=...)` ya es la API pública de
     LangGraph, no hay nada que "envolver" funcionalmente, pero nombrar la
     construcción acá evita que el handler HTTP (PR7) arme el dict a mano en
     cada lugar y se desincronice del shape real que los tools leen.

Ninguna de las dos funciones llama a `interrupt()` ni a un tool — viven
estrictamente del lado de "antes de invocar"/"después de que el grafo pausó",
nunca dentro del cuerpo de un tool.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from langgraph.types import Command, Interrupt

# Shape que YA usan los 8 sitios de `interrupt()` en core/agents/tools/*.py —
# documentado acá para que el HTTP layer (PR7) no tenga que adivinarlo.
ConfirmationPayload = Dict[str, Any]  # {"tool_name": str, "summary": str, "tool_args": dict}


def parse_interrupt_payload(invoke_result: Dict[str, Any]) -> Optional[ConfirmationPayload]:
    """Extrae el payload de confirmación pendiente de un resultado de
    `graph.invoke(...)` (o `await graph.ainvoke(...)`), si el grafo pausó.

    Devuelve `None` si no hay ningún `interrupt()` pendiente (el grafo
    terminó normalmente). Si hay uno o más, devuelve el payload (`.value`)
    del PRIMERO — en el diseño actual (design.md, "Confirmation placement")
    cada turno dispara a lo sumo un `interrupt()` por especialista, así que
    no hace falta resolver múltiples pausas simultáneas en esta capa todavía.

    No asume la forma de `invoke_result["__interrupt__"]` sin chequear —
    LangGraph puede devolver una tupla vacía si no hay interrupciones."""
    interrupts: Tuple[Interrupt, ...] = invoke_result.get("__interrupt__", ()) or ()
    if not interrupts:
        return None
    payload = interrupts[0].value
    if not isinstance(payload, dict):
        # Defensivo: si algún tool futuro pasara un valor no-dict a
        # interrupt(), no rompemos el HTTP layer — lo envolvemos en el shape
        # esperado con el valor crudo disponible para debugging.
        return {"tool_name": None, "summary": str(payload), "tool_args": None}
    return payload


def build_resume_command(approved: bool) -> Command:
    """Construye el `Command(resume=...)` para reanudar el grafo después de
    una decisión humana — mismo shape `{"approved": bool}` que cada tool
    interrupt-gated ya lee (`decision.get("approved")`, ver
    `sales_tools.py`, `purchases_tools.py`, `inventory_tools.py`,
    `dispatch_tools.py`, `finance_tools.py`).

    Usar SIEMPRE este helper en el handler de `POST /agent/confirm` (PR7) en
    vez de armar `Command(resume={...})` a mano en cada lugar — si el shape
    que los tools esperan cambia algún día, se actualiza una sola vez acá."""
    return Command(resume={"approved": approved})
