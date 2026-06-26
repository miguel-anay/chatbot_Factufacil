"""
`SpecialistAgent` — base compartida para los 5 agentes especialistas del
co-piloto ERP (design.md, Phase 4).

DECISIÓN DE DISEÑO — `bind_tools()` + loop acotado, NO `create_react_agent`
prebuilt (plan/design.md: "`base.py`: SpecialistAgent — system prompt
assembly + bind_tools + bounded loop"): el ground truth
(`~/.claude/plans/si-hago-multiagente-lo-tingly-quilt.md`) describe
explícitamente `base.py` como "arma system prompt + bind_tools, loop
acotado" — no menciona `create_react_agent`. Se eligió `.bind_tools()` +
un loop manual y acotado (`max_iterations`) porque:
  1. Da control total sobre la construcción de mensajes y el límite de
     iteraciones sin depender de la implementación interna del prebuilt.
  2. El mismo cliente `ChatOpenAI` ya probado en Phase 0 (spike de
     tool-calling, `scripts/spike_smoke_test_toolcalling.py`) se reusa sin
     cambios — `.bind_tools()` es exactamente la llamada que ese spike ya
     verificó contra qwen-plus.
  3. El punto de integración de `interrupt()` (design.md, "Confirmation
     placement") vive DENTRO del cuerpo de cada tool de escritura — esto
     funciona idéntico bajo `bind_tools()`+loop manual o bajo
     `create_react_agent` prebuilt, así que no hay pérdida de
     funcionalidad al elegir la opción más simple/explícita.

Esta capa (`core/agents/*`) SÍ puede importar langchain-core/langgraph
(design.md, "Hexagonal boundary") — es application-services, no dominio
puro. `core/domain.py`/`core/ports.py` nunca importan estos frameworks.
"""
from __future__ import annotations

from typing import List

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from infrastructure.config import Config

DEFAULT_MAX_ITERATIONS = 6

# Guardrail de dominio compartido por los 5 especialistas. Se antepone a
# cada `system_prompt` concreto para que el co-piloto rechace consultas
# ajenas a la gestión del ERP de FactuFácil (clima, historia, chistes,
# cultura general, etc.) en vez de responderlas como un asistente genérico.
# Vive acá —y no duplicado en cada agente— para tener una sola fuente de
# verdad: cambiar el alcance del producto es editar este único bloque.
#
# NOTA de nombre: de cara al usuario el producto es "FactuFácil" (la empresa);
# "FacturadorPro7" es el ERP subyacente y NO debe aparecer en mensajes al
# usuario.
DOMAIN_GUARDRAIL = """\
ALCANCE (regla prioritaria, por encima de cualquier otra):
Sos el co-piloto de FactuFácil. Solo asistís con tareas de gestión del ERP \
(productos, stock, compras, proveedores, ventas, clientes, comprobantes, \
despacho/logística, contabilidad y reportes relacionados).
Si la consulta del usuario NO tiene relación con la operación del ERP, NO la \
respondas ni intentes resolverla, aunque sepas la respuesta. En su lugar, \
explicá brevemente que solo podés ayudar con tareas de FactuFácil y ofrecé 2 \
o 3 ejemplos concretos de lo que sí podés hacer. No inventes una conexión con \
el ERP donde no la hay. Nunca menciones "FacturadorPro7" al usuario.
"""


def build_llm_client() -> ChatOpenAI:
    """Construye el cliente `ChatOpenAI` con la MISMA configuración que
    `OpenAICompatibleAdapter` (`adapters/llm/openai_compatible.py`) y el
    spike de Phase 0 (`scripts/spike_smoke_test_toolcalling.py`) — mismas
    credenciales Qwen/DashScope ya verificadas para tool-calling, sin
    introducir configuración nueva."""
    kwargs: dict = {
        "model": Config.LLM_MODEL,
        "api_key": Config.LLM_API_KEY,
        "temperature": Config.LLM_TEMPERATURE,
    }
    if Config.LLM_BASE_URL:
        kwargs["base_url"] = Config.LLM_BASE_URL
    return ChatOpenAI(**kwargs)


class SpecialistAgent:
    """Agente especialista: prompt de sistema (en español) + subset de
    tools del dominio + loop acotado de tool-calling.

    Cada agente concreto (`inventario_agent.py`, `ventas_agent.py`, etc.)
    es una definición delgada: instancia esta clase con su propio
    `system_prompt` y su propia lista de tools — no duplica la lógica de
    "armar prompt + bind_tools + construir runnable" en cada archivo.
    """

    def __init__(self, name: str, system_prompt: str, tools: List[BaseTool]) -> None:
        if not tools:
            raise ValueError(f"SpecialistAgent '{name}' requiere al menos una tool.")
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools
        self._tools_by_name = {t.name: t for t in tools}
        self._llm = build_llm_client()
        self._llm_with_tools = self._llm.bind_tools(tools)

    @property
    def tool_names(self) -> List[str]:
        return [t.name for t in self.tools]

    async def ainvoke(
        self,
        messages: List[BaseMessage],
        config: RunnableConfig,
        *,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> List[BaseMessage]:
        """Ejecuta el loop acotado: LLM -> (tool_calls? -> ejecutar tools ->
        LLM otra vez) hasta que el LLM responda sin tool_calls o se agote
        `max_iterations`.

        `config` es el `RunnableConfig` de LangGraph que trae
        `configurable.creds` (TenantCredentials) — se propaga a cada tool
        vía `tool.ainvoke(args, config=config)`, nunca como argumento
        normal (design.md, "Credential injection").

        Devuelve la lista de mensajes NUEVOS generados en esta invocación
        (no incluye los `messages` de entrada) — pensado para que el nodo
        del grafo (Phase 5) los agregue al estado vía `add_messages`.

        Si una tool dispara `interrupt()`, la excepción de LangGraph se
        propaga tal cual (no se atrapa acá) — el grafo compilado es quien
        debe pausar/reanudar, esta clase no conoce ese mecanismo.
        """
        system_prompt = f"{DOMAIN_GUARDRAIL}\n{self.system_prompt}"
        conversation: List[BaseMessage] = [SystemMessage(content=system_prompt), *messages]
        new_messages: List[BaseMessage] = []

        for _ in range(max_iterations):
            response: AIMessage = await self._llm_with_tools.ainvoke(conversation, config=config)
            conversation.append(response)
            new_messages.append(response)

            if not response.tool_calls:
                break

            for call in response.tool_calls:
                tool_obj = self._tools_by_name.get(call["name"])
                if tool_obj is None:
                    tool_message = ToolMessage(
                        content=f"Error: la tool '{call['name']}' no existe en el agente '{self.name}'.",
                        tool_call_id=call["id"],
                    )
                else:
                    result = await tool_obj.ainvoke(call["args"], config=config)
                    tool_message = ToolMessage(content=str(result), tool_call_id=call["id"])
                conversation.append(tool_message)
                new_messages.append(tool_message)

        return new_messages
