"""
Adapter DispatchPort — guías de remisión (despacho), agente de Logística.

Endpoints reales (openapi.yaml):
  GET  /api/dispatches/tables  -> get_tables()
  POST /api/dispatches         -> create_dispatch()  (borrador, NO interrupt)
  POST /api/dispatches/send    -> send_dispatch()    (interrupt — capa de tools)
  GET  /api/dispatches/records -> list_dispatches()
"""
from __future__ import annotations

from typing import Any, Dict, List

from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from core.domain import Dispatch, DispatchTables
from core.ports import DispatchPort


class DispatchAdapter(DispatchPort):
    def __init__(self, client: FacturadorPro7Client):
        self._client = client

    async def get_tables(self) -> DispatchTables:
        result = await self._client.get("/api/dispatches/tables") or {}
        transfer_reasons = result.get("transferReasonTypes", [])
        transport_modes = result.get("transportModeTypes", [])
        extra = {k: v for k, v in result.items() if k not in ("transferReasonTypes", "transportModeTypes")}
        return DispatchTables(transfer_reasons=transfer_reasons, transport_modes=transport_modes, extra=extra)

    async def create_dispatch(self, draft: Dict[str, Any]) -> Dispatch:
        # openapi.yaml only documents delivery.address/origin.address as
        # required, but a real guía de remisión needs transfer reason,
        # transport mode, items, etc. — the design's "extra: dict" escape
        # carries any additional real fields the tool layer resolved via
        # get_tables() first. We send delivery/origin plus everything else
        # the caller provided, verbatim.
        result = await self._client.post("/api/dispatches", json=draft)
        raw = result.get("data") or {}
        delivery = draft.get("delivery") or {}
        origin = draft.get("origin") or {}
        return Dispatch(
            id=raw.get("id", 0),
            origin_address=origin.get("address", ""),
            delivery_address=delivery.get("address", ""),
            state=None,
            sunat_status=None,
            extra={"external_id": raw.get("external_id"), "number": raw.get("number")},
        )

    async def send_dispatch(self, id: int) -> Dispatch:  # interrupt (tools layer)
        # /api/dispatches/send takes external_id, not the numeric id — the
        # tool layer is expected to have the Dispatch entity (with its
        # extra["external_id"]) from create_dispatch(); the port signature
        # takes `id` as the canonical identifier per design.md, so callers
        # must pass the dispatch's numeric id and we resolve external_id by
        # listing records first (no GET-by-id endpoint exists for dispatch).
        dispatches = await self.list_dispatches()
        match = next((d for d in dispatches if d.id == id), None)
        external_id = match.extra.get("external_id") if match else None
        if not external_id:
            raise ValueError(f"No se encontró el external_id de la guía con id={id}; no se puede enviar a SUNAT.")
        result = await self._client.post("/api/dispatches/send", json={"external_id": external_id})
        return Dispatch(
            id=id,
            origin_address=match.origin_address if match else "",
            delivery_address=match.delivery_address if match else "",
            state=(result or {}).get("state_type_id"),
            sunat_status=(result or {}).get("state_type_description"),
            extra={"external_id": external_id},
        )

    async def list_dispatches(self, **filters: Any) -> List[Dispatch]:
        result = await self._client.get("/api/dispatches/records", params=filters or None)
        raw_list = self._unwrap_list(result)
        return [self._to_dispatch(raw) for raw in raw_list]

    @staticmethod
    def _unwrap_list(result: Any) -> List[Dict[str, Any]]:
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                inner = data.get("data")
                if isinstance(inner, list):
                    return inner
        return []

    @staticmethod
    def _to_dispatch(raw: dict) -> Dispatch:
        # NOTE: GET /api/dispatches/records (list view, real shape verified
        # live) does NOT echo origin/delivery addresses — it's a summary row
        # (customer, state, document links) rather than the create-response
        # shape. origin_address/delivery_address are left empty here; the
        # full addresses are only available right after create_dispatch().
        origin = raw.get("origin") if isinstance(raw.get("origin"), dict) else {}
        delivery = raw.get("delivery") if isinstance(raw.get("delivery"), dict) else {}
        return Dispatch(
            id=raw.get("id", 0),
            origin_address=origin.get("address", ""),
            delivery_address=delivery.get("address", ""),
            state=raw.get("state_type_id"),
            sunat_status=raw.get("state_type_description"),
            extra={
                "external_id": raw.get("external_id"),
                "number": raw.get("number"),
                "customer_name": raw.get("customer_name"),
                "btn_send": raw.get("btn_send"),
            },
        )
