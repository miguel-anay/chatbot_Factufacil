"""
Tools de `FinancePort` — retenciones, percepciones, caja y reportes,
agente de Contabilidad/Finanzas.

Todas las tools de escritura de este módulo son interrupt-gated
(`crear_retencion`, `crear_percepcion`, `abrir_caja`, `cerrar_caja` —
clasificación de escritura del plan/design.md: movimientos financieros
reales). `reporte_del_dia`/`reporte_general_ventas` son de solo lectura.

DECISIÓN DE DISEÑO — `documentos`/`supplier_identity` en retención (design.md,
"Interfaces / Contracts" — FinancePort.create_retention, y Open Questions
"/api/retentions schema"): Phase 2 follow-up source-read confirmó que la API
requiere TANTO `datos_del_emisor` (vía `establishment_fiscal_code`) COMO
`datos_del_proveedor` (vía `supplier_identity`) — ambos ya expuestos como
parámetros explícitos del port. Un intento real adicional reveló que un
body solo con `totales` y SIN `documentos` (las facturas de compra
referenciadas por la retención) falla profundo en generación de XML —
GENUINAMENTE NO RESUELTO en Phase 2 (time-boxed). Esta tool trata
`documentos` como efectivamente requerido (no opcional) en su schema,
consistente con la instrucción de la tarea — el agente (PR 5) debe
resolverlo de un `Purchase` real antes de llamar a esta tool.

`crear_percepcion` NO requiere `establishment_fiscal_code` (corrección de
Phase 2 follow-up vía source read: `PerceptionTransform`/`PerceptionValidation`
no usan `datos_del_emisor` en absoluto) — solo `customer_identity`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from adapters.facturadorpro7_api.finance_adapter import FinanceAdapter
from core.agents.tools._shared import InjectedConfig, build_client


class PersonIdentityInput(BaseModel):
    codigo_tipo_documento_identidad: str = Field(description="Tipo de documento de identidad (ej. '6'=RUC, '1'=DNI).")
    numero_documento: str = Field(description="Número de documento de identidad.")
    apellidos_y_nombres_o_razon_social: str = Field(description="Nombre completo o razón social.")
    codigo_pais: str = Field(default="PE", description="Código de país ISO (requerido por la API, ej. 'PE').")
    ubigeo: Optional[str] = Field(default=None, description="Ubigeo, si se conoce (opcional).")
    direccion: Optional[str] = Field(default=None, description="Dirección, si se conoce (opcional).")
    correo_electronico: Optional[str] = Field(default=None, description="Email, si se conoce (opcional).")
    telefono: Optional[str] = Field(default=None, description="Teléfono, si se conoce (opcional).")


class DocumentoReferenciadoInput(BaseModel):
    """Documento de compra referenciado por la retención — REQUERIDO en la
    práctica (ver docstring del módulo), resuelto de un `Purchase` real."""
    document_type_id: str = Field(description="Tipo de documento de la compra referenciada.")
    series: str = Field(description="Serie del documento de compra referenciado.")
    number: str = Field(description="Número del documento de compra referenciado.")
    total: float = Field(description="Monto total del documento de compra referenciado.")


class CrearRetencionInput(BaseModel):
    establishment_fiscal_code: str = Field(description="Código de establecimiento fiscal del emisor (ej. '0000').")
    supplier_identity: PersonIdentityInput = Field(description="Identidad del proveedor retenido.")
    documentos: List[DocumentoReferenciadoInput] = Field(
        description=(
            "Documentos de compra referenciados por esta retención — "
            "tratado como efectivamente requerido (ver docstring del "
            "módulo); resolvé esto de un Purchase real antes de llamar a "
            "esta tool."
        )
    )
    total: float = Field(description="Monto total retenido.")


@tool("crear_retencion", args_schema=CrearRetencionInput)
async def crear_retencion(
    establishment_fiscal_code: str,
    supplier_identity: PersonIdentityInput,
    documentos: List[DocumentoReferenciadoInput],
    total: float,
    config: InjectedConfig,
) -> str:
    """Registra una retención a un proveedor. Es un movimiento financiero
    real — REQUIERE confirmación humana antes de ejecutarse."""
    decision = interrupt(
        {
            "tool_name": "crear_retencion",
            "summary": (
                f"Registrar retención al proveedor "
                f"'{supplier_identity.apellidos_y_nombres_o_razon_social}' "
                f"por un total de {total}, referenciando {len(documentos)} documento(s)."
            ),
            "tool_args": {
                "establishment_fiscal_code": establishment_fiscal_code,
                "supplier_identity": supplier_identity.model_dump(exclude_none=True),
                "documentos": [d.model_dump() for d in documentos],
                "total": total,
            },
        }
    )
    if not (isinstance(decision, dict) and decision.get("approved")):
        return "Retención RECHAZADA por el usuario — no se registró ningún movimiento."

    d: Dict[str, Any] = {
        "totales": {"total": total},
        "documentos": [doc.model_dump() for doc in documentos],
    }
    client = build_client(config)
    adapter = FinanceAdapter(client)
    try:
        retention = await adapter.create_retention(
            d,
            establishment_fiscal_code=establishment_fiscal_code,
            supplier_identity=supplier_identity.model_dump(exclude_none=True),
        )
    finally:
        await client.aclose()
    return f"Retención registrada: id={retention.id} | monto={retention.amount}"


class CrearPercepcionInput(BaseModel):
    customer_identity: PersonIdentityInput = Field(description="Identidad del cliente percibido.")
    total: float = Field(description="Monto total de la percepción.")


@tool("crear_percepcion", args_schema=CrearPercepcionInput)
async def crear_percepcion(customer_identity: PersonIdentityInput, total: float, config: InjectedConfig) -> str:
    """Registra una percepción a un cliente. Es un movimiento financiero
    real — REQUIERE confirmación humana antes de ejecutarse."""
    decision = interrupt(
        {
            "tool_name": "crear_percepcion",
            "summary": (
                f"Registrar percepción al cliente "
                f"'{customer_identity.apellidos_y_nombres_o_razon_social}' "
                f"por un total de {total}."
            ),
            "tool_args": {
                "customer_identity": customer_identity.model_dump(exclude_none=True),
                "total": total,
            },
        }
    )
    if not (isinstance(decision, dict) and decision.get("approved")):
        return "Percepción RECHAZADA por el usuario — no se registró ningún movimiento."

    d: Dict[str, Any] = {"totales": {"total": total}}
    client = build_client(config)
    adapter = FinanceAdapter(client)
    try:
        perception = await adapter.create_perception(d, customer_identity=customer_identity.model_dump(exclude_none=True))
    finally:
        await client.aclose()
    return f"Percepción registrada: id={perception.id} | monto={perception.amount}"


class AbrirCajaInput(BaseModel):
    beginning_balance: float = Field(description="Monto inicial de la caja (único campo realmente requerido por la API).")


@tool("abrir_caja", args_schema=AbrirCajaInput)
async def abrir_caja(beginning_balance: float, config: InjectedConfig) -> str:
    """Abre la caja del día con un monto inicial. Es un movimiento
    financiero real — REQUIERE confirmación humana antes de ejecutarse."""
    decision = interrupt(
        {
            "tool_name": "abrir_caja",
            "summary": f"Abrir caja con monto inicial de {beginning_balance}.",
            "tool_args": {"beginning_balance": beginning_balance},
        }
    )
    if not (isinstance(decision, dict) and decision.get("approved")):
        return "Apertura de caja RECHAZADA por el usuario."

    client = build_client(config)
    adapter = FinanceAdapter(client)
    try:
        cash = await adapter.open_cash({"beginning_balance": beginning_balance})
    finally:
        await client.aclose()
    return f"Caja abierta: id={cash.id} | monto_inicial={cash.beginning_balance}"


class CerrarCajaInput(BaseModel):
    cash_id: int = Field(description="ID de la caja a cerrar (de abrir_caja).")


@tool("cerrar_caja", args_schema=CerrarCajaInput)
async def cerrar_caja(cash_id: int, config: InjectedConfig) -> str:
    """Cierra una caja abierta. Es un movimiento financiero real —
    REQUIERE confirmación humana antes de ejecutarse."""
    decision = interrupt(
        {
            "tool_name": "cerrar_caja",
            "summary": f"Cerrar la caja id={cash_id}.",
            "tool_args": {"cash_id": cash_id},
        }
    )
    if not (isinstance(decision, dict) and decision.get("approved")):
        return "Cierre de caja RECHAZADO por el usuario."

    client = build_client(config)
    adapter = FinanceAdapter(client)
    try:
        cash = await adapter.close_cash(cash_id)
    finally:
        await client.aclose()
    return f"Caja cerrada: id={cash.id} | estado_abierta={cash.state}"


class ReporteDelDiaInput(BaseModel):
    filters: Dict[str, Any] = Field(default_factory=dict, description="Filtros opcionales (ej. fecha, almacén).")


@tool("reporte_del_dia", args_schema=ReporteDelDiaInput)
async def reporte_del_dia(filters: Dict[str, Any], config: InjectedConfig) -> str:
    """Obtiene el reporte de caja/ventas del día. Solo lectura, no requiere
    confirmación."""
    client = build_client(config)
    adapter = FinanceAdapter(client)
    try:
        report = await adapter.get_daily_report(**filters)
    finally:
        await client.aclose()
    return f"Reporte del día: {report.data}"


class ReporteGeneralVentasInput(BaseModel):
    date_start: str = Field(description="Fecha de inicio en formato ISO 'YYYY-MM-DD'.")
    date_end: str = Field(description="Fecha de fin en formato ISO 'YYYY-MM-DD'.")
    establishment_id: Optional[int] = Field(default=None, description="ID de establecimiento, si se filtra por uno.")


@tool("reporte_general_ventas", args_schema=ReporteGeneralVentasInput)
async def reporte_general_ventas(
    date_start: str, date_end: str, establishment_id: Optional[int], config: InjectedConfig
) -> str:
    """Obtiene el reporte general de ventas para un rango de fechas. Solo
    lectura, no requiere confirmación."""
    d: Dict[str, Any] = {"date_start": date_start, "date_end": date_end}
    if establishment_id is not None:
        d["establishment_id"] = establishment_id
    client = build_client(config)
    adapter = FinanceAdapter(client)
    try:
        report = await adapter.get_general_sale_report(d)
    finally:
        await client.aclose()
    return f"Reporte general de ventas ({date_start} a {date_end}): {report.data}"


FINANCE_TOOLS: List = [
    crear_retencion,
    crear_percepcion,
    abrir_caja,
    cerrar_caja,
    reporte_del_dia,
    reporte_general_ventas,
]
