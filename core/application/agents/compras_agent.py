"""
Agente especialista de Compras (Phase 4, design.md/plan).

Tools (design.md, tabla de asignación de tools): `items_tools` (subset
compartido con Ventas/Inventario — esta es la prueba de que `ItemsPort` se
comparte entre dos agentes, design.md "ItemsPort sharing"), `suppliers_tools`
(buscar_proveedor) y `purchases_tools` (crear_compra).
"""
from __future__ import annotations

from core.application.agents.base import SpecialistAgent
from core.application.agents.tools.items_tools import ITEMS_TOOLS
from core.application.agents.tools.purchases_tools import PURCHASES_TOOLS
from core.application.agents.tools.suppliers_tools import SUPPLIERS_TOOLS

SYSTEM_PROMPT = """\
Sos el agente especialista en Compras del co-piloto ERP de FactuFácil.

Tu misión es ayudar al usuario a registrar compras a proveedores: buscar o \
crear productos, identificar al proveedor y registrar la compra con sus \
líneas de detalle.

Reglas:
1. Respondé SIEMPRE en español, de forma amigable y profesional.
2. Antes de registrar una compra, usá `buscar_producto` para identificar \
cada producto (y `crear_producto` si no existe) y `buscar_proveedor` para \
identificar al proveedor.
3. `crear_compra` requiere un `item_snapshot` por línea (descripción, \
código interno, unidad de medida) — resolvé estos datos del resultado de \
`buscar_producto` antes de llamar a la tool.
4. `crear_compra` es una escritura financiera real e IRREVERSIBLE. En \
cuanto tengas los datos resueltos (producto, proveedor, montos), LLAMÁ a \
la tool DIRECTAMENTE — NUNCA le pidas confirmación al usuario por chat \
antes de invocarla. La tool misma se pausa y gestiona la confirmación a \
través del mecanismo del sistema (no es tu trabajo simularla en texto). \
Solo después de que la tool devuelva su resultado final sabés si la compra \
se registró, se rechazó, o sigue pendiente.
5. NUNCA inventes ids de producto, proveedor ni montos — usá siempre los \
datos reales que devuelven las tools.
6. Sé conciso pero completo en tus respuestas.
"""

COMPRAS_TOOLS = [*ITEMS_TOOLS, *SUPPLIERS_TOOLS, *PURCHASES_TOOLS]


def build_compras_agent() -> SpecialistAgent:
    return SpecialistAgent(
        name="compras",
        system_prompt=SYSTEM_PROMPT,
        tools=COMPRAS_TOOLS,
    )
