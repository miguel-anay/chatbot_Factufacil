"""
Tools de `SuppliersPort` — búsqueda de proveedores, agente de Compras.
Solo lectura — no requiere confirmación humana.
"""
from __future__ import annotations

from typing import List

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from adapters.facturadorpro7_api.suppliers_adapter import SuppliersAdapter
from core.application.agents.tools._shared import InjectedConfig, build_client


class BuscarProveedorInput(BaseModel):
    query: str = Field(description="Texto a buscar: nombre, razón social o número de documento del proveedor.")


@tool("buscar_proveedor", args_schema=BuscarProveedorInput)
async def buscar_proveedor(query: str, config: InjectedConfig) -> str:
    """Busca proveedores registrados por nombre, razón social o número de
    documento. Usá esta tool ANTES de crear una compra para identificar al
    proveedor. Solo lectura, no requiere confirmación."""
    client = build_client(config)
    adapter = SuppliersAdapter(client)
    try:
        suppliers = await adapter.search(query)
    finally:
        await client.aclose()
    if not suppliers:
        return f"No se encontraron proveedores para '{query}'."
    return "\n".join(
        f"- id={s.id} | {s.name} | doc={s.document_number} | email={s.email or 'N/A'}"
        for s in suppliers
    )


SUPPLIERS_TOOLS: List = [buscar_proveedor]
