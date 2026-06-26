"""
Tools de `InventoryPort` — mantenimiento profundo de catálogo y stock,
exclusivo del agente de Inventario/Producto (design.md, "ItemsPort sharing").

`registrar_movimiento_stock` es la única tool de este módulo interrupt-gated
(es un movimiento de stock real, irreversible en el sentido de negocio —
tabla de clasificación del plan/design.md). El resto son lectura o
actualizaciones de metadata de catálogo sin riesgo financiero/SUNAT.

OPEN RISK heredado de Phase 2 (design.md, Open Questions): `inventory_transaction_id`
es una FK configurada por tenant sin endpoint de listado en openapi.yaml. Esta
tool NO hardcodea ningún id — lo recibe como parámetro explícito que el agente
(PR 5) debe resolver/confirmar con el usuario, documentado en el schema.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from adapters.facturadorpro7_api.inventory_adapter import InventoryAdapter
from core.application.agents.tools._shared import InjectedConfig, build_client
from core.domain import StockTxn


class ObtenerProductoInput(BaseModel):
    id: int = Field(description="ID numérico del producto en el catálogo.")


@tool("obtener_producto", args_schema=ObtenerProductoInput)
async def obtener_producto(id: int, config: InjectedConfig) -> str:
    """Obtiene el detalle completo de un producto del catálogo por su id.
    Solo lectura, no requiere confirmación."""
    client = build_client(config)
    adapter = InventoryAdapter(client)
    try:
        item = await adapter.get_item(id)
    finally:
        await client.aclose()
    return (
        f"id={item.id} | {item.description} | precio={item.price} | "
        f"barcode={item.barcode or 'N/A'} | activo={item.active} | "
        f"favorito={item.favorite} | stock={item.stock} | "
        f"categoria_id={item.category_id} | marca_id={item.brand_id}"
    )


class ActualizarProductoInput(BaseModel):
    id: int = Field(description="ID numérico del producto a actualizar.")
    patch: Dict[str, Any] = Field(
        description=(
            "Diccionario con los campos a modificar (por ejemplo "
            "{'description': 'Nuevo nombre', 'sale_unit_price': 25.5}). "
            "Solo es necesario incluir los campos que cambian; el adapter "
            "completa el resto con el registro actual."
        )
    )


@tool("actualizar_producto", args_schema=ActualizarProductoInput)
async def actualizar_producto(id: int, patch: Dict[str, Any], config: InjectedConfig) -> str:
    """Actualiza campos de un producto existente (descripción, precio, etc.).
    No es un movimiento financiero ni un paso ante SUNAT — no requiere
    confirmación humana."""
    client = build_client(config)
    adapter = InventoryAdapter(client)
    try:
        item = await adapter.update_item(id, patch)
    finally:
        await client.aclose()
    return f"Producto actualizado: id={item.id} | {item.description} | precio={item.price}"


class ActivarODesactivarProductoInput(BaseModel):
    id: int = Field(description="ID numérico del producto.")
    active: bool = Field(description="True para activar el producto, False para desactivarlo.")


@tool("activar_o_desactivar_producto", args_schema=ActivarODesactivarProductoInput)
async def activar_o_desactivar_producto(id: int, active: bool, config: InjectedConfig) -> str:
    """Activa o desactiva un producto del catálogo (visibilidad/disponibilidad
    para venta). No requiere confirmación humana."""
    client = build_client(config)
    adapter = InventoryAdapter(client)
    try:
        await adapter.change_active(id, active)
    finally:
        await client.aclose()
    estado = "activado" if active else "desactivado"
    return f"Producto id={id} {estado} correctamente."


class MarcarFavoritoInput(BaseModel):
    id: int = Field(description="ID numérico del producto.")
    favorite: bool = Field(description="True para marcar como favorito, False para desmarcar.")


@tool("marcar_favorito", args_schema=MarcarFavoritoInput)
async def marcar_favorito(id: int, favorite: bool, config: InjectedConfig) -> str:
    """Marca o desmarca un producto como favorito. No requiere confirmación
    humana."""
    client = build_client(config)
    adapter = InventoryAdapter(client)
    try:
        await adapter.change_favorite(id, favorite)
    finally:
        await client.aclose()
    estado = "marcado como favorito" if favorite else "desmarcado como favorito"
    return f"Producto id={id} {estado} correctamente."


class ListarCategoriasInput(BaseModel):
    pass


@tool("listar_categorias", args_schema=ListarCategoriasInput)
async def listar_categorias(config: InjectedConfig) -> str:
    """Lista todas las categorías de productos disponibles en el catálogo.
    Solo lectura, no requiere confirmación."""
    client = build_client(config)
    adapter = InventoryAdapter(client)
    try:
        categories = await adapter.list_categories()
    finally:
        await client.aclose()
    if not categories:
        return "No hay categorías registradas."
    return "\n".join(f"- id={c.id} | {c.name}" for c in categories)


class ListarMarcasInput(BaseModel):
    pass


@tool("listar_marcas", args_schema=ListarMarcasInput)
async def listar_marcas(config: InjectedConfig) -> str:
    """Lista todas las marcas de productos disponibles en el catálogo. Solo
    lectura, no requiere confirmación."""
    client = build_client(config)
    adapter = InventoryAdapter(client)
    try:
        brands = await adapter.list_brands()
    finally:
        await client.aclose()
    if not brands:
        return "No hay marcas registradas."
    return "\n".join(f"- id={b.id} | {b.name}" for b in brands)


class RegistrarMovimientoStockInput(BaseModel):
    item_code: str = Field(description="Código/SKU interno del producto.")
    type: str = Field(description="Tipo de movimiento: 'input' (ingreso) o 'output' (salida).")
    warehouse_id: int = Field(description="ID del almacén donde ocurre el movimiento.")
    inventory_transaction_id: int = Field(
        description=(
            "ID del tipo de transacción de inventario configurado en este "
            "tenant (por ejemplo, un id de 'ingreso por compra' o 'salida por "
            "venta'). Esta API no expone un endpoint de listado para estos "
            "ids — debe confirmarse con el usuario o resolverse de un "
            "movimiento anterior conocido del mismo tenant, nunca asumirse "
            "fijo entre tenants distintos."
        )
    )
    quantity: float = Field(description="Cantidad del movimiento (siempre positiva; el signo lo da 'type').")


@tool("registrar_movimiento_stock", args_schema=RegistrarMovimientoStockInput)
async def registrar_movimiento_stock(
    item_code: str,
    type: str,
    warehouse_id: int,
    inventory_transaction_id: int,
    quantity: float,
    config: InjectedConfig,
) -> str:
    """Registra un movimiento de stock (ingreso o salida simple) sobre un
    producto. Es una escritura real e irreversible en el inventario del
    tenant — REQUIERE confirmación humana antes de ejecutarse."""
    decision = interrupt(
        {
            "tool_name": "registrar_movimiento_stock",
            "summary": (
                f"Registrar movimiento de stock: {type} de {quantity} unidades "
                f"de '{item_code}' en almacén {warehouse_id} "
                f"(tipo de transacción {inventory_transaction_id})."
            ),
            "tool_args": {
                "item_code": item_code,
                "type": type,
                "warehouse_id": warehouse_id,
                "inventory_transaction_id": inventory_transaction_id,
                "quantity": quantity,
            },
        }
    )
    if not (isinstance(decision, dict) and decision.get("approved")):
        return "Movimiento de stock RECHAZADO por el usuario — no se ejecutó ningún cambio."

    client = build_client(config)
    adapter = InventoryAdapter(client)
    try:
        txn = StockTxn(
            item_code=item_code,
            type=type,
            warehouse_id=warehouse_id,
            inventory_transaction_id=inventory_transaction_id,
            quantity=quantity,
        )
        movement = await adapter.register_transaction(txn)
    finally:
        await client.aclose()
    return (
        f"Movimiento registrado: id={movement.id} | {movement.type} | "
        f"{movement.quantity} unidades de '{movement.item_code}' | "
        f"stock resultante={movement.resulting_stock}"
    )


INVENTORY_TOOLS: List = [
    obtener_producto,
    actualizar_producto,
    activar_o_desactivar_producto,
    marcar_favorito,
    listar_categorias,
    listar_marcas,
    registrar_movimiento_stock,
]
