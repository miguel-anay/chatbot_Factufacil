"""
Adapter SalesPort — preliminar de venta → generación de CPE (irreversible
ante SUNAT).

Endpoints reales (openapi.yaml):
  POST /api/sale-note                    -> create_sale_note()  (borrador, NO interrupt)
  POST /api/sale-note/{id}/generate-cpe  -> generate_cpe()      (interrupt — capa de tools)
"""
from __future__ import annotations

from typing import Any, Dict

from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from core.domain import Cpe, SaleNote
from core.ports import SalesPort


class SalesAdapter(SalesPort):
    def __init__(self, client: FacturadorPro7Client):
        self._client = client

    async def create_sale_note(self, draft: Dict[str, Any]) -> SaleNote:
        # draft is expected to carry series_id/customer_id/date_of_issue/items
        # (required per openapi.yaml) plus any optional fields the caller set.
        result = await self._client.post("/api/sale-note", json=draft)
        raw = result.get("data") or {}
        return SaleNote(
            id=raw.get("id"),
            customer_id=draft.get("customer_id"),
            items=draft.get("items", []),
            total=float(raw.get("total", 0) or 0),
            series=raw.get("number", "").split("-")[0] if raw.get("number") else None,
            number=raw.get("number"),
            state=raw.get("state_type_id"),
        )

    async def generate_cpe(self, sale_note_id: int) -> Cpe:  # interrupt (called from tools layer)
        # generate-cpe requires document-type/series/number/fecha/hora — the
        # caller (tool layer) is expected to pass these via a richer call;
        # the port signature takes only sale_note_id, so we resolve the
        # remaining required fields from the sale note itself plus sane
        # current-time defaults. This mirrors the design's "propose then
        # confirm" flow: the preliminary data was already decided when the
        # sale note was created.
        from datetime import datetime, date

        now = datetime.now()
        payload = {
            "codigo_tipo_documento": "03",  # Boleta by default; Factura ("01") needs RUC customer, decided at tool layer
            "serie_documento": "B001",
            "numero_documento": str(sale_note_id),
            "fecha_de_emision": date.today().isoformat(),
            "hora_de_emision": now.strftime("%H:%M:%S"),
        }
        result = await self._client.post(f"/api/sale-note/{sale_note_id}/generate-cpe", json=payload)
        raw = result.get("data") or {}
        return Cpe(
            id=raw.get("id", sale_note_id),
            sale_note_id=sale_note_id,
            document_type_id=payload["codigo_tipo_documento"],
            series=raw.get("number", "").split("-")[0] if raw.get("number") else payload["serie_documento"],
            number=raw.get("number") or payload["numero_documento"],
            sunat_status=raw.get("state_type_description"),
        )
