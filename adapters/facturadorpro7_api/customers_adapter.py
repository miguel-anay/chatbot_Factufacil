"""
Adapter CustomersPort — búsqueda de clientes para el agente de Ventas.

Endpoint real (openapi.yaml):
  GET /api/document/search-customers -> search()
"""
from __future__ import annotations

from typing import List

from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from core.domain import Customer
from core.ports import CustomersPort


class CustomersAdapter(CustomersPort):
    def __init__(self, client: FacturadorPro7Client):
        self._client = client

    async def search(self, query: str) -> List[Customer]:
        result = await self._client.get("/api/document/search-customers", params={"input": query})
        raw_list = (result.get("data") or {}).get("customers", [])
        return [self._to_customer(raw) for raw in raw_list]

    @staticmethod
    def _to_customer(raw: dict) -> Customer:
        return Customer(
            id=raw.get("id"),
            document_number=raw.get("number") or "",
            name=raw.get("name") or raw.get("description") or "",
            address=raw.get("address"),
            email=raw.get("email"),
        )
