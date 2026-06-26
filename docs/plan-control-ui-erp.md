# Plan — Control de la UI del ERP desde el co-piloto

> **Estado: PROPUESTA. No implementado.** Documento de diseño para discutir y
> ejecutar más adelante. Nada de esto está en el código todavía.

## 1. Objetivo

Que el co-piloto pueda **dirigir la interfaz del ERP** (FacturadorPro7, Vue2):
navegar a una pantalla, aplicar un filtro, abrir un modal, prellenar un
formulario. Ejemplo guía:

> Usuario: "buscame productos con P" → la grilla de productos del ERP se abre
> ya filtrada por "P", y el co-piloto NO vuelca las filas al chat (ahorro de
> tokens + UX nativa).

## 2. Principio rector

El backend **no toca la UI**. El agente **emite directivas**; el frontend del
ERP las **ejecuta**. Es un contrato JSON, no acceso al DOM.

```
Agente (este repo)                ERP frontend (Vue2)
 ─ decide la intención     →      ─ recibe ui_actions[]
 ─ EMITE una directiva            ─ las interpreta y ejecuta
   {action, ...payload}             (vue-router / store / modal)
```

Beneficio clave: **desacople total.** Si el ERP cambia de framework, el contrato
de acciones sigue igual. El agente nunca conoce el DOM ni los componentes Vue.

## 3. La línea roja (restricciones NO negociables)

1. **Las ESCRITURAS no se hacen puppeteando la UI.** Siguen yendo por los tools
   de API con `interrupt()` (venta, compra, stock) — confirmación humana +
   validación server-side del ERP. La UI-control es solo
   **navegación / filtrado / presentación / prellenado**.
2. **`prefill_form` es el máximo nivel permitido en UI:** el agente llena campos
   para que el HUMANO revise y apriete el botón. El agente nunca "envía" un form.
3. **El agente NO accede a la DB directamente**, aunque tengamos acceso. Sigue
   entrando por `ports`/`adapters` contra la API del ERP (respeta permisos,
   multi-tenancy, y la frontera hexagonal). La DB es herramienta nuestra para
   read-models/búsqueda, no el patio del LLM.

## 4. El contrato de acciones (`ui_actions`)

Una lista de objetos. Empezar con pocas y crecer. Catálogo inicial propuesto:

| action | payload | efecto en el frontend |
|---|---|---|
| `navigate` | `{ route, params }` | `router.push` a una pantalla |
| `filter_products` | `{ query, page }` | abre la grilla de productos filtrada |
| `open_modal` | `{ modal, context }` | abre un modal del ERP |
| `prefill_form` | `{ form, fields }` | precarga campos (el humano confirma) |
| `highlight` | `{ selector_id }` | resalta un elemento (onboarding/guía) |

> El catálogo se versiona. El frontend ignora acciones que no conoce (forward-
> compatible): nunca rompe si el backend manda una acción nueva.

## 5. Cambios en el BACKEND (este repo)

### 5.1 Schema de respuesta — `entrypoints/api/schemas.py`
Agregar a `AgentChatResponse`:
```python
ui_actions: List[Dict[str, Any]] = Field(default_factory=list)
```

### 5.2 Estado del grafo — `core/application/orchestration/state.py`
Agregar a `AgentState` un canal acumulador (mismo principio que `add_messages`):
```python
ui_actions: Annotated[list, operator.add]
```

### 5.3 Tools que EMITEN acciones — `core/application/agents/tools/`
Un tool que devuelve un `Command` de LangGraph escribiendo en el estado, en vez
de volcar datos al LLM:
```python
@tool("mostrar_grilla_productos")
async def mostrar_grilla_productos(query: str, page: int, config: InjectedConfig) -> Command:
    return Command(update={
        "ui_actions": [{"action": "filter_products", "query": query, "page": page}],
        "messages": [ToolMessage("Le mostré al usuario la grilla filtrada.", tool_call_id=...)],
    })
```
El `ToolMessage` le dice al LLM "ya está, no vuelques filas" → **tokens ≈ 0** en
datos. La `ui_action` viaja por el estado hasta el router.

### 5.4 Router — `entrypoints/api/agent_router.py`
Tras `graph.ainvoke(...)`:
```python
ui_actions = result.get("ui_actions", [])
# incluirlo en AgentChatResponse(..., ui_actions=ui_actions)
```
Verificar interacción con el flujo de `confirmation` (un turno puede pausar por
`interrupt()` Y traer acciones; definir orden de prioridad).

### 5.5 Catálogo versionado
Doc/enum único con las acciones soportadas y su payload — fuente de verdad
compartida con el equipo de frontend.

## 6. Cambios en el FRONTEND del ERP (Vue2)

1. **Widget de chat** que llama `POST /agent/chat` enviando lo que el schema ya
   pide: `tenant_base_url`, `tenant_token` (sesión ya autenticada) y
   `context_module` (módulo actual — ya soportado en `schemas.py`).
2. **Action dispatcher** — mapea cada `ui_action` a Vue:
   ```js
   const handlers = {
     filter_products: a => router.push({ name: 'products', query: { search: a.query, page: a.page } }),
     navigate:        a => router.push({ name: a.route, params: a.params }),
     open_modal:      a => store.dispatch('ui/openModal', a),
     prefill_form:    a => store.commit('form/prefill', a.fields),
   }
   // acciones desconocidas: ignorar (forward-compatible)
   ```
3. **Render del chat** + manejo de `status: "awaiting_confirmation"` (botón
   Aprobar/Rechazar → `POST /agent/confirm`). Este flujo ya existe en el backend.

## 7. Roadmap por fases (vertical slices)

- **Fase 0 — contrato.** Cerrar el catálogo inicial de acciones + el campo
  `ui_actions` en el schema. Solo diseño/acuerdo con frontend.
- **Fase 1 — un slice end-to-end: `filter_products`.** Backend (state + tool +
  router) + frontend (widget + dispatcher de esa única acción). Validar el
  circuito completo. Si funciona, el patrón está probado.
- **Fase 2 — navegación.** `navigate` + `open_modal` (read-only, bajo riesgo).
- **Fase 3 — `prefill_form`.** Recién acá se toca prellenado de escritura;
  diseñar con cuidado qué revisa/confirma el humano.

## 8. Preguntas abiertas (decidir antes de Fase 1)

1. ¿`ui_actions` y `confirmation` pueden coexistir en una misma respuesta? ¿Cuál
   tiene prioridad de render?
2. ¿El frontend ejecuta TODAS las acciones de la lista o solo la última? (Sugerido:
   todas, en orden.)
3. ¿Alcance inicial: solo NAVEGAR/MOSTRAR, o ya incluimos `prefill_form`?
   (Recomendación: arrancar solo navegación/mostrar.)
4. ¿Cómo audita el frontend qué acciones ejecutó? (logging para debugging.)

## 9. Relación con el cap de búsqueda (ya implementado)

El tope `MAX_SEARCH_RESULTS` en `items_tools.py` es la mejora barata e inmediata
para el costo de tokens MIENTRAS no exista este canal de UI. Cuando la Fase 1
esté lista, las búsquedas de **navegación** dejarán de volcar filas por completo
(las muestra la grilla nativa); `buscar_producto` con datos quedará reservado
para el caso "resolver un id para una venta/compra", donde sí se necesitan unos
pocos candidatos en contexto. Los dos mecanismos son complementarios, no se
pisan.
</content>
