# LangGraph + Arquitectura Hexagonal en este proyecto

> Guía de aprendizaje: cómo funciona el co-piloto ERP multiagente y cómo se
> relaciona con la arquitectura hexagonal. Pensado para releer en frío.

---

## 1. Las dos ideas, por separado

Hay **dos modelos mentales distintos** que se cruzan. Mucha gente los mezcla.
Primero hay que separarlos.

**Arquitectura hexagonal** responde a UNA sola pregunta: *¿quién puede depender
de quién?* Es una regla de dependencias. El centro (el dominio, las reglas de
negocio) no conoce el mundo exterior; el mundo exterior conoce al centro. Las
flechas de dependencia apuntan SIEMPRE hacia adentro.

**LangGraph** responde a otra pregunta totalmente distinta: *¿cómo orquesto
varios pasos/agentes que comparten un estado y a veces tienen que pausar?* Es
una máquina de estados: nodos, aristas, un estado que viaja entre ellos.

No compiten. Una es la regla del edificio; la otra es una herramienta que vive
en UNA habitación del edificio. La clave de este proyecto es esa: **LangGraph
está confinado a una sola capa y nunca toca el centro.**

---

## 2. El mapa de capas (la regla hexagonal del repo)

```
entrypoints/        ← FastAPI. El mundo HTTP. Maneja el grafo.
   └─ entrypoints/api/agent_router.py

core/application/   ← CASOS DE USO. AQUÍ vive LangGraph. ✅
   ├─ orchestration/ (graph, supervisor, state, confirmation)
   ├─ agents/        (base + 5 especialistas)
   └─ agents/tools/  (los tools que el LLM puede llamar)

core/ports.py       ← INTERFACES (ABC). Contratos puros. ❌ sin frameworks
core/domain.py      ← ENTIDADES puras (Item, SaleNote, Customer...). ❌ sin frameworks

adapters/           ← Implementaciones concretas de los ports.
   └─ facturadorpro7_api/  (HTTP real contra el ERP)
```

La regla de oro está escrita literal en `core/application/agents/base.py`
(líneas 24-26):

> "Esta capa (`core/agents/*`) SÍ puede importar langchain-core/langgraph — es
> application-services, no dominio puro. `core/domain.py`/`core/ports.py` nunca
> importan estos frameworks."

Eso es la frontera hexagonal en acción. Si mañana tirás LangGraph a la basura y
lo reemplazás por otra cosa, `domain.py` y `ports.py` no se enteran. Esa es la
prueba de que la frontera está bien puesta: **LangGraph es un detalle de
implementación de la capa de aplicación, no parte del corazón.**

---

## 3. Qué ES el grafo, concretamente

Definido en `core/application/orchestration/graph.py` (líneas 101-117). El grafo
es literalmente esto:

```
            supervisor
                │  (arista condicional según active_specialist)
   ┌────────┬───┴────┬──────────┬─────────────┐
inventario compras ventas  logistica  contabilidad
   └────────┴────────┴──────────┴─────────────┘
                │
               END
```

Tres conceptos de LangGraph y dónde están en el código:

**a) El estado** — `state.py`, `AgentState` (un `TypedDict`). Es la "mochila" que
viaja por todos los nodos. Lo más importante: el campo
`messages: Annotated[list, add_messages]`. Ese `add_messages` es un *reducer*:
cada nodo devuelve SOLO los mensajes nuevos y LangGraph los acumula solo. Por eso
`SpecialistAgent.ainvoke()` (en `base.py`) devuelve `new_messages` y no la
conversación entera.

**b) Los nodos** son funciones `(state) -> dict parcial`. El supervisor
(`supervisor.py`, `supervisor_node`) devuelve `{"active_specialist": ...}` y NO
toca `messages`. Cada especialista (`graph.py`, `specialist_node`) devuelve
`{"messages": new_messages}`. LangGraph mergea esos dicts parciales sobre el
estado. Nadie pisa lo que no devuelve.

**c) Las aristas.** El supervisor no es un nodo que "hace trabajo": es un
**router**. Decide a qué especialista mandar. IMPORTANTE: el supervisor SOLO
elige especialista — nunca evalúa si la pregunta tiene sentido para el ERP. (Por
eso el guardrail de dominio —"no respondas cosas ajenas al ERP"— vive en el
prompt del agente, en `base.py::DOMAIN_GUARDRAIL`, no en el router.)

---

## 4. Trazá un request real (de punta a punta)

Siguiendo un `POST /agent/chat`:

1. **`agent_router.py` (línea 81)** recibe el JSON. Acá nace algo clave: las
   credenciales del tenant.
   ```python
   creds = TenantCredentials(base_url=..., token=...)
   config = {"configurable": {"creds": creds, "thread_id": session_id}}
   ```

2. **`graph.ainvoke(state, config=config)`**. Arranca el grafo. El `state` lleva
   el mensaje y el `context_module`; el `config` lleva las credenciales **por un
   canal separado**. (Recordá esto: es la joya #1.)

3. **`supervisor_node`**. Si `context_module` es uno de los 5 válidos →
   *fast-path*, sin LLM. Setea `active_specialist`. Si no viene o es inválido →
   un único LLM call clasifica el mensaje (`.with_structured_output()`).

4. La **arista condicional** lee `active_specialist` y enruta al nodo correcto.

5. **El nodo especialista** llama a `agent.ainvoke(...)`. Ahí corre el loop
   acotado de `base.py`: LLM → ¿pidió tools? → ejecutá tools → LLM otra vez →
   hasta que responda sin tools o se agoten 6 iteraciones (`DEFAULT_MAX_ITERATIONS`).

6. Termina en `END`. El router toma el último mensaje y lo devuelve como `answer`.

---

## 5. Las tres joyas de diseño (acá está el oro)

### Joya 1 — Las credenciales NUNCA viajan en el estado ni en el schema del LLM

¿Por qué `config.configurable` y no un campo más en `AgentState`? Porque el
estado lo persiste el **checkpointer** (`graph.py`, `InMemorySaver` hoy, Postgres
mañana). Si metieras el Bearer token en el estado, lo estarías escribiendo a la
base de datos. Lo explica `state.py` (líneas 17-23).

Y va más fino. En `agents/tools/_shared.py`:
```python
InjectedConfig = Annotated[RunnableConfig, InjectedToolArg]
```
Cuando un tool declara `config: InjectedConfig`, `@tool` **excluye ese parámetro
del JSON schema que ve el LLM**. O sea: el modelo nunca ve el token, no puede
inventarlo ni filtrarlo. La credencial entra por la puerta de atrás (`config`) y
sale directo al adapter. **Seguridad por construcción, no por disciplina.**

### Joya 2 — Los tools construyen su adapter al vuelo, por request

El grafo se compila UNA vez al arrancar (singleton). Pero las credenciales son
por-request. ¿Cómo se concilia? En cualquier tool, ej.
`agents/tools/inventory_tools.py`:
```python
client = build_client(config)        # cliente NUEVO con las creds de ESTE request
adapter = InventoryAdapter(client)
...
finally:
    await client.aclose()
```
El agente (LLM + tools) es un singleton de larga vida SIN credenciales adentro.
El adapter concreto contra FacturadorPro7 se arma fresco en cada invocación de
tool y se cierra. **Multi-tenant correcto:** dos empresas usando el mismo proceso
jamás comparten un cliente HTTP.

Y la frontera hexagonal acá: el tool (capa aplicación) llama a `InventoryAdapter`
(capa adapters), que implementa `InventoryPort` (capa ports). El tool NO conoce
HTTP; conoce el contrato. El adapter es el único que sabe que del otro lado hay
un Laravel con endpoints REST.

### Joya 3 — `interrupt()`: pausar el grafo para pedirle permiso a un humano

La más linda. En `inventory_tools.py`, `registrar_movimiento_stock` es una
escritura irreversible. Lo PRIMERO que hace, antes del POST real:
```python
decision = interrupt({"tool_name": ..., "summary": ..., "tool_args": ...})
if not (isinstance(decision, dict) and decision.get("approved")):
    return "Movimiento RECHAZADO..."
# recién acá construye el adapter y escribe
```
`interrupt()` **congela el grafo entero** en medio del tool y se lo devuelve al
HTTP. El router lo detecta (`confirmation.py::parse_interrupt_payload`) y responde
`status="awaiting_confirmation"`. El usuario aprueba con `POST /agent/confirm`,
que hace `graph.ainvoke(Command(resume={"approved": True}))`... y el grafo
**resucita DENTRO del mismo tool, en la línea siguiente al `interrupt()`**. La
variable `decision` recibe ese `{"approved": True}` y sigue.

Es human-in-the-loop nativo. Sutileza: como el checkpointer NO persiste las
credenciales (joya 1), el router tiene que guardarlas aparte (`_PENDING_CREDS`)
para reconstruir el adapter cuando el tool reanude. Las dos decisiones de diseño
se abrazan.

---

## 6. Síntesis (lo que hay que llevarse)

- **Hexagonal = regla de dependencias.** El centro (`domain`, `ports`) no sabe
  nada del mundo. Test: ¿podés borrar LangGraph sin tocar `domain.py`? Sí →
  frontera bien puesta.
- **LangGraph = orquestación, confinada a `core/application`.** Es un ciudadano
  de la capa de aplicación. Nunca sube al dominio.
- **El estado viaja; las credenciales viajan APARTE.** Esa separación es lo que
  hace el sistema seguro y multi-tenant.
- **El `interrupt()` vive dentro del tool**, no en el grafo — la confirmación
  está pegada a la operación riesgosa, donde tiene que estar.

---

## 7. Ejercicio para fijar el concepto

*Si tuvieras que agregar un agente de "Recursos Humanos" mañana, ¿qué archivos
tocás y cuáles NO podés tocar?*

Pista: mirá cuántas líneas tiene `inventario_agent.py` y por qué es tan corto. Si
respondés bien esto, entendiste la arquitectura.

---

## Apéndice — Mapa rápido de archivos

| Archivo | Rol |
|---|---|
| `entrypoints/api/agent_router.py` | HTTP: `/agent/chat`, `/agent/confirm`, `/agent/session/{id}` |
| `core/application/orchestration/graph.py` | Construye y compila el `StateGraph` |
| `core/application/orchestration/supervisor.py` | Router: elige especialista (fast-path o LLM) |
| `core/application/orchestration/state.py` | `AgentState` (el estado que viaja) |
| `core/application/orchestration/confirmation.py` | Traduce `__interrupt__` ↔ `Command(resume=...)` |
| `core/application/agents/base.py` | `SpecialistAgent` + loop acotado + `DOMAIN_GUARDRAIL` |
| `core/application/agents/<modulo>_agent.py` | Definición fina de cada especialista (prompt + tools) |
| `core/application/agents/tools/<x>_tools.py` | Tools que el LLM puede llamar (con `interrupt()` en las escrituras) |
| `core/application/agents/tools/_shared.py` | Inyección de credenciales + `build_client` |
| `core/ports.py` | Interfaces (puertos) — contratos puros |
| `core/domain.py` | Entidades puras del dominio |
| `adapters/facturadorpro7_api/*` | Implementaciones HTTP reales contra el ERP |
</content>
</invoke>
