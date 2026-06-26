# Onboarding — Chatbot FactuFácil

> Guía de cero a productivo para alguien que entra al repo por primera vez.
> Si solo querés **correr** el proyecto, andá directo a la sección 2.
> Si vas a **contribuir**, leé todo: la sección 4 explica cómo trabajamos.

---

## 1. Qué es este proyecto (5 minutos)

El repo arrancó como el **proyecto final** del diploma *Diseño e Implementación
de Chatbots* (un chatbot LLM + RAG para [FactuFácil](https://factufacil.pe)) y
fue evolucionando hacia un **co-piloto del ERP**. Hoy conviven **dos
subsistemas** sobre la misma arquitectura hexagonal:

| Subsistema | Para qué | Entrada HTTP | Núcleo |
|------------|----------|--------------|--------|
| **A — Chatbot de preventa (RAG)** | Responde 24/7 sobre planes, precios, SUNAT, etc. usando RAG sobre la base de conocimiento de FactuFácil. Es el entregable académico. | `POST /chat` | `core/application/presales_service.py` |
| **B — Co-piloto ERP multiagente** | Asiste dentro del ERP (FacturadorPro7): ventas, compras, inventario, contabilidad, logística. Orquestado con LangGraph, con confirmación humana para las escrituras. | `POST /agent/chat`, `POST /agent/confirm` | `core/application/orchestration/` + `core/application/agents/` |

Los dos comparten el mismo proceso FastAPI y el mismo Composition Root
(`entrypoints/api/main.py`). El co-piloto ERP es **aditivo**: si el grafo no
compila al arrancar, `/agent/*` devuelve `503` y el `/chat` de preventa sigue
funcionando igual.

**La regla de oro de la arquitectura:** las flechas de dependencia apuntan
SIEMPRE hacia adentro. El `core/` (dominio + lógica) no conoce a FAISS, ni a
LangGraph, ni a la API del ERP. Esos viven en `adapters/`. Releé
[`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) y
[`docs/langgraph-y-arquitectura-hexagonal.md`](docs/langgraph-y-arquitectura-hexagonal.md)
hasta que esto te quede natural — es lo que mantiene el proyecto sano.

---

## 2. Setup local (paso a paso)

### Requisitos previos

- **Python 3.10+** (el venv del repo usa 3.12).
- Una API key de LLM: `ALIBABA_API_KEY` (Alibaba DashScope / Qwen3) **o**
  `OPENAI_API_KEY`.
- Para el co-piloto ERP además: credenciales de un tenant FacturadorPro7
  (`base_url` + `token`), que se pasan **por request**, no en `.env`.

### Pasos

```bash
# 1. Entrar al proyecto
cd proyecto_final_factufacil

# 2. Entorno virtual
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Dependencias (versiones pineadas — ver requirements.txt)
pip install -r requirements.txt

# 4. Variables de entorno
cp .env.example .env
# Editá .env y completá ALIBABA_API_KEY o OPENAI_API_KEY
```

---

## 3. Correr y probar

```bash
# Arranque dev (con reload)
python run.py
# Equivalente: uvicorn entrypoints.api.main:app --reload --host 0.0.0.0 --port 8000
```

- **Swagger UI:** http://localhost:8000/docs — la forma más rápida de ver y
  probar todos los endpoints.
- El índice FAISS se construye solo en el primer arranque (~30 s).
- `GET /health` te dice si el modelo está OK y si el co-piloto ERP
  (`agent_available`) compiló.

**Humo rápido del chatbot de preventa:**

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "¿Cuánto cuesta el plan PRO?"}'
```

**Tests funcionales** (con el server corriendo):

```bash
python test_chatbot.py
```

**Con Docker:**

```bash
docker-compose up --build
```

---

## 4. Cómo trabajamos (leer antes de tu primer PR)

### Flujo de ramas y PRs

- `main` es la rama estable. **No se commitea directo a `main`.**
- Trabajás en una rama por cambio (ej. `feat/...`, `fix/...`,
  `refactor/...`, `docs/...`).
- Abrís un **Pull Request** contra `main`.
- **El branch remoto se borra solo al mergear la PR** (el repo tiene activado
  *Automatically delete head branches*). Tu rama local NO se toca: limpiala
  vos con `git branch -d <rama>` cuando quieras.
- Commits en estilo **Conventional Commits** (`feat:`, `fix:`, `docs:`,
  `refactor:`, `perf:`, `chore:`), sin atribución de IA.

### Planificación (SDD / OpenSpec)

Los cambios grandes se planifican antes de codear. Los artefactos
(propuesta → specs → diseño → tareas → verificación) viven en
[`openspec/`](openspec/). Mirá un cambio ya archivado como ejemplo:
`openspec/changes/archive/2026-06-24-multiagente-erp-facturadorpro7/`.

### Roadmap y seguimiento

El trabajo pendiente se trackea en el **GitHub Project board**
[*chatbot_Factufacil Board*](https://github.com/users/miguel-anay/projects/11).
Hoy ahí viven, por ejemplo:

- `feat: control de la UI del ERP desde el co-piloto (canal ui_actions)` →
  diseño en [`docs/plan-control-ui-erp.md`](docs/plan-control-ui-erp.md).
- `feat: migrar checkpointer del grafo de InMemorySaver a Postgres`.
- `decision: NO vectorizar el catálogo de productos (por ahora)`.

---

## 5. Mapa del código (dónde vive cada cosa)

```
proyecto_final_factufacil/
│
├── core/                              ← DOMINIO + APLICACIÓN (sin infra)
│   ├── domain.py                        entidades: ChatMessage, ChatResponse, BotPersona
│   ├── ports.py                         interfaces: LLMPort, RAGPort, MemoryPort
│   └── application/
│       ├── presales_service.py          Subsistema A — chatbot RAG de preventa
│       ├── orchestration/               Subsistema B — grafo LangGraph
│       │   ├── graph.py                   build_graph(): arma supervisor + agentes
│       │   ├── supervisor.py              enruta al especialista correcto
│       │   ├── state.py                   AgentState que viaja entre nodos
│       │   └── confirmation.py            interrupt() / resume para escrituras
│       └── agents/                      especialistas + sus tools
│           ├── ventas_agent.py · compras_agent.py · inventario_agent.py
│           ├── contabilidad_agent.py · logistica_agent.py
│           └── tools/                     tools que llaman a la API del ERP
│
├── adapters/                          ← INFRAESTRUCTURA (implementan ports)
│   ├── llm/openai_compatible.py         LLMPort con Qwen3/GPT
│   ├── rag/faiss_adapter.py             RAGPort con FAISS
│   ├── memory/window_memory_adapter.py  MemoryPort en RAM (k=8 turnos)
│   └── facturadorpro7_api/              cliente HTTP + adapters del ERP
│
├── infrastructure/                    ← CONFIG Y DATOS
│   ├── config.py                        constantes y variables de entorno
│   └── knowledge_base.py                13 documentos de FactuFácil (RAG)
│
├── entrypoints/api/                   ← ENTRADA HTTP (Composition Root)
│   ├── main.py                          ensambla adaptadores y expone /chat, /health, /rag/*
│   ├── agent_router.py                  expone /agent/chat, /agent/confirm, /agent/session
│   └── schemas.py                       modelos Pydantic request/response
│
├── docs/                              ← guías de arquitectura y planes
├── openspec/                          ← artefactos SDD (propuestas, specs, diseño)
├── run.py · test_chatbot.py · requirements.txt · Dockerfile · docker-compose.yml
└── data/faiss_index/                  ← se genera solo al iniciar
```

---

## 6. Línea roja del co-piloto ERP (no negociable)

Si tocás el subsistema B, grabate esto:

- Las **escrituras** (crear venta, mover stock, etc.) van SIEMPRE por los tools
  de la API con `interrupt()` → **confirmación humana** + validación
  server-side. El agente nunca escribe solo.
- El agente **NO** accede a la base de datos directamente: entra por
  `ports`/`adapters`.
- LangGraph está **confinado a `core/application/orchestration/`**. No se filtra
  hacia el dominio ni hacia los entrypoints.

---

## 7. Próximos pasos para vos

1. Levantá el server y jugá con `/docs`.
2. Leé [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) y
   [`docs/langgraph-y-arquitectura-hexagonal.md`](docs/langgraph-y-arquitectura-hexagonal.md).
3. Mirá el board para ver qué hay en cola.
4. Para tu primer aporte, agarrá algo chico (un `docs:` o un `fix:`) y seguí el
   flujo de la sección 4.

¿Dudas que esta guía no resuelve? Abrí un issue o mejorá este mismo archivo en
tu PR — el onboarding lo mantenemos entre todos.
