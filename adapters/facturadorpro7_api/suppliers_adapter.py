"""
Adapter SuppliersPort — búsqueda de proveedores para el agente de Compras.

Endpoint real (openapi.yaml):
  GET /api/purchases/search-suppliers -> search()
"""
from __future__ import annotations

from typing import List

from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from core.domain import Supplier
from core.ports import SuppliersPort


class SuppliersAdapter(SuppliersPort):
    def __init__(self, client: FacturadorPro7Client):
        self._client = client

    async def search(self, query: str) -> List[Supplier]:
        result = await self._client.get("/api/purchases/search-suppliers", params={"input": query})
        raw_list = self._unwrap_list(result)
        return [self._to_supplier(raw) for raw in raw_list]

    @staticmethod
    def _unwrap_list(result) -> list:
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("suppliers", "customers", "persons"):
                    value = data.get(key)
                    if isinstance(value, list):
                        return value
        return []

    @staticmethod
    def _to_supplier(raw: dict) -> Supplier:
        return Supplier(
            id=raw.get("id"),
            document_number=raw.get("number") or "",
            name=raw.get("name") or raw.get("description") or "",
            address=raw.get("address"),
            email=raw.get("email"),
        )
