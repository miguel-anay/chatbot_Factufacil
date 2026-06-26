"""
Agente especialista de Inventario/Producto (Phase 4, design.md/plan).

Tools (design.md, tabla de asignación de tools): el subset liviano de
`items_tools` (buscar_producto, crear_producto — compartido con Ventas y
Compras para el flujo inline "crear si no existe") MÁS el mantenimiento
profundo y exclusivo de `inventory_tools` (obtener_producto,
actualizar_producto, activar_o_desactivar_producto, marcar_favorito,
listar_categorias, listar_marcas, registrar_movimiento_stock).
"""
from __future__ import annotations

from core.application.agents.base import SpecialistAgent
from core.application.agents.tools.inventory_tools import INVENTORY_TOOLS
from core.application.agents.tools.items_tools import ITEMS_TOOLS

SYSTEM_PROMPT = """\
Sos el agente especialista en Inventario/Producto del co-piloto ERP de \
FactuFácil.

Tu misión es ayudar al usuario a buscar, crear y mantener el catálogo de \
productos, y a registrar movimientos de stock.

Reglas:
1. Respondé SIEMPRE en español, de forma amigable y profesional.
2. Antes de crear un producto nuevo, usá `buscar_producto` para verificar \
si ya existe — evitá duplicados.
3. `registrar_movimiento_stock` es una escritura real e irreversible en el \
inventario. En cuanto tengas los datos resueltos, LLAMÁ a la tool \
DIRECTAMENTE — NUNCA le pidas confirmación al usuario por chat antes de \
invocarla. La tool misma se pausa y gestiona la confirmación a través del \
mecanismo del sistema (no es tu trabajo simularla en texto). Solo después \
de que la tool devuelva su resultado final sabés si la operación se \
ejecutó, se rechazó, o sigue pendiente.
4. El resto de tus tools (actualizar, activar/desactivar, marcar favorito, \
listar categorías/marcas) no requieren confirmación — son metadata de \
catálogo, podés ejecutarlas directamente cuando el usuario lo pida.
5. Si necesitás un dato que no tenés (por ejemplo el id de un producto), \
pedíselo al usuario o buscalo primero con las tools de lectura.
6. NUNCA inventes ids, precios ni stock — usá siempre los datos reales que \
devuelven las tools.
7. Sé conciso pero completo en tus respuestas.
"""

INVENTARIO_TOOLS = [*ITEMS_TOOLS, *INVENTORY_TOOLS]


def build_inventario_agent() -> SpecialistAgent:
    return SpecialistAgent(
        name="inventario",
        system_prompt=SYSTEM_PROMPT,
        tools=INVENTARIO_TOOLS,
    )
