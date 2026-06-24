"""
LIVE verification for FinanceAdapter against the real sandbox tenant.

- get_daily_report(), get_general_sale_report(): read-only, safe, run freely.
- open_cash()/close_cash(): REAL writes allowed per safety rules (internal
  cash-register lifecycle, not SUNAT-facing). Opens AND closes a cash
  register in the same run so no dangling open cash is left behind.
- create_retention()/create_perception(): the spec documents an EMPTY
  `type: object` schema for both. Live discovery (real 500s, not 422s --
  validation never reached) confirmed both require a nested
  "datos_del_emisor" issuer-data structure NOT present anywhere in
  openapi.yaml, traced two levels deep into Laravel transform classes
  (RetentionTransform.php -> EstablishmentTransform.php) before being
  time-boxed. NOT a successful real write for either -- reported honestly.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_finance_live.py <creds.json>
"""
import asyncio
import json
import sys

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client, UpstreamError, ValidationError
from adapters.facturadorpro7_api.finance_adapter import FinanceAdapter

TEST_MARKER = "TEST-AGENTE-IA-VERIFICACION-NO-USAR"


async def main(creds_path: str) -> int:
    with open(creds_path) as f:
        raw = json.load(f)
    creds = TenantCredentials(base_url=raw["base_url"], token=raw["token"])
    client = FacturadorPro7Client(creds)
    adapter = FinanceAdapter(client)

    try:
        print("Calling REAL get_daily_report() ...")
        report = await adapter.get_daily_report()
        print(f"  real report top-level keys: {list(report.data.keys())}")
        assert isinstance(report.data, dict) and report.data, "real daily report must return non-empty data"
        print("LIVE get_daily_report() PASSED.\n")

        print("Calling REAL get_general_sale_report() (period/month derived automatically) ...")
        sale_report = await adapter.get_general_sale_report({"date_start": "2026-06-01", "date_end": "2026-06-23"})
        totals = sale_report.data.get("totals", {})
        print(f"  real totals: {totals}")
        assert "total" in totals, "real sale report must include a 'total' figure"
        print("LIVE get_general_sale_report() PASSED — real totals returned (e.g. total={}).\n".format(totals.get("total")))

        print(f"Calling REAL open_cash() with marked test data ({TEST_MARKER}) ...")
        # NOTE: real discovery -- 'reference_number' has a short DB column
        # (full TEST_MARKER string overflowed it: "Data too long for column
        # 'reference_number'"). Use a truncated marker that still reads as
        # an obvious test artifact.
        cash = await adapter.open_cash({"beginning_balance": 0.01, "reference_number": "TEST-AGENTE-IA"})
        print(f"  REAL WRITE EXECUTED: POST /api/cash/open -> cash_id={cash.id}, beginning_balance={cash.beginning_balance}")
        assert cash.id, "real open_cash must return a real cash_id"

        print(f"Calling REAL close_cash({cash.id}) to clean up the test cash register ...")
        closed = await adapter.close_cash(cash.id)
        print(f"  REAL WRITE EXECUTED: GET /api/cash/close/{cash.id} -> closed")
        assert closed.state is False
        print("LIVE open_cash()/close_cash() round trip PASSED (no dangling open cash left).\n")

        print("Attempting REAL create_retention() — known incomplete schema, expect failure ...")
        try:
            await adapter.create_retention({"totales": {"total": 0.01}, "description": TEST_MARKER})
            print("  UNEXPECTED SUCCESS — schema gap may have been resolved server-side; verify manually.")
        except (UpstreamError, ValidationError) as e:
            print(f"  Expected failure (undiscovered 'datos_del_emisor' nested schema, see module docstring): {str(e)[:200]}")
            print("  NOT a successful write. Open risk, not resolved in this pass.")
        print("LIVE create_retention() attempt completed (honestly reported).\n")
    finally:
        await client.aclose()

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_phase2_finance_live.py <creds.json>")
        sys.exit(2)
    exit_code = asyncio.run(main(sys.argv[1]))
    sys.exit(exit_code)
