"""
Helpers compartidos por todos los módulos de tools del co-piloto ERP.

Decisión de diseño (design.md, "Credential injection"): `TenantCredentials`
viaja por `config.configurable`, NUNCA como parámetro normal de la tool —
un parámetro normal quedaría expuesto en el JSON schema que ve el LLM
(filtración del Bearer token). El mecanismo correcto en esta versión de
langchain-core (1.4.0) es anotar un parámetro `config: RunnableConfig` con
`InjectedToolArg`; `@tool` excluye automáticamente cualquier parámetro así
anotado del schema serializado (`tool.tool_call_schema`) — verificado
explícitamente en los tests de cada módulo de tools, no asumido.

Cada tool construye su propio `FacturadorPro7Client`/adapter AL VUELO, en
cada invocación, a partir de las credenciales de ESE request — el grafo se
compila una sola vez (singleton de larga vida) pero las credenciales son
por-request, así que nunca se cachea un cliente/adapter entre invocaciones.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client

# Alias de tipo para el parámetro de config inyectado — usar en TODAS las
# tools de este paquete para que `InjectedToolArg` excluya el parámetro del
# schema visible al LLM. NUNCA agregar `creds`/`token`/`base_url` como un
# parámetro Pydantic normal de ninguna tool.
InjectedConfig = Annotated[RunnableConfig, InjectedToolArg]


class MissingCredentialsError(RuntimeError):
    """El config.configurable no trae `creds` (TenantCredentials) — error de
    wiring del grafo/orquestación, nunca debería llegar hasta una tool en
    producción (PR 6 lo garantiza), pero se valida explícitamente para que
    un test/llamado directo falle con un mensaje claro en vez de un
    AttributeError críptico."""


def get_credentials(config: RunnableConfig) -> TenantCredentials:
    """Extrae `TenantCredentials` de `config['configurable']['creds']`.

    Lanza `MissingCredentialsError` si no están — nunca asume credenciales
    default ni intenta construir un cliente sin ellas."""
    configurable: Dict[str, Any] = (config or {}).get("configurable", {}) or {}
    creds = configurable.get("creds")
    if not isinstance(creds, TenantCredentials):
        raise MissingCredentialsError(
            "config['configurable']['creds'] debe ser una instancia de "
            "TenantCredentials — no se encontró ninguna. Esta tool no puede "
            "construir un adapter sin credenciales de tenant."
        )
    return creds


def build_client(config: RunnableConfig) -> FacturadorPro7Client:
    """Construye un `FacturadorPro7Client` nuevo a partir de las credenciales
    inyectadas en este request. Nunca cachea ni reusa instancias entre
    invocaciones — cada llamada a una tool arma su propio cliente."""
    return FacturadorPro7Client(get_credentials(config))


def compute_igv_breakdown(unit_price: float, quantity: float, affectation_type_id: str) -> Dict[str, float]:
    """Calcula el desglose de IGV/total para una línea de venta.

    DECISIÓN DE DISEÑO (Phase 3, ver design.md "Open Questions" — sale-note
    `total` gap): la API bare de FacturadorPro7 NO computa `total` (ni los
    totales agregados de la nota de venta) server-side — Phase 2 follow-up
    confirmó esto vía un NOT-NULL real en `total` después de resolver los
    otros 3 gaps (`prefix`/`time_of_issue`/`exchange_rate_sale`). La
    alternativa habría sido reverse-engineer `mergeData()` para que el
    server compute, o dejar que el agente (PR 5) calcule el total — ambas
    fueron descartadas: la primera está fuera de scope (no hay fuente
    Laravel de ese cálculo en el plan), la segunda dispersaría la lógica de
    IGV entre la capa de tools y la de agentes para una operación puramente
    aritmética sin ambigüedad de negocio. SE DECIDIÓ que `crear_preliminar_venta`
    (esta capa, tools) compute el desglose ANTES de llamar al port, usando
    el mismo patrón de afectación documentado en el proyecto hermano
    (`unitValue = unitPrice / 1.18` para afectación "10" = Gravado con IGV;
    sin IGV para "20"/"30" = Exonerado/Inafecto). Esta es una asunción
    explícita y razonable (IGV Perú = 18%), no una adivinanza silenciosa —
    documentada aquí y en sales_tools.py.

    affectation_type_id: "10" = Gravado (con IGV 18%), "20" = Exonerado,
    "30" = Inafecto (sin IGV en ambos casos).
    """
    IGV_RATE = 0.18
    gross = round(unit_price * quantity, 2)
    if affectation_type_id == "10":
        unit_value = unit_price / (1 + IGV_RATE)
        taxed_amount = round(unit_value * quantity, 2)
        igv_amount = round(gross - taxed_amount, 2)
    else:
        taxed_amount = 0.0
        igv_amount = 0.0
    return {
        "total_value": gross if affectation_type_id != "10" else round(unit_value * quantity, 2),
        "total_taxed": taxed_amount if affectation_type_id == "10" else 0.0,
        "total_unaffected": gross if affectation_type_id == "30" else 0.0,
        "total_exempt": gross if affectation_type_id == "20" else 0.0,
        "total_igv": igv_amount,
        "total": gross,
    }
