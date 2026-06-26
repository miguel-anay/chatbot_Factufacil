"""
Tools de `ItemsPort` — búsqueda/creación liviana de productos.
Compartido por los agentes de Compras y Ventas para el flujo inline
"crear si no existe" (design.md, "ItemsPort sharing").

Ninguna de las dos tools de este módulo está interrupt-gated: `buscar_producto`
es de solo lectura, y `crear_producto` crea un ítem de catálogo nuevo (no es
un movimiento financiero/SUNAT — el riesgo real está en los tools de venta/
compra/stock que SÍ están interrupt-gated).
"""
from __future__ import annotations

from typing import List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from adapters.facturadorpro7_api.items_adapter import ItemsAdapter
from core.application.agents.tools._shared import InjectedConfig, build_client
from core.domain import ItemDraft

# Tope de productos que `buscar_producto` vuelca al contexto del LLM por
# llamada. La búsqueda del ERP ya filtra server-side (`items_adapter.search`),
# pero una query amplia (ej. "p") puede devolver decenas de filas — y CADA fila
# es texto que entra al contexto del modelo y se paga en tokens en esta y en
# las siguientes iteraciones del loop. Capear acá ataca el costo de tokens sin
# tocar la búsqueda en sí: se muestran los primeros N y se le avisa al LLM (y al
# usuario) que hay más, sugiriendo afinar o paginar. Si más adelante se agrega
# el canal de `ui_actions` (ver docs/plan-control-ui-erp.md), las búsquedas de
# navegación dejarán de volcar filas por completo; este tope es la mejora barata
# e inmediata mientras tanto.
MAX_SEARCH_RESULTS = 8


class BuscarProductoInput(BaseModel):
    query: str = Field(description="Texto o código de barras a buscar en el catálogo de productos.")
    by_barcode: bool = Field(
        default=False,
        description="Si es True, busca coincidencia EXACTA por código de barras en vez de texto libre.",
    )
    page: int = Field(default=1, description="Número de página de resultados (paginado del lado del servidor).")


@tool("buscar_producto", args_schema=BuscarProductoInput)
async def buscar_producto(query: str, by_barcode: bool, page: int, config: InjectedConfig) -> str:
    """Busca productos del catálogo por texto libre o código de barras exacto.
    Usá esta tool ANTES de crear una venta o compra para verificar si el
    producto ya existe — solo lectura, no requiere confirmación."""
    client = build_client(config)
    adapter = ItemsAdapter(client)
    try:
        items = await adapter.search(query, by_barcode=by_barcode, page=page)
    finally:
        await client.aclose()
    if not items:
        return f"No se encontraron productos para '{query}'."
    shown = items[:MAX_SEARCH_RESULTS]
    lines = [
        f"- id={i.id} | {i.description} | precio={i.price} | barcode={i.barcode or 'N/A'} | stock={i.stock}"
        for i in shown
    ]
    extra = len(items) - len(shown)
    if extra > 0:
        lines.append(
            f"… y {extra} resultado(s) más en esta página (mostrando los primeros "
            f"{MAX_SEARCH_RESULTS}). Pedile al usuario que afine la búsqueda, o "
            f"traé la página siguiente con page={page + 1}."
        )
    return "\n".join(lines)


class CrearProductoInput(BaseModel):
    description: str = Field(description="Descripción/nombre del producto.")
    price: float = Field(description="Precio de venta unitario.")
    barcode: Optional[str] = Field(default=None, description="Código de barras, si lo tiene.")
    has_igv: bool = Field(default=True, description="Si el producto está afecto a IGV (18%).")
    category_id: Optional[int] = Field(default=None, description="ID de categoría existente, si aplica.")
    brand_id: Optional[int] = Field(default=None, description="ID de marca existente, si aplica.")
    image: Optional[str] = Field(default=None, description="URL o referencia de imagen, si aplica.")


@tool("crear_producto", args_schema=CrearProductoInput)
async def crear_producto(
    description: str,
    price: float,
    barcode: Optional[str],
    has_igv: bool,
    category_id: Optional[int],
    brand_id: Optional[int],
    image: Optional[str],
    config: InjectedConfig,
) -> str:
    """Crea un producto nuevo en el catálogo cuando `buscar_producto` no
    encontró ninguna coincidencia. Usado inline por los flujos de venta y
    compra para "crear si no existe". No es un movimiento financiero ni un
    paso ante SUNAT — no requiere confirmación humana."""
    client = build_client(config)
    adapter = ItemsAdapter(client)
    try:
        draft = ItemDraft(
            description=description,
            price=price,
            barcode=barcode,
            has_igv=has_igv,
            category_id=category_id,
            brand_id=brand_id,
            image=image,
        )
        item = await adapter.create(draft)
    finally:
        await client.aclose()
    return f"Producto creado: id={item.id} | {item.description} | precio={item.price}"


ITEMS_TOOLS: List = [buscar_producto, crear_producto]
