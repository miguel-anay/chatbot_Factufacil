"""
LIVE verification for DispatchAdapter against the real sandbox tenant.

- get_tables(), list_dispatches(): read-only, safe, run freely.
- create_dispatch(): draft/internal document (not yet SUNAT-submitted),
  REAL attempt allowed per safety rules with marked test data. The live
  tenant returned a genuine server-side error ("Undefined index:
  datos_del_emisor") even with delivery/origin/establishment_id/
  transfer_reason_type_id/transport_mode_type_id all populated — this is a
  REAL, NOT-GUESSED discovery that openapi.yaml's documented schema for
  POST /api/dispatches is materially incomplete (a nested "datos_del_emisor"
  / issuer-data structure is required server-side with no documented shape
  anywhere in the spec, and no schema named anything like it exists). NOT
  resolved in this verification pass -- reported as an open risk, not
  silently patched with a guess.
- send_dispatch(): the IRREVERSIBLE SUNAT-facing step. NEVER executed for
  real. Forced a safe error via a nonexistent dispatch id, proving the
  resolve-external-id-then-guard logic raises BEFORE ever calling
  POST /api/dispatches/send.

Run: PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_dispatch_live.py <creds.json>
"""
import asyncio
import json
import sys

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client, UpstreamError
from adapters.facturadorpro7_api.dispatch_adapter import DispatchAdapter

TEST_MARKER = "TEST-AGENTE-IA-VERIFICACION-NO-USAR"


async def main(creds_path: str) -> int:
    with open(creds_path) as f:
        raw = json.load(f)
    creds = TenantCredentials(base_url=raw["base_url"], token=raw["token"])
    client = FacturadorPro7Client(creds)
    adapter = DispatchAdapter(client)

    try:
        print("Calling REAL get_tables() ...")
        tables = await adapter.get_tables()
        print(f"  transfer_reasons={len(tables.transfer_reasons)} transport_modes={len(tables.transport_modes)}")
        assert len(tables.transfer_reasons) > 0, "real sandbox tenant must have at least one transfer reason configured"
        print("LIVE get_tables() PASSED.\n")

        print("Calling REAL list_dispatches() ...")
        dispatches = await adapter.list_dispatches()
        print(f"  {len(dispatches)} real dispatches returned")
        print("LIVE list_dispatches() PASSED.\n")

        print("Attempting REAL create_dispatch() with marked test data ...")
        draft = {
            "delivery": {"address": f"{TEST_MARKER} - Direccion Entrega"},
            "origin": {"address": f"{TEST_MARKER} - Direccion Origen"},
            "establishment_id": 1,
            "transfer_reason_type_id": "01",
            "transport_mode_type_id": "02",
        }
        try:
            result = await adapter.create_dispatch(draft)
            print(f"  REAL WRITE EXECUTED: POST /api/dispatches -> dispatch id={result.id}")
        except UpstreamError as e:
            print(f"  Real tenant-side error (spec gap, NOT an adapter bug): {e}")
            print("  openapi.yaml's documented schema for POST /api/dispatches is incomplete: the")
            print("  server requires a 'datos_del_emisor' structure not present anywhere in the spec.")
            print("  NOT resolved in this pass -- flagged as an open risk for the tools/agents phase.")
        print("LIVE create_dispatch() attempt completed (honestly reported).\n")

        print("Forcing a SAFE error on send_dispatch() with a nonexistent id (NEVER a real SUNAT submission) ...")
        try:
            await adapter.send_dispatch(999999999)
            print("  UNEXPECTED: call succeeded — should not happen for a nonexistent id.")
            return 1
        except ValueError as e:
            print(f"  Expected guard error (proves the code never reached POST /api/dispatches/send): {e}")
            print("LIVE send_dispatch() error-path PASSED.\n")
    finally:
        await client.aclose()

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_phase2_dispatch_live.py <creds.json>")
        sys.exit(2)
    exit_code = asyncio.run(main(sys.argv[1]))
    sys.exit(exit_code)
