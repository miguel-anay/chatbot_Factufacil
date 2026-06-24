"""
Tools de `DispatchPort` — guías de remisión (despacho), agente de Logística.

Patrón propone→confirma (design.md, "Confirmation placement"):
  - `crear_guia_remision` (borrador) — NO interrupt-gated.
  - `enviar_guia_sunat` (paso ante SUNAT, irreversible) — interrupt-gated.

`obtener_tablas_despacho` y `listar_guias_remision` son de solo lectura.

DECISIÓN DE DISEÑO — campos `datos_del_emisor`/ubigeo (design.md, "Interfaces /
Contracts" — DispatchPort, y Open Questions): Phase 2 follow-up confirmó vía
source-code real que `establishment_fiscal_code`/`origin_location_id`/
`delivery_location_id` son REQUERIDOS y no existe ningún endpoint de lookup
de ubigeo en esta API. Esta tool los expone como parámetros explícitos del
input — el agente (PR 5) debe pedírselos al usuario o derivarlos de un
registro conocido (ej. la dirección fiscal del tenant via `GET /api/company`,
fuera de scope de esta PR), nunca asumirse fijos/hardcodeados aquí.

OPEN RISK heredado, NO resuelto en esta PR (design.md, Open Questions —
"/api/dispatches required fields"): ambos modos de transporte requieren un
objeto persona anidado no documentado (`driver` o `dispatcher`/transportista)
que esta API tampoco expone vía lookup. Se modela como un campo `extra: dict`
de escape en el input — el agente debe completarlo con los datos que tenga
disponibles; esta tool NO intenta resolverlo silenciosamente ni inventa una
estructura por defecto, documentado explícitamente como limitación conocida.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from adapters.facturadorpro7_api.dispatch_adapter import DispatchAdapter
from core.agents.tools._shared import InjectedConfig, build_client


class ObtenerTablasDespachoInput(BaseModel):
    pass


@tool("obtener_tablas_despacho", args_schema=ObtenerTablasDespachoInput)
async def obtener_tablas_despacho(config: InjectedConfig) -> str:
    """Obtiene los catálogos auxiliares para armar una guía de remisión
    (motivos de traslado, modos de transporte). Usá esta tool ANTES de
    `crear_guia_remision` para conocer los valores válidos. Solo lectura."""
    client = build_client(config)
    adapter = DispatchAdapter(client)
    try:
        tables = await adapter.get_tables()
    finally:
        await client.aclose()
    reasons = "\n".join(f"  - {r}" for r in tables.transfer_reasons) or "  (ninguno)"
    modes = "\n".join(f"  - {m}" for m in tables.transport_modes) or "  (ninguno)"
    return f"Motivos de traslado:\n{reasons}\nModos de transporte:\n{modes}"


class CrearGuiaRemisionInput(BaseModel):
    establishment_fiscal_code: str = Field(
        description="Código de establecimiento fiscal del emisor (ej. '0000'), NO un id numérico."
    )
    origin_location_id: str = Field(description="Ubigeo (código de 6 dígitos) de la dirección de origen.")
    delivery_location_id: str = Field(description="Ubigeo (código de 6 dígitos) de la dirección de entrega.")
    origin_address: str = Field(description="Dirección textual de origen.")
    delivery_address: str = Field(description="Dirección textual de entrega.")
    transfer_reason_type_id: str = Field(description="Código de motivo de traslado (de obtener_tablas_despacho).")
    transport_mode_type_id: str = Field(
        description="Código de modo de transporte: '01'=público, '02'=privado (de obtener_tablas_despacho)."
    )
    extra: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Datos adicionales requeridos por la API real y NO resueltos por "
            "esta integración (ver docstring del módulo): para "
            "transport_mode_type_id='02' (privado) se espera un objeto "
            "'driver' completo; para '01' (público) un objeto 'dispatcher' "
            "completo. Sin esto, la API puede rechazar la creación con un "
            "error 500 — limitación conocida, no oculta."
        ),
    )


@tool("crear_guia_remision", args_schema=CrearGuiaRemisionInput)
async def crear_guia_remision(
    establishment_fiscal_code: str,
    origin_location_id: str,
    delivery_location_id: str,
    origin_address: str,
    delivery_address: str,
    transfer_reason_type_id: str,
    transport_mode_type_id: str,
    extra: Dict[str, Any],
    config: InjectedConfig,
) -> str:
    """Crea el BORRADOR de una guía de remisión (despacho). NO requiere
    confirmación — es un borrador editable; el paso irreversible es
    `enviar_guia_sunat`."""
    draft: Dict[str, Any] = {
        "transfer_reason_type_id": transfer_reason_type_id,
        "transport_mode_type_id": transport_mode_type_id,
        "origin": {"address": origin_address, "location_id": origin_location_id},
        "delivery": {"address": delivery_address, "location_id": delivery_location_id},
        **extra,
    }
    client = build_client(config)
    adapter = DispatchAdapter(client)
    try:
        dispatch = await adapter.create_dispatch(
            draft,
            establishment_fiscal_code=establishment_fiscal_code,
            origin_location_id=origin_location_id,
            delivery_location_id=delivery_location_id,
        )
    finally:
        await client.aclose()
    return f"Guía de remisión creada (borrador): id={dispatch.id} | origen={dispatch.origin_address} | destino={dispatch.delivery_address}"


class EnviarGuiaSunatInput(BaseModel):
    id: int = Field(description="ID de la guía de remisión a enviar (de crear_guia_remision o listar_guias_remision).")


@tool("enviar_guia_sunat", args_schema=EnviarGuiaSunatInput)
async def enviar_guia_sunat(id: int, config: InjectedConfig) -> str:
    """Envía una guía de remisión a SUNAT. Es IRREVERSIBLE — REQUIERE
    confirmación humana antes de ejecutarse."""
    decision = interrupt(
        {
            "tool_name": "enviar_guia_sunat",
            "summary": f"Enviar la guía de remisión id={id} a SUNAT. Esta acción es IRREVERSIBLE.",
            "tool_args": {"id": id},
        }
    )
    if not (isinstance(decision, dict) and decision.get("approved")):
        return "Envío a SUNAT RECHAZADO por el usuario — la guía no fue enviada."

    client = build_client(config)
    adapter = DispatchAdapter(client)
    try:
        dispatch = await adapter.send_dispatch(id)
    finally:
        await client.aclose()
    return f"Guía enviada a SUNAT: id={dispatch.id} | estado={dispatch.state or 'pendiente'} | estado_sunat={dispatch.sunat_status or 'pendiente'}"


class ListarGuiasRemisionInput(BaseModel):
    filters: Dict[str, Any] = Field(default_factory=dict, description="Filtros opcionales de búsqueda (clave/valor).")


@tool("listar_guias_remision", args_schema=ListarGuiasRemisionInput)
async def listar_guias_remision(filters: Dict[str, Any], config: InjectedConfig) -> str:
    """Lista guías de remisión existentes, con filtros opcionales. Solo
    lectura, no requiere confirmación."""
    client = build_client(config)
    adapter = DispatchAdapter(client)
    try:
        dispatches = await adapter.list_dispatches(**filters)
    finally:
        await client.aclose()
    if not dispatches:
        return "No hay guías de remisión registradas."
    return "\n".join(
        f"- id={d.id} | estado={d.state or 'N/A'} | sunat={d.sunat_status or 'N/A'} | "
        f"origen={d.origin_address or 'N/A'} | destino={d.delivery_address or 'N/A'}"
        for d in dispatches
    )


DISPATCH_TOOLS: List = [
    obtener_tablas_despacho,
    crear_guia_remision,
    enviar_guia_sunat,
    listar_guias_remision,
]
