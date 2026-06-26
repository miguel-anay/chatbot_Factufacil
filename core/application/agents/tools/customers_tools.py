"""
Tools de `CustomersPort` — búsqueda de clientes, agente de Ventas.
Solo lectura — no requiere confirmación humana.
"""
from __future__ import annotations

from typing import List

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from adapters.facturadorpro7_api.customers_adapter import CustomersAdapter
from core.application.agents.tools._shared import InjectedConfig, build_client


class BuscarClienteInput(BaseModel):
    query: str = Field(description="Texto a buscar: nombre, razón social o número de documento del cliente.")


@tool("buscar_cliente", args_schema=BuscarClienteInput)
async def buscar_cliente(query: str, config: InjectedConfig) -> str:
    """Busca clientes registrados por nombre, razón social o número de
    documento. Usá esta tool ANTES de crear una venta para identificar al
    cliente. Solo lectura, no requiere confirmación."""
    client = build_client(config)
    adapter = CustomersAdapter(client)
    try:
        customers = await adapter.search(query)
    finally:
        await client.aclose()
    if not customers:
        return f"No se encontraron clientes para '{query}'."
    return "\n".join(
        f"- id={c.id} | {c.name} | doc={c.document_number} | email={c.email or 'N/A'}"
        for c in customers
    )


CUSTOMERS_TOOLS: List = [buscar_cliente]
