# Chatbot FactuFácil — LLM + RAG con LangChain

**Diploma AI Engineer — Diseño e Implementación de Chatbots**  
Proyecto Final · Módulo: Diseño e Implementación de Chatbots

---

## 1. Objetivo de la Evaluación

Desarrollar un agente conversacional inteligente aplicando **LLM** (Large Language Models) y **RAG** (Retrieval-Augmented Generation) para demostrar integración funcional entre recuperación de información, generación de respuestas naturales y manejo de alucinaciones.

**Aplicación:** asistente virtual para [FactuFácil](https://factufacil.pe), sistema de facturación electrónica peruano. Responde 24/7 consultas sobre planes, precios, funcionalidades e integración con SUNAT, manteniendo contexto conversacional entre turnos.

---

## 2. Características del Producto Tecnológico

- Chatbot inteligente basado en **LLM + RAG** sobre base de conocimiento de FactuFácil.
- Responde consultas usando **13 documentos estructurados** con información real del sitio.
- Orquestación de intenciones y entidades con **LangChain** (no Rasa — el LLM entiende el lenguaje de forma nativa).
- Integración de LLM via **Alibaba DashScope (Qwen3)** con fallback a OpenAI — misma interfaz `ChatOpenAI`.
- **Embeddings multilingüe** locales (`paraphrase-multilingual-MiniLM-L12-v2`), vectorización con `RecursiveCharacterTextSplitter`, indexación y búsqueda semántica con **FAISS**.
- FAISS opera localmente en lugar de OpenSearch — misma calidad de búsqueda semántica, sin dependencias de servidor para el entorno académico.
- **Manejo de alucinaciones**: el prompt ancla cada respuesta al contexto recuperado; si no hay suficiente contexto, el modelo deriva a `ventas@factufacil.pe` en lugar de inventar.

---

## 3. Requisitos Técnicos

| Requisito | Implementación |
|-----------|---------------|
| Lenguaje Python | Python 3.10+ |
| LangChain obligatorio | `langchain >= 0.2`, `langchain-community`, `langchain-openai` |
| Motor de embeddings / vector database | FAISS local + `sentence-transformers` |
| Código estructurado | Arquitectura hexagonal (v2.0): `core/`, `adapters/`, `infrastructure/`, `entrypoints/` |
| README con instrucciones | Este documento |
| Diagrama de arquitectura | Sección 5 |

---

## 4. Desarrollo del Proyecto

| Sesión | Objetivo | Entregable |
|--------|----------|-----------|
| **5** | Núcleo LLM + demo básica | `chatbot_service.py`, `main.py` — conversación funcional sin RAG |
| **6** | Embeddings, indexación y búsqueda semántica | `rag_system.py` — FAISS + `sentence-transformers` integrado |
| **7** | Chatbot completo con RAG | Integración final, tests, arquitectura hexagonal (v2.0) |

---

## 5. Arquitectura del Sistema

> 📐 Referencia completa de capas, reglas de dependencia y decisiones de diseño: **[docs/ARQUITECTURA.md](docs/ARQUITECTURA.md)**.

```
Usuario
  │
  ▼
FastAPI  (main.py / entrypoints/api/main.py — puerto 8000)
  │
  ▼
ChatbotService  (core/chatbot_service.py)
  │
  ├──► RAGPort → FAISSAdapter  (adapters/rag/faiss_adapter.py)
  │       ├── HuggingFace Embeddings
  │       │     paraphrase-multilingual-MiniLM-L12-v2
  │       ├── FAISS Vector Store  (data/faiss_index/)
  │       └── RecursiveCharacterTextSplitter
  │             chunk_size=500 / overlap=50
  │
  ├──► MemoryPort → WindowMemoryAdapter  (adapters/memory/)
  │       ConversationBufferWindowMemory (k=8 turnos por sesión)
  │
  └──► LLMPort → OpenAICompatibleAdapter  (adapters/llm/)
         Qwen3 via Alibaba DashScope (OpenAI-compatible)
         o OpenAI directamente
```

**Flujo por mensaje:**

```
1. POST /chat  →  ChatbotService.chat()
2. RAGPort.retrieve(query, k=4)       ← búsqueda semántica en FAISS
3. Armar prompt:
       [sistema] + [contexto RAG] + [historial] + [pregunta]
4. LLMPort.generate(prompt)           ← Qwen3 / GPT genera respuesta
5. MemoryPort.save_turn()             ← guardar turno en memoria de sesión
6. Retornar: answer + sources + session_id
```

### Estructura del proyecto

```
proyecto_final_factufacil/
│
├── core/                         ← DOMINIO (sin dependencias externas)
│   ├── domain.py                   entidades: ChatMessage, ChatResponse, BotPersona
│   ├── ports.py                    interfaces: LLMPort, RAGPort, MemoryPort
│   └── chatbot_service.py          lógica de negocio pura
│
├── adapters/                     ← INFRAESTRUCTURA (implementan los puertos)
│   ├── llm/openai_compatible.py    → LLMPort con Qwen3/GPT
│   ├── rag/faiss_adapter.py        → RAGPort con FAISS
│   └── memory/window_memory_adapter.py → MemoryPort en RAM
│
├── infrastructure/               ← CONFIGURACIÓN Y DATOS
│   ├── config.py                   constantes y variables de entorno
│   └── knowledge_base.py           13 documentos de FactuFácil
│
├── entrypoints/api/              ← ENTRADA HTTP
│   ├── main.py                     Composition Root — ensambla adaptadores
│   └── schemas.py                  modelos Pydantic de request/response
│
├── main.py                       ← versión v1 (sin hexagonal)
├── chatbot_service.py            ← versión v1
├── rag_system.py                 ← versión v1
├── knowledge_base.py             ← versión v1
├── config.py                     ← versión v1
├── test_chatbot.py               ← tests funcionales
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── data/faiss_index/             ← generado automáticamente al iniciar
```

---

## 6. Herramientas y Tecnologías Utilizadas

| Tecnología | Versión | Rol |
|-----------|---------|-----|
| Python | 3.10+ | Lenguaje principal |
| LangChain | >= 0.2 | Orquestación LLM + memoria |
| FAISS | >= 1.7.4 | Vector store local |
| sentence-transformers | >= 2.3.1 | Embeddings multilingüe (sin API key) |
| FastAPI | >= 0.110 | API REST |
| Uvicorn | >= 0.27 | Servidor ASGI |
| Qwen3 (DashScope) | — | LLM generativo principal |
| OpenAI GPT | — | LLM alternativo |
| Docker / docker-compose | — | Containerización |

---

## 7. Instalación y Ejecución

### Requisitos previos

- Python 3.10+
- `ALIBABA_API_KEY` (Alibaba DashScope) o `OPENAI_API_KEY`

### Instalación local

```bash
# 1. Clonar / entrar al directorio
cd proyecto_final_factufacil

# 2. Entorno virtual
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Dependencias
pip install -r requirements.txt

# 4. Variables de entorno
cp .env.example .env
# Editá .env y agregá ALIBABA_API_KEY o OPENAI_API_KEY
```

### Ejecución

```bash
# Arranque (dev, con reload)
python run.py

# Equivalente directo
python entrypoints/api/main.py

# Con uvicorn
uvicorn entrypoints.api.main:app --reload --host 0.0.0.0 --port 8000
```

El índice FAISS se construye automáticamente en el primer arranque (~30 s). **Swagger UI:** http://localhost:8000/docs

### Con Docker

```bash
docker-compose up --build
```

El servicio queda expuesto en `http://localhost:8000`.

---

## 8. Uso de la API

### Nuevo chat

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "¿Cuánto cuesta el plan PRO?"}'
```

### Continuar conversación (mantener contexto)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "¿Y qué diferencia tiene con el Básico?", "session_id": "TU_SESSION_ID"}'
```

### Respuesta

```json
{
  "session_id": "f3a1b2c4-...",
  "answer": "El plan PRO cuesta S/.95 al mes (S/.950 al año) y es el más popular...",
  "sources": [
    { "category": "precios", "topic": "planes", "excerpt": "Plan PRO — S/.95 por mes..." }
  ],
  "message_count": 1
}
```

### Otros endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/health` | Estado del servicio |
| `GET` | `/rag/stats` | Info del índice FAISS |
| `POST` | `/rag/reindex` | Reconstruir índice RAG |
| `GET` | `/session/{id}` | Info de una sesión |
| `DELETE` | `/session/{id}` | Limpiar historial de sesión |

---

## 9. Tests

```bash
# Con el servidor corriendo en :8000
python test_chatbot.py
```

Cubre 5 grupos: información general, planes y precios, funcionalidades, memoria conversacional y manejo de alucinaciones.

---

## 10. Manejo de Alucinaciones y Seguridad

- El prompt instruye al LLM a responder **únicamente** con información del contexto recuperado por RAG.
- Si el contexto no contiene la respuesta, el modelo indica contactar a `ventas@factufacil.pe` o `+51 964 979 320` en lugar de inventar.
- Las fuentes usadas (`sources`) se devuelven en cada respuesta para trazabilidad completa.
- CORS configurado con orígenes explícitos (`CORS_ORIGINS` en `.env`) — no `allow_origins=["*"]`.

---

## 11. Entregables

- [x] Repositorio GitHub con código fuente completo
- [x] README con pasos de instalación y ejecución (este documento)
- [x] Diagrama de arquitectura (sección 5)
- [x] Documentación técnica extendida (`DETALLE.md`)
- [x] Tests funcionales (`test_chatbot.py`)
- [x] Dockerfile y `docker-compose.yml`
- [ ] Notebook en Google Colab / Jupyter
- [ ] Diapositivas de presentación final

---

## 12. Conclusiones y Mejoras Futuras

**Conclusiones:**
- LangChain + FAISS local es suficiente para un chatbot de soporte/ventas con calidad de búsqueda semántica real.
- La arquitectura hexagonal hace que cambiar de FAISS a OpenSearch, o de Qwen a GPT, sea un swap de una línea en el Composition Root.
- Los embeddings multilingüe locales (`paraphrase-multilingual-MiniLM-L12-v2`) eliminan costos de API y funcionan bien en español.

**Mejoras futuras:**
- Reemplazar FAISS por OpenSearch para búsqueda distribuida en producción.
- Agregar reranking semántico (cross-encoder) para mayor precisión RAG.
- Implementar streaming de respuestas (`StreamingResponse`) para UX en tiempo real.
- Integración con Telegram o WhatsApp como canal de atención.
- Panel de administración para actualizar la base de conocimiento sin tocar código.
