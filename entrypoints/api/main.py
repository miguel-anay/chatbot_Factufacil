"""
Entry point — API REST.
Responsabilidad: ensamblar los adaptadores, exponer los endpoints HTTP.
No contiene lógica de negocio.
"""
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from adapters.llm.openai_compatible import OpenAICompatibleAdapter
from adapters.memory.window_memory_adapter import WindowMemoryAdapter
from adapters.rag.faiss_adapter import FAISSAdapter
from core.application.presales_service import ChatbotService
from core.domain import BotPersona
from core.application.orchestration.graph import build_graph
from entrypoints.api import agent_router
from entrypoints.api.schemas import ChatRequest, ChatResponse
from infrastructure.config import Config

logger = logging.getLogger(__name__)

# ── Ensamblado de dependencias (Composition Root) ──────────────────────────
# Acá es el ÚNICO lugar donde se eligen los adaptadores concretos.
# Para cambiar FAISS por OpenSearch: solo cambiar FAISSAdapter → OpenSearchAdapter.
# Para cambiar Qwen por GPT: solo cambiar OpenAICompatibleAdapter con otra config.

FACTUFACIL_PERSONA = BotPersona(
    name=Config.COMPANY_NAME,
    email=Config.COMPANY_EMAIL,
    phone=Config.COMPANY_PHONE,
    system_prompt="",  # el template vive en core/chatbot_service.py
)

chatbot: ChatbotService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global chatbot
    chatbot = ChatbotService(
        llm=OpenAICompatibleAdapter(),
        rag=FAISSAdapter(),
        memory=WindowMemoryAdapter(),
        persona=FACTUFACIL_PERSONA,
    )

    # Co-piloto ERP multiagente (additive) — design.md "Lifespan failure
    # isolation": la compilación del grafo va DESPUÉS de que `chatbot` ya
    # esté armado, envuelta en try/except. `lifespan()` es un único hook de
    # arranque compartido por toda la app — si esto explotara sin el
    # try/except, tumbaría el `/chat` existente (que no tiene nada que ver
    # con este cambio) junto con todo lo nuevo. Si falla, `/agent/*` queda
    # deshabilitado (503) pero `/chat`/`/health`/etc. arrancan igual.
    try:
        app.state.agent_graph = build_graph()
        app.state.agent_error = None
    except Exception as exc:  # noqa: BLE001 — nunca debe tumbar el lifespan
        app.state.agent_graph = None
        app.state.agent_error = str(exc)
        logger.exception("Agent graph no compiló — /agent/* deshabilitado, /chat no afectado")

    yield


# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Chatbot FactuFácil — Hexagonal Architecture",
    description="LangChain + FAISS + LLM con arquitectura hexagonal. Fácil migración a microservicios.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.CORS_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=False,
)

# Co-piloto ERP multiagente (additive) — design.md "Migration / Rollout":
# rollback = sacar esta línea, reiniciar, `/chat` sigue andando y `/agent/*`
# desaparece. No toca ninguna ruta/middleware/registro existente arriba.
app.include_router(agent_router.router, prefix="/agent", tags=["agent"])


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/", tags=["info"])
def root():
    return {
        "service": "Chatbot FactuFácil",
        "version": "2.0.0",
        "architecture": "hexagonal",
        "docs": "/docs",
    }


@app.post("/chat", response_model=ChatResponse, tags=["chat"])
def chat(request: ChatRequest):
    """Envía un mensaje. Guardá el session_id para mantener el contexto."""
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío.")
    result = chatbot.chat(message=request.message, session_id=request.session_id)
    return result


@app.get("/session/{session_id}", tags=["sessions"])
def get_session(session_id: str):
    return chatbot._memory.get_session_info(session_id)


@app.delete("/session/{session_id}", tags=["sessions"])
def clear_session(session_id: str):
    if not chatbot._memory.clear(session_id):
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")
    return {"cleared": True, "session_id": session_id}


@app.post("/rag/reindex", tags=["rag"])
def reindex():
    chatbot._rag.reindex()
    return {"message": "Índice RAG reconstruido exitosamente."}


@app.get("/rag/stats", tags=["rag"])
def rag_stats():
    return chatbot._rag.get_stats()


@app.get("/health", tags=["info"])
def health():
    return {
        "status": "ok",
        "model": Config.LLM_MODEL,
        "architecture": "hexagonal",
        "sessions_active": len(chatbot._memory.list_sessions()),
        "agent_available": app.state.agent_graph is not None,
        "agent_error": app.state.agent_error,
    }


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "entrypoints.api.main:app",
        host=Config.HOST,
        port=Config.PORT,
        reload=True,
    )
