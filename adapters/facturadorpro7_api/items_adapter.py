"""
Adapter ItemsPort — búsqueda/creación liviana de productos.
Compartido por Compras y Ventas para el flujo inline "crear si no existe".

Endpoints reales (openapi.yaml):
  GET  /api/document/search-items  -> search()
  POST /api/item                   -> create()
"""
from __future__ import annotations

from typing import List

from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from core.domain import Item, ItemDraft
from core.ports import ItemsPort


class ItemsAdapter(ItemsPort):
    def __init__(self, client: FacturadorPro7Client):
        self._client = client

    async def search(self, query: str, *, by_barcode: bool = False, page: int = 1) -> List[Item]:
        params = {"input": query}
        if by_barcode:
            # Laravel casts ANY non-empty query string to boolean true except
            # "0" — sending search_by_barcode=false/False(python bool)/0
            # still triggers exact-barcode-only matching server-side and
            # silently returns zero results for a normal text search.
            # Discovered live against the sandbox tenant. Only send the key
            # when explicitly requesting barcode mode, with the value "1".
            params["search_by_barcode"] = "1"
        result = await self._client.get("/api/document/search-items", params=params)
        raw_items = (result.get("data") or {}).get("items", [])
        return [self._to_item(raw) for raw in raw_items]

    async def create(self, item: ItemDraft) -> Item:
        # NOTE: openapi.yaml documents only description/unit_type_id/
        # currency_type_id/sale_unit_price/sale_affectation_igv_type_id as
        # required. A real 422 against the sandbox tenant revealed this
        # tenant's actual Laravel validation ALSO requires internal_id,
        # purchase_unit_price, purchase_affectation_igv_type_id, stock, and
        # stock_min — discovered live, not guessed. Defaults below are the
        # minimum that satisfies validation for a fresh item with no stock.
        payload = {
            "description": item.description,
            "internal_id": self._generate_internal_id(item),
            "unit_type_id": "NIU",
            "currency_type_id": "PEN",
            "sale_unit_price": item.price,
            "purchase_unit_price": item.price,
            "sale_affectation_igv_type_id": "10" if item.has_igv else "20",
            "purchase_affectation_igv_type_id": "10" if item.has_igv else "20",
            "has_igv": item.has_igv,
            "stock": 0,
            "stock_min": 0,
        }
        if item.barcode is not None:
            payload["barcode"] = item.barcode
        if item.image is not None:
            payload["image"] = item.image
        result = await self._client.post("/api/item", json=payload)
        raw = result.get("data") or {}
        return self._to_item(raw)

    @staticmethod
    def _generate_internal_id(item: ItemDraft) -> str:
        """The API requires a unique internal_id (SKU) per item. ItemDraft
        has no such field in the domain (it's an ERP-internal code, not a
        business concept the agent reasons about), so derive a short
        deterministic-enough code from the barcode if present, otherwise
        from a hash of the description + a timestamp to avoid collisions."""
        if item.barcode:
            return item.barcode[:30]
        import time
        return f"AUTO-{abs(hash(item.description)) % 100000}-{int(time.time()) % 100000}"

    @staticmethod
    def _to_item(raw: dict) -> Item:
        price_raw = raw.get("sale_unit_price", raw.get("price", 0))
        try:
            price = float(price_raw)
        except (TypeError, ValueError):
            price = 0.0
        return Item(
            id=raw.get("id") or raw.get("item_id"),
            description=raw.get("description") or raw.get("full_description") or "",
            price=price,
            barcode=raw.get("barcode"),
            has_igv=bool(raw.get("has_igv", True)),
            active=bool(raw.get("active", True)),
            favorite=bool(raw.get("favorite", False)),
            category_id=raw.get("category_id"),
            brand_id=raw.get("brand_id"),
            stock=raw.get("stock"),
        )
