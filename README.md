# Co-piloto ERP FactuFácil — multiagente con LangGraph

**Asistente conversacional embebido en el ERP [FacturadorPro7](https://factufacil.pe)**, construido con **LangGraph** (orquestación multiagente) sobre **arquitectura hexagonal**. Un supervisor enruta cada consulta al especialista correcto (ventas, compras, inventario, contabilidad, logística), y toda escritura pasa por **confirmación humana** antes de tocar el ERP.

> 🚀 **¿Recién llegás al repo?** Empezá por **[ONBOARDING.md](ONBOARDING.md)** —
> setup, cómo correrlo, mapa del código y cómo trabajamos (ramas, PRs, roadmap).

> 🎓 **Origen académico:** este proyecto nació como el trabajo final del diploma
> *Diseño e Implementación de Chatbots* (un chatbot LLM + RAG de preventa). Ese
> entregable sigue vivo como uno de los dos canales — ver [sección 9](#9-origen-académico--chatbot-de-preventa-rag).

---

## 1. Visión

FacturadorPro7 es un ERP de facturación electrónica peruano. El operador pasa el
día entre módulos (inventario, compras, ventas, guías de remisión, contabilidad)
ejecutando tareas repetitivas. La idea de este proyecto es ponerle al lado un
**co-piloto conversacional** que entienda en qué módulo está parado, resuelva
consultas y prepare acciones — **sin reemplazar el criterio humano en las
escrituras**.

Dos canales, un mismo proceso FastAPI:

| Canal | Para qué | Entrada HTTP | Núcleo |
|-------|----------|--------------|--------|
| **Co-piloto ERP** (foco actual) | Asiste dentro del ERP: ventas, compras, inventario, contabilidad, logística. Multiagente con LangGraph. | `POST /agent/chat`, `POST /agent/confirm` | `core/application/orchestration/` + `core/application/agents/` |
| **Chatbot de preventa (RAG)** | Responde 24/7 sobre planes, precios, SUNAT, etc. usando RAG sobre la base de conocimiento. Es el entregable académico original. | `POST /chat` | `core/application/presales_service.py` |

El co-piloto ERP es **aditivo**: si su grafo no compila al arrancar, `/agent/*`
devuelve `503` y el chatbot de preventa sigue funcionando intacto.

---

## 2. Cómo funciona el co-piloto (multiagente)

```
                          POST /agent/chat
                                │
                                ▼
                      ┌──────────────────┐
                      │    SUPERVISOR    │   decide active_specialist
                      └──────────────────┘
            ┌──────────┬──────────┼──────────┬───────────┐
            ▼          ▼          ▼          ▼           ▼
        inventario  compras    ventas    logistica  contabilidad
            │          │          │          │           │
            └──────────┴────── cada uno ──────┴───────────┘
                                │
                                ▼
                               END
```

- **Supervisor (routing).** Dos caminos en orden:
  1. **Fast-path sin LLM** — si el frontend del ERP manda `context_module`
     (sabe en qué pantalla está el usuario), se usa directo. Cero costo de
     tokens.
  2. **Fallback con un único LLM call** — clasifica el último mensaje con
     `.with_structured_output()` sobre un `Literal` de los 5 módulos. Nunca
     adivina ni hardcodea un default.
- **5 especialistas.** `inventario`, `compras`, `ventas`, `logistica`,
  `contabilidad`. Cada uno tiene sus `tools` que llaman a la API de
  FacturadorPro7 (`adapters/facturadorpro7_api/`).
- **Sin encadenamiento automático.** Cada especialista va **directo a `END`**;
  no hay aristas entre especialistas. Un pedido multi-dominio se resuelve como
  turnos separados — encadenar escrituras automáticas multiplicaría el *blast
  radius* de una sola confirmación humana. Es una decisión de diseño, no un
  olvido.

---

## 3. Confirmación humana — la línea roja

Toda acción que **modifica** datos del ERP (crear una nota de venta, mover
stock, registrar una compra…) está *interrupt-gated*:

```
POST /agent/chat   →  el especialista invoca un tool de escritura
                   →  interrupt(): el grafo PAUSA
                   →  responde { status: "awaiting_confirmation", confirmation: {...} }

POST /agent/confirm { session_id, approved }
                   →  reanuda DENTRO del mismo tool
                   →  approved=true: ejecuta contra el ERP
                      approved=false: cancela, no escribe nada
```

Reglas no negociables:

- **El agente nunca escribe solo.** Sin un `POST /agent/confirm` con
  `approved=true`, no hay efecto en el ERP.
- **El agente no toca la base de datos directamente** — entra siempre por
  `ports` / `adapters`.
- **Credenciales por request, nunca persistidas.** Cada `/agent/chat` trae
  `tenant_base_url` + `tenant_token`; viven en memoria del proceso mientras hay
  una confirmación pendiente y mueren con él. No van a `.env`, ni a disco, ni a
  logs, ni al estado del checkpointer.
- **LangGraph confinado.** Solo vive en `core/application/orchestration/`; no se
  filtra al dominio ni a los entrypoints.

---

## 4. Arquitectura (hexagonal)

> 📐 Referencia completa: **[docs/ARQUITECTURA.md](docs/ARQUITECTURA.md)** ·
> cómo conviven LangGraph y hexagonal:
> **[docs/langgraph-y-arquitectura-hexagonal.md](docs/langgraph-y-arquitectura-hexagonal.md)**.

La regla de oro: **las flechas de dependencia apuntan siempre hacia adentro**.
El `core/` (dominio + aplicación) no conoce FAISS, ni LangGraph, ni la API del
ERP. Eso vive en `adapters/`. Cambiar FAISS por OpenSearch, o Qwen por GPT, es un
swap de una línea en el Composition Root.

```
proyecto_final_factufacil/
│
├── core/                              ← DOMINIO + APLICACIÓN (sin infra)
│   ├── domain.py                        entidades: ChatMessage, ChatResponse, BotPersona
│   ├── ports.py                         interfaces: LLMPort, RAGPort, MemoryPort
│   └── application/
│       ├── presales_service.py          canal preventa — chatbot RAG
│       ├── orchestration/               co-piloto ERP — grafo LangGraph
│       │   ├── graph.py                   build_graph(): supervisor + 5 especialistas
│       │   ├── supervisor.py              routing (fast-path + fallback LLM)
│       │   ├── state.py                   AgentState que viaja entre nodos
│       │   └── confirmation.py            interrupt() / resume para escrituras
│       └── agents/                      especialistas + tools que llaman al ERP
│           ├── ventas_agent.py · compras_agent.py · inventario_agent.py
│           ├── contabilidad_agent.py · logistica_agent.py
│           └── tools/                     items, inventory, sales, purchases, …
│
├── adapters/                          ← INFRAESTRUCTURA (implementan los ports)
│   ├── llm/openai_compatible.py         → LLMPort con Qwen3/GPT
│   ├── rag/faiss_adapter.py             → RAGPort con FAISS
│   ├── memory/window_memory_adapter.py  → MemoryPort en RAM (k=8 turnos)
│   └── facturadorpro7_api/              → cliente HTTP + adapters del ERP
│
├── infrastructure/                    ← CONFIGURACIÓN Y DATOS
│   ├── config.py                        constantes y variables de entorno
│   └── knowledge_base.py                13 documentos de FactuFácil (RAG preventa)
│
├── entrypoints/api/                   ← ENTRADA HTTP (Composition Root)
│   ├── main.py                          ensambla todo, expone /chat, /health, /rag/*
│   ├── agent_router.py                  expone /agent/chat, /agent/confirm, /agent/session
│   └── schemas.py                       modelos Pydantic request/response
│
├── docs/                              ← guías de arquitectura y planes de roadmap
├── openspec/                          ← artefactos SDD (propuestas, specs, diseño, tareas)
├── run.py · test_chatbot.py · requirements.txt · Dockerfile · docker-compose.yml
└── data/faiss_index/                 ← generado automáticamente al iniciar
```

---

## 5. Stack tecnológico

| Tecnología | Versión | Rol |
|-----------|---------|-----|
| Python | 3.10+ (venv en 3.12) | Lenguaje principal |
| LangGraph | 1.2.0 | Orquestación multiagente (supervisor + especialistas) |
| LangChain | core 1.4.0 · openai 1.2.1 | Clientes LLM, tool-calling, structured output |
| FAISS | >= 1.7.4 | Vector store local (canal preventa) |
| sentence-transformers | >= 2.3.1 | Embeddings multilingüe (sin API key) |
| FastAPI / Uvicorn | >= 0.110 / >= 0.27 | API REST |
| httpx | 0.28.1 | Cliente HTTP hacia la API del ERP |
| Qwen3 (DashScope) / OpenAI GPT | — | LLM generativo (OpenAI-compatible) |
| Docker / docker-compose | — | Containerización |

> Las dependencias del co-piloto van con **versión exacta** (no rangos) — ver el
> comentario en `requirements.txt`: un rango deja que pip resuelva algo nunca
> probado.

---

## 6. Instalación y ejecución

### Requisitos previos

- **Python 3.10+**
- Una API key de LLM: `ALIBABA_API_KEY` (DashScope / Qwen3) **o** `OPENAI_API_KEY`
- Para el co-piloto ERP: credenciales de un tenant FacturadorPro7
  (`base_url` + `token`), que se pasan **por request**, no en `.env`.

### Local

```bash
cd proyecto_final_factufacil
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # completá ALIBABA_API_KEY o OPENAI_API_KEY
```

```bash
python run.py
# Equivalente: uvicorn entrypoints.api.main:app --reload --host 0.0.0.0 --port 8000
```

El índice FAISS se construye solo en el primer arranque (~30 s).
**Swagger UI:** http://localhost:8000/docs · `GET /health` reporta el modelo y si
el co-piloto (`agent_available`) compiló.

### Docker

```bash
docker-compose up --build          # expone http://localhost:8000
```

---

## 7. Uso de la API

### Co-piloto ERP

```bash
# 1. Mensaje al co-piloto (las creds del tenant van por request)
curl -X POST http://localhost:8000/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
        "session_id": "demo-1",
        "message": "registrá una nota de venta para el cliente X",
        "context_module": "ventas",
        "tenant_base_url": "https://midominio.facturadorpro7.com",
        "tenant_token": "TOKEN_DEL_TENANT"
      }'
# → si el especialista invoca una escritura, responde:
#   { "status": "awaiting_confirmation", "confirmation": { ... } }

# 2. Confirmar (o rechazar) la escritura pendiente
curl -X POST http://localhost:8000/agent/confirm \
  -H "Content-Type: application/json" \
  -d '{ "session_id": "demo-1", "approved": true }'
```

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/agent/chat` | Mensaje al co-piloto; puede pausar pidiendo confirmación |
| `POST` | `/agent/confirm` | Aprueba/rechaza una escritura pendiente y reanuda |
| `GET` | `/agent/session/{id}` | Lee el estado del thread sin mutarlo |

### Chatbot de preventa (RAG)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "¿Cuánto cuesta el plan PRO?"}'
```

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/health` | Estado del servicio + disponibilidad del co-piloto |
| `POST` | `/chat` | Pregunta al chatbot de preventa (RAG) |
| `GET` / `DELETE` | `/session/{id}` | Info / limpieza de una sesión de preventa |
| `GET` | `/rag/stats` | Info del índice FAISS |
| `POST` | `/rag/reindex` | Reconstruir el índice RAG |

---

## 8. Tests

```bash
# Con el servidor corriendo en :8000
python test_chatbot.py
```

Cubre el canal de preventa: información general, planes y precios,
funcionalidades, memoria conversacional y manejo de alucinaciones. La
verificación del grafo del co-piloto vive en `scripts/` (smoke tests por fase).

---

## 9. Origen académico — chatbot de preventa (RAG)

Este repo arrancó como el **proyecto final** del diploma. Ese entregable hoy es
el canal `POST /chat` y se mantiene completo:

- **LLM + RAG** sobre **13 documentos estructurados** de FactuFácil.
- **Embeddings multilingüe** locales (`paraphrase-multilingual-MiniLM-L12-v2`),
  `RecursiveCharacterTextSplitter` (chunk 500 / overlap 50), búsqueda semántica
  con **FAISS** local — misma calidad que OpenSearch, sin servidor.
- **Manejo de alucinaciones:** el prompt ancla cada respuesta al contexto
  recuperado; si no alcanza, deriva a `ventas@factufacil.pe` /
  `+51 964 979 320` en vez de inventar. Las `sources` se devuelven en cada
  respuesta para trazabilidad.
- **CORS** con orígenes explícitos (`CORS_ORIGINS`), no `["*"]`.

**Entregables del trabajo final:**

- [x] Repositorio GitHub con código fuente completo
- [x] README con instalación y ejecución (este documento)
- [x] Diagrama de arquitectura ([sección 4](#4-arquitectura-hexagonal))
- [x] Documentación técnica extendida (`DETALLE.md`)
- [x] Tests funcionales (`test_chatbot.py`)
- [x] Dockerfile y `docker-compose.yml`
- [ ] Notebook en Google Colab / Jupyter
- [ ] Diapositivas de presentación final

---

## 10. Roadmap y decisiones

El trabajo pendiente se trackea en el **GitHub Project board**
[*chatbot_Factufacil Board*](https://github.com/users/miguel-anay/projects/11).
Hoy ahí viven, entre otros:

- `feat: control de la UI del ERP desde el co-piloto (canal ui_actions)` →
  diseño en [`docs/plan-control-ui-erp.md`](docs/plan-control-ui-erp.md).
- `feat: migrar checkpointer del grafo de InMemorySaver a Postgres` — hoy el
  estado del grafo vive en memoria del proceso; una confirmación pendiente no
  sobrevive a un reinicio.
- `decision: NO vectorizar el catálogo de productos (por ahora)`.

**Mejoras futuras (canal preventa):** reranking semántico (cross-encoder),
streaming de respuestas (`StreamingResponse`), integración con Telegram/WhatsApp,
panel para actualizar la base de conocimiento sin tocar código.
