"""
LIVE verification for ItemsAdapter against the real sandbox tenant.

- search(): REAL call, safe (read-only) — per safety rules, freely verifiable.
- create(): REAL call ALLOWED per safety rules (drafts/internal docs, not
  SUNAT-submitted) but MUST use obviously-marked test data so the created
  record is unambiguously identifiable as a test artifact.

Run:
  PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_items_live.py <path-to-creds.json> [--create]

Without --create, only the safe search() call is exercised.
"""
import asyncio
import json
import sys

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from adapters.facturadorpro7_api.items_adapter import ItemsAdapter
from core.domain import ItemDraft

TEST_MARKER = "TEST-AGENTE-IA-VERIFICACION-NO-USAR"


async def main(creds_path: str, do_create: bool) -> int:
    with open(creds_path) as f:
        raw = json.load(f)
    creds = TenantCredentials(base_url=raw["base_url"], token=raw["token"])
    client = FacturadorPro7Client(creds)
    adapter = ItemsAdapter(client)

    try:
        print("Calling REAL search('macetero') against /api/document/search-items ...")
        results = await adapter.search("macetero")
        print(f"search() returned {len(results)} real Item(s)")
        assert isinstance(results, list), "search() must return a list"
        if results:
            sample = results[0]
            print(f"  sample: id={sample.id} description={sample.description!r} price={sample.price}")
            assert sample.id is not None, "real item must have a real id"
            assert sample.description, "real item must have a non-empty description"
        print("LIVE search() PASSED.\n")

        if do_create:
            print(f"Calling REAL create() with marked test data ({TEST_MARKER}) ...")
            draft = ItemDraft(description=TEST_MARKER, price=0.01, barcode=None)
            created = await adapter.create(draft)
            print(f"  CREATED real item id={created.id} description={created.description!r}")
            assert created.id is not None, "created item must have a real id from the API"
            assert TEST_MARKER in created.description, "created item must echo the test marker"
            print("LIVE create() PASSED — REAL WRITE EXECUTED, see report below.\n")
            print(f"REAL WRITE EXECUTED: POST /api/item -> created item id={created.id}, "
                  f"description='{TEST_MARKER}', sale_unit_price=0.01")
    finally:
        await client.aclose()

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_phase2_items_live.py <path-to-creds.json> [--create]")
        sys.exit(2)
    do_create = "--create" in sys.argv[2:]
    exit_code = asyncio.run(main(sys.argv[1], do_create))
    sys.exit(exit_code)
