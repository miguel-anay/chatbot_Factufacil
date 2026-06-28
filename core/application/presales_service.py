"""
Servicio de APLICACIÓN (caso de uso) del chatbot de preventa.
Orquesta los puertos del dominio (LLM, RAG, memoria) para resolver un turno
de conversación. No importa LangChain, FAISS, FastAPI ni ninguna librería de
infraestructura — solo habla con los puertos definidos en core/ports.py.
"""
import uuid
from typing import Optional

from core.domain import BotPersona, ChatMessage, ChatResponse
from core.ports import LLMPort, MemoryPort, RAGPort

GUARDRAIL_RESPONSE = "Solo puedo ayudarte con consultas sobre FactuFácil y FacturadorPro7."

PROMPT_TEMPLATE = """\
Sos el asistente virtual de {name}.

Tu misión es ayudar a los usuarios con información sobre el sistema.

Reglas:
1. Respondé SIEMPRE en español, de forma amigable y profesional.
2. Usá ÚNICAMENTE la información del contexto proporcionado.
3. Si no tenés información suficiente, indicá contactar a {email} o al {phone}.
4. NUNCA inventes precios, características ni datos fuera del contexto.
5. Sé conciso pero completo. Máximo 3 oraciones salvo que el usuario pida más detalle.
6. Si la pregunta NO está relacionada con FactuFácil, FacturadorPro7, facturación electrónica o SUNAT, respondé ÚNICAMENTE: "Solo puedo ayudarte con consultas sobre FactuFácil y FacturadorPro7."

--- CONTEXTO ---
{context}

--- HISTORIAL ---
{history}

Usuario: {message}
Asistente:\
"""


class ChatbotService:
    """
    Núcleo del chatbot. Completamente agnóstico de infraestructura.

    Puede usarse con cualquier combinación de adaptadores:
        - LLM: Qwen, GPT, Llama, mock para tests
        - RAG: FAISS, OpenSearch, Pinecone, mock para tests
        - Memory: ventana deslizante, Redis, in-memory, mock para tests
    """

    def __init__(
        self,
        llm: LLMPort,
        rag: RAGPort,
        memory: MemoryPort,
        persona: BotPersona,
    ) -> None:
        self._llm = llm
        self._rag = rag
        self._memory = memory
        self._persona = persona

    def chat(self, message: str, session_id: Optional[str] = None) -> ChatResponse:
        if not session_id:
            session_id = str(uuid.uuid4())

        # 1. Recuperar contexto semántico — filtra por score de relevancia.
        # Si FAISS no encuentra ningún doc por debajo del umbral, la query
        # está fuera del dominio: devolvemos la respuesta fija sin gastar tokens de LLM.
        docs = self._rag.retrieve(message)
        if not docs:
            return ChatResponse(
                session_id=session_id,
                answer=GUARDRAIL_RESPONSE,
                sources=[],
                message_count=self._memory.get_session_info(session_id).get("message_count", 0),
            )

        context = "\n\n---\n\n".join(d.content for d in docs)

        # 2. Recuperar historial de sesión
        history = self._memory.get_history(session_id)
        history_text = self._format_history(history)

        # 3. Construir prompt
        prompt = PROMPT_TEMPLATE.format(
            name=self._persona.name,
            email=self._persona.email,
            phone=self._persona.phone,
            context=context,
            history=history_text,
            message=message,
        )

        # 4. Generar respuesta
        answer = self._llm.generate(prompt)

        # 5. Persistir turno en memoria
        self._memory.save_turn(session_id, message, answer)

        sources = [
            {"category": d.category, "topic": d.topic, "excerpt": d.content[:120] + "..."}
            for d in docs
        ]
        info = self._memory.get_session_info(session_id)

        return ChatResponse(
            session_id=session_id,
            answer=answer,
            sources=sources,
            message_count=info.get("message_count", 0),
        )

    @staticmethod
    def _format_history(messages: list[ChatMessage]) -> str:
        if not messages:
            return "Sin historial previo."
        lines = [
            f"{'Usuario' if m.role == 'user' else 'Asistente'}: {m.content}"
            for m in messages
        ]
        return "\n".join(lines)
