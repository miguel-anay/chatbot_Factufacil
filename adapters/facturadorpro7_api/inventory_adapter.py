"""
Adapter InventoryPort — mantenimiento profundo de catálogo y stock,
exclusivo del agente de Inventario/Producto.

Endpoints reales (openapi.yaml):
  GET  /api/items/record/{id}                      -> get_item()
  POST /api/items/update                           -> update_item()
  GET  /api/items/change-active/{id}/{active}       -> change_active()
  GET  /api/items/change-favorite/{id}/{favorite}   -> change_favorite()
  GET  /api/categories-records                      -> list_categories()
  GET  /api/brands-records                          -> list_brands()
  POST /api/inventory/transaction                   -> register_transaction()  (interrupt — see tools layer)
"""
from __future__ import annotations

from typing import Any, Dict, List

from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from core.domain import Brand, Category, Item, StockMovement, StockTxn
from core.ports import InventoryPort


class InventoryAdapter(InventoryPort):
    def __init__(self, client: FacturadorPro7Client):
        self._client = client

    async def get_item(self, id: int) -> Item:
        result = await self._client.get(f"/api/items/record/{id}")
        raw = result.get("data") if isinstance(result, dict) and "data" in result else result
        return self._to_item(raw or {})

    async def update_item(self, id: int, patch: Dict[str, Any]) -> Item:
        # NOTE: openapi.yaml documents only id/description as required for
        # this endpoint. A real 422 against the sandbox revealed this
        # tenant's actual validation ALSO requires unit_type_id,
        # currency_type_id, sale_unit_price, purchase_unit_price,
        # sale_affectation_igv_type_id and purchase_affectation_igv_type_id
        # — i.e. it behaves like a full-record PUT, not a partial PATCH,
        # even though the field name says "update". Discovered live, not
        # guessed: fetch the current full item first, merge the caller's
        # patch on top, then send the complete required set.
        current = await self.get_item(id)
        payload: Dict[str, Any] = {
            "id": id,
            "description": current.description,
            "unit_type_id": "NIU",
            "currency_type_id": "PEN",
            "sale_unit_price": current.price,
            "purchase_unit_price": current.price,
            "sale_affectation_igv_type_id": "10" if current.has_igv else "20",
            "purchase_affectation_igv_type_id": "10" if current.has_igv else "20",
            "has_igv": current.has_igv,
        }
        if current.barcode:
            payload["barcode"] = current.barcode
        payload.update(patch)
        await self._client.post("/api/items/update", json=payload)
        return await self.get_item(id)

    async def change_active(self, id: int, active: bool) -> None:
        await self._client.get(f"/api/items/change-active/{id}/{1 if active else 0}")

    async def change_favorite(self, id: int, favorite: bool) -> None:
        await self._client.get(f"/api/items/change-favorite/{id}/{1 if favorite else 0}")

    async def list_categories(self) -> List[Category]:
        result = await self._client.get("/api/categories-records")
        raw_list = self._unwrap_list(result)
        return [Category(id=r.get("id"), name=r.get("name") or r.get("description") or "") for r in raw_list]

    async def list_brands(self) -> List[Brand]:
        result = await self._client.get("/api/brands-records")
        raw_list = self._unwrap_list(result)
        return [Brand(id=r.get("id"), name=r.get("name") or r.get("description") or "") for r in raw_list]

    async def register_transaction(self, txn: StockTxn) -> StockMovement:
        payload = {
            "item_code": txn.item_code,
            "type": txn.type,
            "warehouse_id": txn.warehouse_id,
            "inventory_transaction_id": txn.inventory_transaction_id,
            "quantity": txn.quantity,
        }
        result = await self._client.post("/api/inventory/transaction", json=payload)
        return StockMovement(
            id=(result or {}).get("id", 0),
            item_code=txn.item_code,
            type=txn.type,
            warehouse_id=txn.warehouse_id,
            quantity=txn.quantity,
            resulting_stock=(result or {}).get("stock"),
        )

    @staticmethod
    def _unwrap_list(result: Any) -> List[Dict[str, Any]]:
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("data", "categories", "brands"):
                value = result.get(key)
                if isinstance(value, list):
                    return value
        return []

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
