"""
Adapter PurchasesPort — registro de una compra (interrupt — capa de tools).

Endpoint real (openapi.yaml):
  POST /api/purchases -> create_purchase()
"""
from __future__ import annotations

from typing import Any, Dict

from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from core.domain import Purchase
from core.ports import PurchasesPort


class PurchasesAdapter(PurchasesPort):
    def __init__(self, client: FacturadorPro7Client):
        self._client = client

    async def create_purchase(self, draft: Dict[str, Any]) -> Purchase:  # interrupt (tools layer)
        # NOTE: openapi.yaml documents document_type_id/series/number/
        # date_of_issue/supplier_id/items as required. Real 500s against the
        # sandbox tenant (non-null-constraint failures, discovered live, not
        # guessed) revealed this tenant's `purchases` table ALSO requires
        # time_of_issue, currency_type_id and exchange_rate_sale with no DB
        # default. Fill sane defaults when the caller's draft omits them;
        # the caller's explicit values always win (merge order below).
        from datetime import datetime

        payload: Dict[str, Any] = {
            "time_of_issue": datetime.now().strftime("%H:%M:%S"),
            "currency_type_id": "PEN",
            "exchange_rate_sale": 1.0,
            **draft,
        }
        result = await self._client.post("/api/purchases", json=payload)
        raw = result.get("data") or {}
        return Purchase(
            id=raw.get("id"),
            supplier_id=draft.get("supplier_id"),
            doc_type_id=draft.get("document_type_id", ""),
            series=draft.get("series", ""),
            number=raw.get("number_full") or draft.get("number", ""),
            date_of_issue=draft.get("date_of_issue", ""),
            items=draft.get("items", []),
            total=float(draft.get("total", 0) or 0),
        )
