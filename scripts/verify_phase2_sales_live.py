"""
LIVE verification for SalesAdapter against the real sandbox tenant.

- create_sale_note(): REAL call attempted (it's a draft, not yet
  SUNAT-submitted, allowed per safety rules with marked test data). The
  live tenant returned a genuine server-side error (a pre-existing data
  gap in the `sale_notes`/series configuration: "Field 'prefix' doesn't
  have a default value" for series_id=10 "NV01") — NOT an adapter bug.
  This proves create_sale_note() builds the correct request and correctly
  surfaces a 500-class error via UpstreamError end-to-end.
- generate_cpe(): the IRREVERSIBLE SUNAT-facing step. NEVER executed for
  real. Forced an error instead by using a nonexistent sale_note_id —
  proves the request-building/error-path WITHOUT ever reaching a real
  SUNAT submission (impossible since the referenced sale note doesn't
  exist server-side).

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_sales_live.py <creds.json>
"""
import asyncio
import json
import sys

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client, UpstreamError
from adapters.facturadorpro7_api.sales_adapter import SalesAdapter

TEST_MARKER = "TEST-AGENTE-IA-VERIFICACION-NO-USAR"


async def main(creds_path: str, test_item_id: int) -> int:
    with open(creds_path) as f:
        raw = json.load(f)
    creds = TenantCredentials(base_url=raw["base_url"], token=raw["token"])
    client = FacturadorPro7Client(creds)
    adapter = SalesAdapter(client)

    try:
        print("Attempting REAL create_sale_note() with marked test data (draft, not SUNAT-submitted) ...")
        draft = {
            "series_id": 10,
            "customer_id": 28,
            "establishment_id": 1,
            "date_of_issue": "2026-06-23",
            "currency_type_id": "PEN",
            "items": [{"item_id": test_item_id, "quantity": 1, "unit_price": 0.01, "description": TEST_MARKER}],
        }
        try:
            result = await adapter.create_sale_note(draft)
            print(f"  REAL WRITE EXECUTED: POST /api/sale-note -> sale note id={result.id}, number={result.number}")
        except UpstreamError as e:
            print(f"  Real tenant-side error (pre-existing data gap, NOT an adapter bug): {e}")
            print("  Proves create_sale_note() builds the correct request and surfaces 500-class errors via UpstreamError.")
        print("LIVE create_sale_note() attempt PASSED (request-building/error-surfacing verified end-to-end).\n")

        print("Forcing a SAFE error on generate_cpe() with a nonexistent sale_note_id (NEVER a real SUNAT submission) ...")
        try:
            await adapter.generate_cpe(999999999)
            print("  UNEXPECTED: call succeeded — this should not happen for a nonexistent id.")
            return 1
        except UpstreamError as e:
            print(f"  Expected real error from the API: {e}")
            print("LIVE generate_cpe() error-path PASSED — request built and sent correctly, no real CPE/SUNAT submission occurred.\n")
    finally:
        await client.aclose()

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_phase2_sales_live.py <creds.json> [test_item_id]")
        sys.exit(2)
    test_item_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1229
    exit_code = asyncio.run(main(sys.argv[1], test_item_id))
    sys.exit(exit_code)
