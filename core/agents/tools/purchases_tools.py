"""
Tools de `PurchasesPort` — registro de una compra, agente de Compras.

`crear_compra` es interrupt-gated (clasificación de escritura del plan/
design.md: registrar una compra es una escritura financiera real).

`item_snapshots` (design.md, "Interfaces / Contracts" — PurchasesPort):
Phase 2 follow-up confirmó vía source-code real que `purchase_items.item`
es una columna NOT-NULL que la API NO completa server-side — el snapshot
por línea es responsabilidad del caller. Esta tool expone `item_snapshots`
como parte explícita del input (subset description/internal_id/unit_type_id/
item_code), no como un campo oculto — el agente (PR 5) debe resolverlo de
`ItemsPort.search()`/`create()` antes de invocar esta tool, ya sea que el
LLM lo arme a partir del resultado de `buscar_producto` o lo pida al usuario.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from adapters.facturadorpro7_api.purchases_adapter import PurchasesAdapter
from core.agents.tools._shared import InjectedConfig, build_client


class ItemSnapshotInput(BaseModel):
    description: str = Field(description="Descripción del producto al momento de la compra (snapshot).")
    internal_id: Optional[str] = Field(default=None, description="SKU/código interno del producto, si se conoce.")
    unit_type_id: str = Field(default="NIU", description="Unidad de medida (por defecto 'NIU' = unidad).")
    item_code: Optional[str] = Field(default=None, description="Código de producto, si se conoce.")


class LineaCompraInput(BaseModel):
    item_id: int = Field(description="ID del producto comprado (de buscar_producto).")
    quantity: float = Field(description="Cantidad comprada de este producto.")
    unit_price: float = Field(description="Precio de compra unitario.")
    item_snapshot: ItemSnapshotInput = Field(
        description=(
            "Snapshot del producto al momento de la compra — REQUERIDO por "
            "la API real (no documentado en el spec, descubierto vía lectura "
            "de código fuente, ver docstring del módulo). Resolvé estos "
            "datos del resultado de buscar_producto antes de llamar a esta tool."
        )
    )


class CrearCompraInput(BaseModel):
    document_type_id: str = Field(description="Tipo de documento del proveedor (ej. '01'=Factura, '03'=Boleta).")
    series: str = Field(description="Serie del documento del proveedor.")
    number: str = Field(description="Número del documento del proveedor.")
    date_of_issue: str = Field(description="Fecha de emisión en formato ISO 'YYYY-MM-DD'.")
    supplier_id: int = Field(description="ID del proveedor (de buscar_proveedor).")
    items: List[LineaCompraInput] = Field(description="Líneas de la compra — al menos un ítem.")


@tool("crear_compra", args_schema=CrearCompraInput)
async def crear_compra(
    document_type_id: str,
    series: str,
    number: str,
    date_of_issue: str,
    supplier_id: int,
    items: List[LineaCompraInput],
    config: InjectedConfig,
) -> str:
    """Registra una compra a un proveedor. Es una escritura financiera real
    — REQUIERE confirmación humana antes de ejecutarse."""
    decision = interrupt(
        {
            "tool_name": "crear_compra",
            "summary": (
                f"Registrar compra al proveedor id={supplier_id}: documento "
                f"{document_type_id} {series}-{number}, {len(items)} línea(s)."
            ),
            "tool_args": {
                "document_type_id": document_type_id,
                "series": series,
                "number": number,
                "date_of_issue": date_of_issue,
                "supplier_id": supplier_id,
                "items": [li.model_dump() for li in items],
            },
        }
    )
    if not (isinstance(decision, dict) and decision.get("approved")):
        return "Compra RECHAZADA por el usuario — no se registró ningún movimiento."

    total = round(sum(li.quantity * li.unit_price for li in items), 2)
    draft: Dict[str, Any] = {
        "document_type_id": document_type_id,
        "series": series,
        "number": number,
        "date_of_issue": date_of_issue,
        "supplier_id": supplier_id,
        "items": [
            {"item_id": li.item_id, "quantity": li.quantity, "unit_price": li.unit_price}
            for li in items
        ],
        "total": total,
    }
    snapshots = [li.item_snapshot.model_dump(exclude_none=True) for li in items]

    client = build_client(config)
    adapter = PurchasesAdapter(client)
    try:
        purchase = await adapter.create_purchase(draft, item_snapshots=snapshots)
    finally:
        await client.aclose()
    return (
        f"Compra registrada: id={purchase.id} | numero={purchase.number} | "
        f"proveedor_id={purchase.supplier_id} | total={purchase.total}"
    )


PURCHASES_TOOLS: List = [crear_compra]
