"""
Adapter FinancePort — retenciones, percepciones, caja y reportes
(agente de Contabilidad/Finanzas).

Endpoints reales (openapi.yaml):
  POST /api/retentions          -> create_retention()   (interrupt — capa de tools)
  POST /api/perceptions         -> create_perception()  (interrupt — capa de tools)
  POST /api/cash/open           -> open_cash()           (interrupt — capa de tools)
  GET  /api/cash/close/{cash}   -> close_cash()          (interrupt — capa de tools)
  GET  /api/report              -> get_daily_report()
  POST /api/reports/general-sale -> get_general_sale_report()

OPEN RISK (documented, not silently guessed): /api/retentions and
/api/perceptions both require a nested "datos_del_emisor" issuer-data
structure that is COMPLETELY ABSENT from openapi.yaml (the spec declares
an empty `type: object` requestBody for both). Live discovery against the
sandbox tenant (real 500 errors, not 422s — validation is never reached)
confirmed the existence of this requirement and traced it through TWO
nested transform classes server-side (RetentionTransform.php ->
EstablishmentTransform.php -> "datos_del_proveedor" still undiscovered
beyond that point) before this verification pass was time-boxed to avoid
excessive trial-and-error POSTs against a shared tenant. create_retention()
and create_perception() below pass the caller's dict straight through
(matching the port's Dict[str, Any] signature) so a future pass — or a
direct read of RetentionTransform.php/PerceptionTransform.php in the
Laravel app — can supply the full nested shape without an adapter change.
"""
from __future__ import annotations

from typing import Any, Dict

from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from core.domain import Cash, Perception, Report, Retention
from core.ports import FinancePort


class FinanceAdapter(FinancePort):
    def __init__(self, client: FacturadorPro7Client):
        self._client = client

    async def create_retention(self, d: Dict[str, Any]) -> Retention:  # interrupt (tools layer)
        # See module docstring OPEN RISK note: the real required shape
        # (datos_del_emisor / datos_del_proveedor / totales) is only
        # partially discovered. Pass-through verbatim — do NOT guess
        # additional keys here without further controller inspection.
        result = await self._client.post("/api/retentions", json=d)
        raw = (result or {}).get("data") or {}
        return Retention(id=raw.get("id", 0), amount=float(d.get("totales", {}).get("total", 0) or 0), extra=raw)

    async def create_perception(self, d: Dict[str, Any]) -> Perception:  # interrupt (tools layer)
        result = await self._client.post("/api/perceptions", json=d)
        raw = (result or {}).get("data") or {}
        return Perception(id=raw.get("id", 0), amount=float(d.get("totales", {}).get("total", 0) or 0), extra=raw)

    async def open_cash(self, d: Dict[str, Any]) -> Cash:  # interrupt (tools layer)
        # NOTE: openapi.yaml lists no `required` array for this endpoint at
        # all. Live discovery (real 422 against the sandbox) confirmed
        # beginning_balance IS required, and — contrary to the plan's
        # assumption — date_opening/time_opening are NOT actually required
        # by this tenant's validation (a bare {"beginning_balance": X}
        # succeeded with 200). Caller-provided extra fields (user_id,
        # date_opening, time_opening, reference_number) are still passed
        # through for tenants/configs where they ARE required.
        payload: Dict[str, Any] = {"beginning_balance": d.get("beginning_balance", 0), **d}
        result = await self._client.post("/api/cash/open", json=payload)
        cash_id = (result or {}).get("data", {}).get("cash_id", 0)
        return Cash(id=cash_id, state=True, beginning_balance=float(payload["beginning_balance"]))

    async def close_cash(self, cash_id: int) -> Cash:  # interrupt (tools layer)
        # NOTE: openapi.yaml documents this as GET, not POST — confirmed
        # live (a POST to this path is not even routed; GET succeeds with
        # 200 and no request body). The design.md port comment said
        # "POST /api/cash/close/{cash}" but the real spec/route is GET.
        await self._client.get(f"/api/cash/close/{cash_id}")
        return Cash(id=cash_id, state=False, beginning_balance=0.0)

    async def get_daily_report(self, **filters: Any) -> Report:
        result = await self._client.get("/api/report", params=filters or None)
        return Report(data=(result or {}).get("data", {}))

    async def get_general_sale_report(self, d: Dict[str, Any]) -> Report:
        # NOTE: openapi.yaml documents date_start/date_end/establishment_id
        # as the (optional, no `required` listed) body. Live discovery (two
        # rounds of real 422s against the sandbox) revealed this tenant's
        # actual validation ALSO requires period/month_start/month_end IN
        # ADDITION to date_start/date_end — both sets together, not either/or.
        payload: Dict[str, Any] = dict(d)
        if "period" not in payload and "date_start" in payload:
            year = int(str(payload["date_start"])[:4])
            month = int(str(payload["date_start"])[5:7])
            payload.setdefault("period", year)
            payload.setdefault("month_start", month)
            payload.setdefault("month_end", month)
        result = await self._client.post("/api/reports/general-sale", json=payload)
        return Report(data=(result or {}).get("data", {}))
