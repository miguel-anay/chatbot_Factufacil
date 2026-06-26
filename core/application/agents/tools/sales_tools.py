"""
Tools de `SalesPort` — preliminar de venta → generación de CPE (irreversible
ante SUNAT), agente de Ventas.

Patrón propone→confirma (design.md, "Confirmation placement"):
  - `crear_preliminar_venta` (borrador) — NO interrupt-gated.
  - `confirmar_y_generar_cpe` (paso ante SUNAT, irreversible) — interrupt-gated.

DECISIÓN DE DISEÑO — total/IGV de la venta (design.md, Open Questions, "create_sale_note()
NOT-NULL gaps"): Phase 2 follow-up confirmó vía un NOT-NULL real contra el
sandbox que `/api/sale-note` NO computa `total` (ni los totales agregados)
server-side — es un gap genuino del lado del servidor, no del adapter. Se
decidió que ESTA CAPA (tools, no el adapter ni el agente) calcula el
desglose de IGV/total ANTES de llamar al port, vía
`core.application.agents.tools._shared.compute_igv_breakdown()` (mismo patrón
`unitValue = unitPrice / 1.18` para afectación "10", documentado en el
proyecto hermano). Razón de poner el cálculo aquí y no en el adapter: el
adapter no conoce "líneas de venta" como concepto (solo recibe un dict
`draft` ya armado) y no debería inferir tasas impositivas — esa es lógica
de tool/aplicación, no de transporte HTTP. Razón de no ponerlo en el agente
(PR 5, todavía no existe): es aritmética determinística sin ambigüedad de
negocio, no una decisión que requiera razonamiento de LLM; dejarla en el
agente dispersaría la lógica de IGV entre dos capas para una operación que
siempre se calcula igual. Asunción explícita, no adivinanza silenciosa:
IGV Perú = 18%, afectación "10" = Gravado, "20"/"30" = sin IGV.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from adapters.facturadorpro7_api.sales_adapter import SalesAdapter
from core.application.agents.tools._shared import InjectedConfig, build_client, compute_igv_breakdown


class LineaVentaInput(BaseModel):
    item_id: int = Field(description="ID del producto (de buscar_producto/crear_producto).")
    quantity: float = Field(description="Cantidad vendida de este producto.")
    unit_price: float = Field(description="Precio de venta unitario (incluye IGV si afectacion_type_id='10').")
    affectation_type_id: str = Field(
        default="10",
        description="Tipo de afectación IGV de la línea: '10'=Gravado (18% IGV), '20'=Exonerado, '30'=Inafecto.",
    )


class CrearPreliminarVentaInput(BaseModel):
    series_id: int = Field(description="ID de la serie de comprobante a usar (de catálogo del tenant).")
    customer_id: int = Field(description="ID del cliente (de buscar_cliente).")
    date_of_issue: str = Field(description="Fecha de emisión en formato ISO 'YYYY-MM-DD'.")
    items: List[LineaVentaInput] = Field(description="Líneas de la venta — al menos un ítem.")


@tool("crear_preliminar_venta", args_schema=CrearPreliminarVentaInput)
async def crear_preliminar_venta(
    series_id: int,
    customer_id: int,
    date_of_issue: str,
    items: List[LineaVentaInput],
    config: InjectedConfig,
) -> str:
    """Crea el BORRADOR de una nota de venta (preliminar, todavía no es un
    comprobante ante SUNAT). Calcula el desglose de IGV/total de cada línea
    y el total agregado antes de enviarlo, porque la API base no lo computa
    server-side. NO requiere confirmación — es un borrador editable; el paso
    irreversible es `confirmar_y_generar_cpe`."""
    line_items = []
    total_general = 0.0
    total_igv_general = 0.0
    for line in items:
        breakdown = compute_igv_breakdown(line.unit_price, line.quantity, line.affectation_type_id)
        line_items.append(
            {
                "item_id": line.item_id,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "affectation_type_id": line.affectation_type_id,
                **breakdown,
            }
        )
        total_general += breakdown["total"]
        total_igv_general += breakdown["total_igv"]

    draft: Dict[str, Any] = {
        "series_id": series_id,
        "customer_id": customer_id,
        "date_of_issue": date_of_issue,
        "items": line_items,
        "total": round(total_general, 2),
        "total_igv": round(total_igv_general, 2),
        "total_taxed": round(sum(li["total_taxed"] for li in line_items), 2),
        "total_exempt": round(sum(li["total_exempt"] for li in line_items), 2),
        "total_unaffected": round(sum(li["total_unaffected"] for li in line_items), 2),
    }

    client = build_client(config)
    adapter = SalesAdapter(client)
    try:
        sale_note = await adapter.create_sale_note(draft)
    finally:
        await client.aclose()
    return (
        f"Preliminar de venta creado: id={sale_note.id} | "
        f"cliente_id={sale_note.customer_id} | total={sale_note.total} | "
        f"numero={sale_note.number or 'pendiente'}"
    )


class ConfirmarYGenerarCpeInput(BaseModel):
    sale_note_id: int = Field(description="ID del preliminar de venta a confirmar (de crear_preliminar_venta).")


@tool("confirmar_y_generar_cpe", args_schema=ConfirmarYGenerarCpeInput)
async def confirmar_y_generar_cpe(sale_note_id: int, config: InjectedConfig) -> str:
    """Confirma un preliminar de venta y genera el Comprobante de Pago
    Electrónico (CPE) ante SUNAT. Es IRREVERSIBLE — REQUIERE confirmación
    humana antes de ejecutarse."""
    decision = interrupt(
        {
            "tool_name": "confirmar_y_generar_cpe",
            "summary": (
                f"Generar comprobante electrónico (CPE) ante SUNAT para el "
                f"preliminar de venta id={sale_note_id}. Esta acción es "
                f"IRREVERSIBLE."
            ),
            "tool_args": {"sale_note_id": sale_note_id},
        }
    )
    if not (isinstance(decision, dict) and decision.get("approved")):
        return "Generación de CPE RECHAZADA por el usuario — no se emitió ningún comprobante."

    client = build_client(config)
    adapter = SalesAdapter(client)
    try:
        cpe = await adapter.generate_cpe(sale_note_id)
    finally:
        await client.aclose()
    return (
        f"CPE generado: id={cpe.id} | serie={cpe.series} | numero={cpe.number} | "
        f"estado SUNAT={cpe.sunat_status or 'pendiente'}"
    )


SALES_TOOLS: List = [crear_preliminar_venta, confirmar_y_generar_cpe]
