# Arquitectura — Chatbot FactuFácil

Documento de referencia de la arquitectura del proyecto. Si venís a entender
**qué hace cada carpeta y bajo qué reglas vive**, empezá por acá. El *cómo
correr* el proyecto está en el [README](../README.md); el *porqué* de cada
decisión técnica está en los docstrings de cada módulo (no se duplica acá).

El proyecto sigue **arquitectura hexagonal** (Ports & Adapters). La idea
central: el núcleo del negocio NO sabe con qué tecnología habla afuera. El
dominio define QUÉ necesita; los adaptadores resuelven CÓMO.

---

## 1. El hexágono

```
                         EL HEXÁGONO
                              │
   MUNDO        ┌─────────────┼─────────────┐        MUNDO
   EXTERNO      │             │             │        EXTERNO
   (llama)      │          core/            │        (es llamado)
                │      domain + ports       │
  HTTP ────────►│      + application        │────────► Qwen / FAISS
  cliente       │                           │          FacturadorPro7
                └───────────────────────────┘
   entrypoints/                                adapters/
   ADAPTER DE ENTRADA                          ADAPTER DE SALIDA
   (driving / primary)                         (driven / secondary)
```

El control puede **entrar** (un request HTTP golpea `entrypoints/`) y **salir**
(el core llama a un puerto que un `adapters/` implementa contra el mundo real),
pero la **dependencia siempre apunta hacia adentro**: nadie en `core/` conoce a
FastAPI, httpx, FAISS ni Qwen.

---

## 2. Mapa carpeta → capa

| Carpeta | Capa | ¿Toca frameworks? | Rol |
|---------|------|-------------------|-----|
| `entrypoints/` | Adapter de **entrada** (driving) | Sí (FastAPI) | Recibe HTTP e **invoca** un caso de uso. No tiene lógica de negocio. |
| `core/domain.py` | **Dominio** | ❌ NUNCA | Entidades puras (`Item`, `ItemDraft`, `SaleNote`, `Cpe`…). |
| `core/ports.py` | **Puertos** | ❌ NUNCA | Contratos abstractos (`ItemsPort`, `RAGPort`, `SalesPort`…). |
| `core/application/` | **Aplicación** (casos de uso) | Sí (langchain/langgraph) | Orquesta el dominio. Acá SÍ se permite el framework. |
| `adapters/` | Adapter de **salida** (driven) | Sí (httpx, FAISS, Qwen SDK) | **Implementa** un puerto contra un servicio externo real. |
| `infrastructure/` | Infraestructura | Sí | Config y wiring (`Config`, base de conocimiento). |

> **La regla del path:** todo bajo `core/` que NO esté en `application/` es
> puro y agnóstico de infraestructura (verificable: no debería aparecer ningún
> `import langchain`/`httpx`/`fastapi` en `core/domain.py` ni `core/ports.py`).
> Todo bajo `core/application/` es framework-coupled por diseño — es
> application-services, no dominio puro.

### Detalle de `core/application/`

```
core/application/
├── presales_service.py    # caso de uso del chatbot de preventa (imperativo)
├── agents/                # co-piloto ERP — loop ReAct por especialista
│   ├── base.py            #   SpecialistAgent: prompt + bind_tools + loop acotado
│   ├── ventas_agent.py    #   los 5 especialistas (ventas, compras, ...)
│   └── tools/             #   tools de cada dominio; cruzan al adapter por-request
└── orchestration/         # supervisor + grafo LangGraph (caso de uso agéntico)
    ├── supervisor.py      #   routing → uno de los 5 especialistas
    └── graph.py           #   StateGraph: supervisor → especialista → END
```

---

## 3. La regla de oro: "¿quién llama a quién?"

Para clasificar cualquier archivo, hacé UNA pregunta:

- Si el adapter **LLAMA al core** (el control ENTRA al hexágono) → es de
  **entrada** (driving). El mundo te golpea la puerta. Ej: `entrypoints/api/main.py`
  hace `chatbot.chat(...)`.
- Si el core **LLAMA al adapter** vía un puerto (el control SALE del hexágono)
  → es de **salida** (driven). Vos golpeás la puerta del mundo. Ej:
  `ItemsAdapter(ItemsPort)` hace `POST /api/item` contra FacturadorPro7.

Por eso `entrypoints/` no implementa ningún puerto (él manda) y `adapters/`
siempre implementa uno (espera a que el core lo invoque).

**Control vs dependencia** (no se confunden):
- Control: entrada → core → salida (fluye de izquierda a derecha).
- Dependencia: ambos lados dependen del core; el core no depende de ninguno.

Ese es el premio de hexagonal: cambiás FastAPI por gRPC (otro entrypoint) o
Qwen por GPT (otro adapter de salida) sin tocar una línea del core. El
*Composition Root* — el único lugar donde se eligen los adaptadores
concretos — es `entrypoints/api/main.py` (`lifespan()`).

---

## 4. Dos formas de caso de uso

El proyecto tiene dos subsistemas y cada uno expresa su capa de aplicación de
forma distinta:

### 4.1 Preventa — caso de uso **imperativo**

`core/application/presales_service.py` → `ChatbotService.chat()`. Vos escribiste
los pasos y son fijos: `1. RAG → 2. memoria → 3. prompt → 4. LLM → 5. persistir`.
Endpoint: `POST /chat`.

### 4.2 Co-piloto ERP — caso de uso **agéntico**

El caso de uso es el **grafo compilado** (`orchestration/graph.py`). Vos NO
escribiste la secuencia de pasos: definiste las **capacidades** (tools, agentes,
routing) y el LLM decide la secuencia en runtime vía el patrón **ReAct**
(Reason → Act → Observe, loop acotado en `agents/base.py`). Endpoints:
`POST /agent/chat`, `POST /agent/confirm`, `GET /agent/session/{id}`.

Topología del grafo (sin aristas entre especialistas, a propósito):

```
supervisor ──(routing por active_specialist)──► { inventario, compras,
                                                   ventas, logistica,
                                                   contabilidad } ──► END
```

Los especialistas **no se encadenan** dentro de un turno: encadenar escrituras
automáticas multiplicaría el blast radius de una sola confirmación humana. Un
pedido multi-dominio se resuelve sugiriendo el siguiente paso como un turno
nuevo del usuario. Las escrituras irreversibles (emitir CPE, mover stock, etc.)
están *interrupt-gated*: el grafo pausa y exige `POST /agent/confirm`.

---

## 5. Decisiones (ADR-lite)

### 5.1 Reorganización a estructura layer-first

**Contexto.** La estructura original mezclaba criterios: algunas carpetas eran
por capa (`adapters/`, `infrastructure/`) y otras por feature (`agents/`,
`orchestration/`), había archivos legacy sueltos en la raíz, y `core/` se
contradecía — `ports.py` declaraba "el core no importa langchain" pero
`core/agents` y `core/orchestration` estaban llenos de imports de
langchain/langgraph.

**Decisión.** Mover la capa de aplicación bajo `core/application/`:
- `core/chatbot_service.py` → `core/application/presales_service.py`
- `core/agents/` → `core/application/agents/`
- `core/orchestration/` → `core/application/orchestration/`
- `core/domain.py` y `core/ports.py` se dejaron donde estaban (ya eran puros y
  bien nombrados — cero churn de imports).

**Consecuencia.** Ahora el path grita la regla de capa: `core/domain.py` +
`core/ports.py` = puro; `core/application/**` = framework-coupled. La
contradicción desapareció.

### 5.2 Eliminación de código muerto en la raíz

Se borraron `main.py`, `chatbot_service.py`, `config.py`, `rag_system.py` y
`knowledge_base.py` de la raíz. Eran una versión plana pre-hexagonal que solo se
importaba a sí misma. Confirmado muerto: el `Dockerfile` corre
`uvicorn entrypoints.api.main:app`, el CI hace `docker run` + `python test_chatbot.py`
(por HTTP), y ningún módulo de `core/`/`adapters/`/`entrypoints/` los importaba.
El entrypoint de desarrollo vivo es `run.py` → `entrypoints.api.main:app`.

### 5.3 `bind_tools` + loop acotado en vez de `create_react_agent`

`agents/base.py` implementa el loop ReAct a mano (`.bind_tools()` + loop con
`max_iterations`) en lugar del prebuilt `create_react_agent`, para tener control
total sobre la construcción de mensajes y el límite de iteraciones, y reusar el
mismo cliente `ChatOpenAI` ya verificado para tool-calling.

### 5.4 Credenciales de tenant por `config`, nunca como parámetro de tool

Las `TenantCredentials` viajan por `config.configurable` (anotación
`InjectedToolArg`), nunca como parámetro normal de la tool — así el Bearer token
no aparece en el JSON schema que ve el LLM. Cada tool arma su
`FacturadorPro7Client`/adapter al vuelo, por-request. Ver
`core/application/agents/tools/_shared.py`.
