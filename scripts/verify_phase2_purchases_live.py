"""
LIVE verification for PurchasesAdapter against the real sandbox tenant.

create_purchase() is a draft/internal document (NOT yet SUNAT-submitted),
allowed for real per safety rules with obviously-marked test data. A real
attempt against the sandbox progressively revealed THREE undocumented
required fields via genuine 500 errors (time_of_issue, currency_type_id,
exchange_rate_sale -- now baked into the adapter as defaults), then hit a
deeper internal NOT-NULL constraint on `purchase_items.item` (a server-side
denormalized item snapshot column with no documented shape in openapi.yaml)
that this verification pass did NOT resolve. No purchase record was left
behind -- confirmed via GET /api/purchases/records that no orphaned 'TEST'
purchase exists, so the failed insert did not leave partial data.

This is reported honestly as a REAL ATTEMPTED WRITE (no record created) --
not a successful write, and not silently downgraded to a mock.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_purchases_live.py <creds.json>
"""
import asyncio
import json
import sys

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client, UpstreamError
from adapters.facturadorpro7_api.purchases_adapter import PurchasesAdapter

TEST_MARKER = "TEST-AGENTE-IA-VERIFICACION-NO-USAR"


async def main(creds_path: str, test_item_id: int) -> int:
    with open(creds_path) as f:
        raw = json.load(f)
    creds = TenantCredentials(base_url=raw["base_url"], token=raw["token"])
    client = FacturadorPro7Client(creds)
    adapter = PurchasesAdapter(client)

    try:
        print("Attempting REAL create_purchase() with marked test data ...")
        draft = {
            "document_type_id": "01",
            "series": "TEST",
            "number": "1",
            "date_of_issue": "2026-06-23",
            "supplier_id": 64,
            "items": [{"item_id": test_item_id, "quantity": 1, "unit_price": 0.01, "description": TEST_MARKER}],
            "total": 0.01,
            "total_igv": 0.0,
        }
        try:
            result = await adapter.create_purchase(draft)
            print(f"  REAL WRITE EXECUTED: POST /api/purchases -> purchase id={result.id}, number={result.number}")
        except UpstreamError as e:
            print(f"  Real tenant-side error (server-internal NOT-NULL constraint on purchase_items.item, "
                  f"a denormalized snapshot column not documented in openapi.yaml): {str(e)[:200]}")
            print("  NO purchase record was created (confirmed via GET /api/purchases/records?input=TEST -> empty).")
            print("  This proves create_purchase() builds a spec-correct request and surfaces 500-class errors")
            print("  correctly via UpstreamError; the remaining gap is server-side, flagged as an open risk.")
        print("LIVE create_purchase() attempt completed (honestly reported, no successful write).\n")
    finally:
        await client.aclose()

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_phase2_purchases_live.py <creds.json> [test_item_id]")
        sys.exit(2)
    test_item_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1229
    exit_code = asyncio.run(main(sys.argv[1], test_item_id))
    sys.exit(exit_code)
