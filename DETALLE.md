# DETALLE TÉCNICO — Chatbot FactuFácil
**Diploma AI Engineer — Diseño e Implementación de Chatbots**  
**Proyecto Final | Sesiones 6 / 7 / 8**

---

## 1. Descripción del Proyecto

Se desarrolló un asistente virtual inteligente para **FactuFácil** (https://factufacil.pe), sistema de facturación electrónica peruano. El chatbot es capaz de responder preguntas sobre planes, precios, funcionalidades, integración con SUNAT y datos de contacto, manteniendo contexto conversacional entre turnos y evitando alucinaciones.

---

## 2. Problema que Resuelve

Los usuarios de FactuFácil (ferreterías, mini markets, farmacias, restaurantes) necesitan respuestas inmediatas sobre el sistema sin depender de un agente de ventas humano. El chatbot:

- Responde consultas 24/7 sobre planes y precios
- Guía al usuario hacia el plan correcto según su tipo de negocio
- Escala al equipo de ventas solo cuando no tiene suficiente contexto
- Elimina respuestas inventadas (alucinaciones) al anclar cada respuesta en documentos verificados

---

## 3. Arquitectura del Sistema

```
Usuario
  │
  ▼
FastAPI  (main.py — puerto 8000)
  │
  ▼
ChatbotService  (chatbot_service.py)
  │
  ├──► RAGSystem  (rag_system.py)
  │       ├── HuggingFace Embeddings
  │       │     modelo: paraphrase-multilingual-MiniLM-L12-v2
  │       ├── FAISS Vector Store  (data/faiss_index/)
  │       └── RecursiveCharacterTextSplitter
  │             chunk_size=500 / overlap=50
  │
  ├──► ConversationBufferWindowMemory
  │       k = 8 turnos por sesión
  │
  └──► LLM via ChatOpenAI  (Qwen3 / GPT)
         Alibaba DashScope (OpenAI-compatible)
         o OpenAI directamente
```

**Flujo por mensaje:**

```
1. POST /chat  →  ChatbotService.chat()
2. RAGSystem.retrieve(query, k=4)     ← búsqueda semántica en FAISS
3. Armar prompt:
       sistema + contexto RAG + historial + pregunta
4. LLM.invoke(prompt)                 ← Qwen / GPT genera respuesta
5. memory.save_context()              ← guardar turno en memoria
6. Retornar: answer + sources + session_id
```

---

## 4. Descripción de Archivos

### `config.py`
Configuración centralizada. Detecta automáticamente si usar Alibaba DashScope (Qwen3) u OpenAI según las variables de entorno disponibles. Expone constantes para el LLM, los embeddings, el RAG y el servidor.

```
Config.LLM_API_KEY      → ALIBABA_API_KEY o OPENAI_API_KEY
Config.LLM_BASE_URL     → URL del endpoint (vacío si es OpenAI estándar)
Config.LLM_MODEL        → qwen-plus | gpt-3.5-turbo
Config.EMBEDDING_MODEL  → paraphrase-multilingual-MiniLM-L12-v2
Config.CHUNK_SIZE       → 500
Config.CHUNK_OVERLAP    → 50
Config.TOP_K            → 4  (chunks recuperados por consulta)
Config.MEMORY_K         → 8  (turnos retenidos en memoria)
```

---

### `knowledge_base.py`
13 documentos estructurados con datos reales extraídos de https://factufacil.pe.  
Cada documento tiene `content` (texto plano) y `metadata` (category + topic).

| # | Categoría | Tema |
|---|-----------|------|
| 1 | empresa | descripcion_general |
| 2 | precios | planes |
| 3 | precios | comparativa_planes |
| 4 | facturacion | comprobantes |
| 5 | inventario | gestion_inventario |
| 6 | pos | punto_de_venta |
| 7 | ecommerce | ventas_online |
| 8 | gestion | compras_reportes |
| 9 | app | acceso_movil |
| 10 | equipos | hardware |
| 11 | contacto | soporte_contacto |
| 12 | faq | preguntas_frecuentes |
| 13 | casos_uso | sectores |

---

### `rag_system.py`
Motor de Retrieval-Augmented Generation.

**Responsabilidades:**
- Primera ejecución: divide los documentos en chunks con `RecursiveCharacterTextSplitter`, genera embeddings y construye el índice FAISS; lo guarda en `data/faiss_index/`.
- Ejecuciones posteriores: carga el índice desde disco (arranque rápido).
- `retrieve(query, k=4)` → devuelve los `k` chunks más similares semánticamente.
- `reindex()` → elimina y reconstruye el índice (útil si se actualiza la base de conocimiento).

**Elección de embeddings:** `paraphrase-multilingual-MiniLM-L12-v2`  
Modelo de Sentence Transformers con soporte nativo para español, sin costo y sin API key. Normalización L2 activada para comparación coseno consistente con FAISS.

---

### `chatbot_service.py`
Núcleo del chatbot. Orquesta los tres componentes: RAG, LLM y memoria.

**Memoria por sesión:** cada `session_id` tiene su propio `ConversationBufferWindowMemory` con k=8 turnos. Esto permite mantener contexto entre mensajes sin acumular tokens infinitamente.

**Construcción del prompt (manual, no ConversationalRetrievalChain):**
```
[Instrucciones del sistema]
[Contexto recuperado por RAG]
[Historial de conversación]
[Pregunta del usuario]
→ Respuesta del LLM
```
La construcción manual hace el flujo completamente transparente y facilita ajustar el prompt sin entender las abstracciones internas de LangChain.

**Anti-alucinación:** el prompt instruye al LLM a responder solo con información del contexto. Si no hay contexto suficiente, deriva al email y teléfono de ventas de FactuFácil.

---

### `main.py`
API REST construida con FastAPI.

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/chat` | Enviar mensaje; devuelve respuesta + fuentes + session_id |
| `GET` | `/session/{id}` | Info de una sesión activa |
| `DELETE` | `/session/{id}` | Limpiar historial de sesión |
| `POST` | `/rag/reindex` | Reconstruir índice FAISS |
| `GET` | `/rag/stats` | Estadísticas del índice |
| `GET` | `/health` | Estado del servicio |
| `GET` | `/` | Mapa de endpoints |

Swagger UI disponible en `http://localhost:8000/docs`.

---

### `test_chatbot.py`
Batería de tests funcionales contra el servidor live. Cubre 5 grupos:

| Grupo | Qué valida |
|-------|-----------|
| Información general | Descripción del producto y sectores |
| Planes y precios | Valores exactos, diferencias entre planes |
| Funcionalidades | SUNAT, modo offline, sucursales, celular |
| Memoria conversacional | Continuidad de contexto entre turnos |
| Manejo de alucinaciones | Respuestas ante preguntas sin respuesta en la KB |

---

## 5. Decisiones de Diseño

### FAISS en lugar de OpenSearch
OpenSearch requiere un servidor corriendo (Docker o instancia dedicada). FAISS opera completamente en memoria local con persistencia en disco. Para un proyecto académico y para demos, elimina una dependencia de infraestructura sin sacrificar la calidad de la búsqueda semántica.

### Embeddings locales (sentence-transformers)
Los embeddings de OpenAI cuestan tokens y requieren conexión. `paraphrase-multilingual-MiniLM-L12-v2` corre localmente, es gratuito, admite español de forma nativa y produce vectores de 384 dimensiones con muy buena calidad semántica para este dominio.

### Prompt manual vs. ConversationalRetrievalChain
`ConversationalRetrievalChain` abstrae la construcción del prompt internamente, dificultando el control fino. Con el prompt manual se sabe exactamente qué recibe el LLM en cada turno: sistema + contexto + historial + pregunta. Esto también facilita la depuración y el ajuste del comportamiento.

### Alibaba DashScope como proveedor LLM primario
Sigue el mismo patrón del curso (sesiones 5 y 7). DashScope expone una API compatible con OpenAI, por lo que usar `ChatOpenAI` con `base_url` configurado es suficiente. Si el usuario tiene `OPENAI_API_KEY`, el sistema lo detecta automáticamente sin cambiar código.

### Memoria con ventana deslizante (k=8)
`ConversationBufferWindowMemory(k=8)` retiene los últimos 8 turnos. Esto balancea contexto conversacional suficiente con tokens de prompt controlados. Para un chatbot de soporte/ventas, 8 turnos cubren el 95% de las conversaciones reales.

---

## 6. Instalación y Ejecución

```bash
# 1. Entrar al directorio
cd proyecto_final_factufacil

# 2. Entorno virtual
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env → agregar ALIBABA_API_KEY o OPENAI_API_KEY

# 5. Iniciar servidor
python run.py
# El índice FAISS se construye automáticamente en el primer arranque (~30 s)
```

**Swagger UI:** http://localhost:8000/docs

```bash
# 6. Ejecutar tests (con servidor corriendo)
python test_chatbot.py
```

---

## 7. Ejemplo de Uso

**Request:**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "¿Cuánto cuesta el plan PRO y qué incluye?"}'
```

**Response:**
```json
{
  "session_id": "f3a1b2c4-...",
  "answer": "El plan PRO cuesta S/.95 al mes (S/.950 al año) y es el más popular...",
  "sources": [
    { "category": "precios", "topic": "planes", "excerpt": "Plan PRO — S/.95 por mes..." },
    { "category": "precios", "topic": "comparativa_planes", "excerpt": "..." }
  ],
  "message_count": 1
}
```

---

## 8. Estructura del Proyecto

```
proyecto_final_factufacil/
├── main.py               # API REST — FastAPI
├── chatbot_service.py    # Orquestación LLM + RAG + memoria
├── rag_system.py         # FAISS + embeddings multilingüe
├── knowledge_base.py     # Base de conocimiento de FactuFácil
├── config.py             # Configuración centralizada
├── test_chatbot.py       # Tests funcionales
├── requirements.txt      # Dependencias Python
├── .env.example          # Plantilla de variables de entorno
├── README.md             # Guía de instalación y uso
├── DETALLE.md            # Este documento
└── data/
    └── faiss_index/      # Índice FAISS (generado automáticamente)
```

---

## 9. Tecnologías Utilizadas

| Tecnología | Versión | Rol |
|-----------|---------|-----|
| Python | 3.10+ | Lenguaje principal |
| LangChain | >= 0.2 | Framework de orquestación |
| FAISS | >= 1.7.4 | Vector store local |
| sentence-transformers | >= 2.3.1 | Embeddings multilingüe |
| FastAPI | >= 0.110 | API REST |
| Uvicorn | >= 0.27 | Servidor ASGI |
| Qwen3 / GPT | — | LLM generativo |
| Alibaba DashScope | — | Proveedor LLM (OpenAI-compatible) |

---

## 10. Posibles Mejoras Futuras

- Reemplazar FAISS por OpenSearch para búsqueda distribuida en producción
- Agregar reranking semántico (cross-encoder) para mejorar precisión RAG
- Implementar streaming de respuestas (`StreamingResponse`) para UX en tiempo real
- Agregar autenticación por API key en los endpoints
- Integración con Telegram o WhatsApp como canal de atención
- Panel de administración para actualizar la base de conocimiento sin tocar código

---

## 11. Evolución de los Chatbots: Reglas → Intenciones → LLM

### ¿Por qué ya no son necesarias las reglas ni las intenciones con LangChain?

El LLM **ya entiende el lenguaje**. No es necesario definir reglas ni entrenar intenciones con decenas de ejemplos, porque el modelo fue entrenado con enormes volúmenes de texto y comprende el lenguaje de forma nativa.

```
Sesión 1 — Reglas (difflib)
  "si el mensaje contiene 'precio' → responder X"
  Problema: frágil. "¿cuánto cuesta?" no matchea "precio".

Sesión 2 — Intenciones (Rasa)
  Entrenás: consultar_precio → 30 ejemplos
  Problema: si el usuario escribe algo fuera de lo entrenado, falla.

Sesión 5 en adelante — LLM
  El modelo entiende "¿cuánto sale?", "¿tiene costo?",
  "dame el precio", "es caro?" — todo sin entrenar nada.
```

### ¿Para qué sirvió aprender reglas e intenciones entonces?

Para entender **el problema que los LLMs resuelven**. Sin pasar por Rasa, no se entiende por qué el RAG importa, ni qué problema resuelve la memoria conversacional, ni por qué las alucinaciones son un riesgo real. Las sesiones 1 y 2 no eran el destino — eran el contexto para que las sesiones 5, 6 y 7 tuvieran sentido.

> Hoy, en producción, nadie construye un chatbot con reglas o Rasa si tiene acceso a un LLM. La única excepción es hardware muy limitado, uso offline estricto, o latencia de microsegundos.

---

## 12. ¿Entonces para qué sirven las Expresiones Regulares?

Las regex siguen siendo necesarias, pero en un rol muy específico: **validación y extracción de datos estructurados**, no para entender lenguaje.

### Casos concretos donde las seguís necesitando junto a un LLM

**1. Validar formato de datos del usuario**
```python
# El usuario escribe "mi RUC es 2O482719301" (letra O en lugar de cero)
# El LLM no detecta eso — una regex sí
import re
if not re.match(r'^\d{11}$', ruc):
    return "El RUC ingresado no es válido."
```

**2. Extraer datos estructurados antes de llamar al LLM**
```python
# Detectar número de pedido en el mensaje antes de hacer RAG
numero = re.search(r'PED-\d{6}', mensaje)
if numero:
    contexto = buscar_pedido(numero.group())
```

**3. Sanitizar input — prevenir Prompt Injection**
```python
# Si el usuario escribe "ignora las instrucciones anteriores..."
# Una regex puede detectar patrones de ataque conocidos antes de enviar al LLM
PATRONES_INJECTION = [r'ignora.*instrucciones', r'olvida.*sistema', r'act.*como']
for patron in PATRONES_INJECTION:
    if re.search(patron, mensaje.lower()):
        return "Mensaje no permitido."
```

**4. Parsear la respuesta del LLM**
```python
# El LLM devuelve JSON pero a veces agrega texto extra antes o después
# Una regex extrae el bloque JSON limpio
match = re.search(r'\{.*\}', respuesta_llm, re.DOTALL)
if match:
    datos = json.loads(match.group())
```

### La regla de oro

| Tarea | Herramienta correcta |
|-------|---------------------|
| Entender la intención del usuario | LLM |
| Generar una respuesta natural | LLM |
| Validar que un RUC tiene 11 dígitos | Regex |
| Extraer un código de pedido del texto | Regex |
| Detectar formato de email o teléfono | Regex |
| Sanitizar input antes del LLM | Regex |
| Parsear output estructurado del LLM | Regex + json.loads |

> **LLM para entender lenguaje. Regex para validar estructura.**

---

## 13. Arquitectura de Software — ¿Cuál es la correcta para este chatbot?

### Análisis de opciones

| Arquitectura | ¿Aplica? | Razón |
|---|---|---|
| Capas simple (versión 1.0) | Funciona, pero frágil | `ChatbotService` sabe que existe FAISS y Qwen — acoplamiento directo |
| **Hexagonal (versión 2.0)** | **Sí — es la correcta** | El core nunca sabe qué infraestructura usa; los adaptadores son intercambiables |
| Microservicios | No todavía | Overkill para un solo bot; sí aplica cuando hay múltiples bots que escalan distinto |
| Eventos (Event-driven) | Parcialmente | Útil para integraciones futuras (notificaciones, auditoría), no para el core |

### El problema del acoplamiento directo (v1.0)

```python
# ANTES — chatbot_service.py sabía demasiado
class ChatbotService:
    def __init__(self):
        self.rag = RAGSystem()      # acoplado a FAISS
        self.llm = ChatOpenAI(...)  # acoplado a OpenAI/Qwen
        # Si cambio FAISS por OpenSearch → tengo que tocar este archivo
```

### La solución Hexagonal (v2.0)

```python
# AHORA — el core solo habla con interfaces
class ChatbotService:
    def __init__(self, llm: LLMPort, rag: RAGPort, memory: MemoryPort, persona: BotPersona):
        self._llm = llm       # no sabe si es Qwen, GPT o un mock
        self._rag = rag       # no sabe si es FAISS, OpenSearch o Pinecone
        self._memory = memory # no sabe si es RAM, Redis o una base de datos
```

### Estructura de la versión 2.0

```
proyecto_final_factufacil/
│
├── core/                        ← DOMINIO (sin dependencias externas)
│   ├── domain.py                  entidades: ChatMessage, ChatResponse, BotPersona
│   ├── ports.py                   interfaces: LLMPort, RAGPort, MemoryPort
│   └── chatbot_service.py         lógica de negocio pura
│
├── adapters/                    ← INFRAESTRUCTURA (implementan los puertos)
│   ├── llm/
│   │   └── openai_compatible.py   → LLMPort con Qwen/GPT
│   ├── rag/
│   │   └── faiss_adapter.py       → RAGPort con FAISS
│   └── memory/
│       └── window_memory_adapter.py → MemoryPort en RAM
│
├── infrastructure/              ← CONFIGURACIÓN Y DATOS
│   ├── config.py
│   └── knowledge_base.py
│
└── entrypoints/                 ← ENTRADA (HTTP, CLI, tests)
    └── api/
        ├── main.py              ← Composition Root: aquí se eligen los adaptadores
        └── schemas.py
```

### El Composition Root — el único lugar donde se toman decisiones de infraestructura

```python
# entrypoints/api/main.py — ÚNICO lugar donde se eligen adaptadores concretos
chatbot = ChatbotService(
    llm=OpenAICompatibleAdapter(),   # mañana: OllamaAdapter() o GroqAdapter()
    rag=FAISSAdapter(),              # mañana: OpenSearchAdapter()
    memory=WindowMemoryAdapter(),    # mañana: RedisMemoryAdapter()
    persona=FACTUFACIL_PERSONA,
)
```

---

## 14. Escalando a Múltiples Bots — Visión de Plataforma

### El problema que surge

Si querés varios bots especializados para el mismo negocio:

```
Bot de Ventas     Bot de Soporte ERP     MCP Agent (automatización)
(WhatsApp / Web)  (consultas técnicas)   (ejecuta tareas en el ERP)
```

Todos comparten el mismo cerebro (LLM, RAG, memoria) pero cada uno tiene:
- Su propia **knowledge base** (ventas vs soporte vs operaciones)
- Su propio **prompt y personalidad** (vendedor vs técnico vs ejecutor)
- Su propio **canal** (WhatsApp vs web vs API interna)
- Sus propias **herramientas** (el MCP Agent puede crear facturas, actualizar stock)

### Por qué Hexagonal hace que esto sea simple

Cada bot es solo una **configuración diferente del mismo `ChatbotService`**:

```python
# Bot de ventas
sales_bot = ChatbotService(
    llm=OpenAICompatibleAdapter(),
    rag=FAISSAdapter(index="ventas_kb"),
    memory=WindowMemoryAdapter(),
    persona=BotPersona(name="FactuFácil Ventas", ...),
)

# Bot de soporte ERP
support_bot = ChatbotService(
    llm=OpenAICompatibleAdapter(),
    rag=FAISSAdapter(index="erp_support_kb"),
    memory=WindowMemoryAdapter(),
    persona=BotPersona(name="FactuFácil Soporte", ...),
)
```

Sin tocar el core. Sin duplicar lógica.

### Diagrama de la plataforma futura

```
                    ┌─────────────────────────────────┐
                    │         API Gateway              │
                    │   (autenticación, routing)       │
                    └──────┬────────────┬──────────────┘
                           │            │            │
                    ┌──────▼──────┐ ┌───▼──────┐ ┌──▼──────────┐
                    │  Sales Bot  │ │ Support  │ │  MCP Agent  │
                    │  (ventas)   │ │  (ERP)   │ │  (tareas)   │
                    └──────┬──────┘ └────┬─────┘ └──┬──────────┘
                           │             │           │
                           └─────────────┴───────────┘
                                         │
                           ┌─────────────▼─────────────┐
                           │       LLM Core Service     │
                           │  RAG · Memoria · Embeddings│
                           └───────────────────────────┘
```

### Tres enfoques de comunicación entre servicios

| Enfoque | Cuándo usarlo | Cómo |
|---------|--------------|------|
| **Monolito modular** | Arrancando, pocos bots, un equipo | Los bots se llaman como funciones Python — sin red |
| **Microservicios síncronos** | Cuando necesitás deployar los bots de forma independiente | `POST /llm/chat` — espera respuesta HTTP |
| **Microservicios asíncronos** | Alta carga, resilencia ante fallos | Mensajes en cola (RabbitMQ, Redis Streams) — no bloquea |

> **Regla práctica:** empezá con monolito modular + Hexagonal. Cuando el tráfico o el equipo crezca, extraés el LLM Core como microservicio. Tu `ChatbotService` no cambia ni una línea — solo swapeás el adaptador por uno que llama HTTP.

### El MCP Agent — el bot más interesante

No conversa: **actúa**. Usa LangChain Agents con tools conectadas al ERP:

```python
tools = [
    Tool(name="crear_factura",    func=erp_api.crear_factura),
    Tool(name="actualizar_stock", func=erp_api.actualizar_stock),
    Tool(name="consultar_pedido", func=erp_api.consultar_pedido),
]
# El LLM decide qué herramienta usar según el mensaje del usuario
agent = initialize_agent(tools, llm, agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION)
```

La diferencia con los otros bots: en lugar de solo responder, **ejecuta operaciones reales** en el sistema.

---

## 15. Entorno de Ejecución — ¿venv, Docker o Sandbox?

Sandbox y Docker no son lo mismo — tienen propósitos distintos.

**Sandbox** es un concepto: entorno aislado donde el código no puede dañar el sistema real. Docker es una *implementación* de sandbox, pero no la única.

| Etapa | Solución recomendada | Por qué |
|-------|---------------------|---------|
| Desarrollo / proyecto del diploma | `venv` solo | Más simple, iteración rápida |
| Deployment / compartir con equipo | Docker | Mismo entorno en cualquier máquina |
| MCP Agent ejecutando operaciones en ERP | Docker + sandbox de validación | El LLM puede alucinar una operación peligrosa |

### Docker para este proyecto

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "entrypoints/api/main.py"]
```

Con múltiples bots, cada uno tiene su propio `.env`:

```yaml
# docker-compose.yml
services:
  sales-bot:
    build: .
    ports: ["8001:8000"]
    env_file: .env.sales

  support-bot:
    build: .
    ports: ["8002:8000"]
    env_file: .env.support

  mcp-agent:
    build: .
    ports: ["8003:8000"]
    env_file: .env.mcp
```

Mismo contenedor, distinta configuración — exactamente el patrón que Hexagonal habilita con `BotPersona`.

---

## 16. MCP Agent — Hexagonal con ToolPort y Sandbox

### El MCP Agent no rompe Hexagonal — lo extiende

El MCP Agent sigue dentro de la arquitectura hexagonal. La única diferencia es que agrega un nuevo tipo de puerto: **ToolPort**.

```
Chatbot que responde (hoy):        MCP Agent que actúa (futuro):
  LLMPort   → genera texto           LLMPort   → decide qué herramienta usar
  RAGPort   → recupera contexto      RAGPort   → contexto para decidir mejor
  MemoryPort → recuerda              MemoryPort → recuerda lo que hizo
                                     ToolPort  → NUEVO: ejecuta acciones en el ERP
```

### El nuevo puerto

```python
# core/ports.py — extensión para el MCP Agent
class ToolPort(ABC):
    @abstractmethod
    def execute(self, tool_name: str, params: dict) -> dict: ...

    @abstractmethod
    def list_tools(self) -> list[str]: ...
```

El core del `ChatbotService` no cambia. Solo el MCP Agent recibe un `ToolPort` adicional en su constructor.

### El sandbox vive en el adaptador — no en el core

```python
# adapters/tools/erp_tool_adapter.py
class ERPToolAdapter(ToolPort):

    def execute(self, tool_name: str, params: dict) -> dict:
        # 1. SANDBOX: validar antes de ejecutar
        self._validate(tool_name, params)
        # 2. Ejecutar en el ERP real
        return self._erp_api.call(tool_name, params)

    def _validate(self, tool_name: str, params: dict) -> None:
        # ¿Es una operación permitida?
        # ¿Los parámetros tienen sentido?
        # ¿Supera algún umbral de riesgo?
        OPERACIONES_PERMITIDAS = {"crear_factura", "consultar_pedido", "actualizar_stock"}
        if tool_name not in OPERACIONES_PERMITIDAS:
            raise ValueError(f"Operación no permitida: {tool_name}")
```

El core nunca sabe si la herramienta está en sandbox, staging o producción. Eso lo decide el adaptador.

### Para tests: MockERPToolAdapter

```python
# tests/adapters/mock_erp_tool_adapter.py
class MockERPToolAdapter(ToolPort):
    """Simula operaciones del ERP sin tocar nada real."""

    def execute(self, tool_name: str, params: dict) -> dict:
        return {"status": "ok", "mock": True, "tool": tool_name, "params": params}

    def list_tools(self) -> list[str]:
        return ["crear_factura", "consultar_pedido", "actualizar_stock"]
```

### Composition Root del MCP Agent

```python
# entrypoints/api/main.py — ensamblado del MCP Agent
mcp_agent = ChatbotService(
    llm=OpenAICompatibleAdapter(),
    rag=FAISSAdapter(index="erp_operations_kb"),
    memory=WindowMemoryAdapter(),
    persona=BotPersona(name="FactuFácil MCP Agent", ...),
    tools=ERPToolAdapter(),        # en producción: con validaciones reales
    # tools=MockERPToolAdapter(),  # en tests: sin tocar el ERP
)
```

> El sandbox no es una capa nueva — es parte del adaptador. El core nunca sabe si la herramienta está en sandbox o en producción.
