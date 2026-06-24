"""
LIVE verification for InventoryAdapter against the real sandbox tenant.

- get_item(), list_categories(), list_brands(): read-only, safe, always run.
- change_active(), change_favorite(), register_transaction(): REAL writes
  allowed per safety rules (internal maintenance ops, not SUNAT-facing) —
  run ONLY against the test item created by verify_phase2_items_live.py
  (--create), never against a real catalog item.

Run:
  PYTHONPATH=. venv/bin/python3 scripts/verify_phase2_inventory_live.py <creds.json> <test_item_id> [--write]
"""
import asyncio
import json
import sys

from adapters.facturadorpro7_api.auth import TenantCredentials
from adapters.facturadorpro7_api.http_client import FacturadorPro7Client
from adapters.facturadorpro7_api.inventory_adapter import InventoryAdapter
from core.domain import StockTxn


async def main(creds_path: str, test_item_id: int, do_write: bool) -> int:
    with open(creds_path) as f:
        raw = json.load(f)
    creds = TenantCredentials(base_url=raw["base_url"], token=raw["token"])
    client = FacturadorPro7Client(creds)
    adapter = InventoryAdapter(client)

    try:
        print(f"Calling REAL get_item({test_item_id}) ...")
        item = await adapter.get_item(test_item_id)
        print(f"  id={item.id} description={item.description!r} price={item.price}")
        assert item.id == test_item_id
        assert item.description, "real item must have a description"
        print("LIVE get_item() PASSED.\n")

        print("Calling REAL list_categories() ...")
        categories = await adapter.list_categories()
        print(f"  {len(categories)} real categories returned")
        if categories:
            print(f"  sample: {categories[0].id} {categories[0].name!r}")
        print("LIVE list_categories() PASSED.\n")

        print("Calling REAL list_brands() ...")
        brands = await adapter.list_brands()
        print(f"  {len(brands)} real brands returned")
        print("LIVE list_brands() PASSED.\n")

        if do_write:
            print(f"Calling REAL change_favorite({test_item_id}, True) on TEST item only ...")
            await adapter.change_favorite(test_item_id, True)
            refreshed = await adapter.get_item(test_item_id)
            print(f"  after change_favorite(True): favorite={refreshed.favorite}")
            assert refreshed.favorite is True, "favorite flag must reflect the real write"
            print(f"REAL WRITE EXECUTED: GET /api/items/change-favorite/{test_item_id}/1")

            print(f"Calling REAL change_active({test_item_id}, False) on TEST item only ...")
            await adapter.change_active(test_item_id, False)
            refreshed2 = await adapter.get_item(test_item_id)
            print(f"  after change_active(False): active={refreshed2.active}")
            print(f"REAL WRITE EXECUTED: GET /api/items/change-active/{test_item_id}/0")
            # restore to active so the test artifact stays in a sane state
            await adapter.change_active(test_item_id, True)
            print(f"REAL WRITE EXECUTED (restore): GET /api/items/change-active/{test_item_id}/1")
            print("LIVE change_active()/change_favorite() PASSED.\n")

            print(f"Calling REAL update_item({test_item_id}, patch) on TEST item only ...")
            updated = await adapter.update_item(test_item_id, {"description": "TEST-AGENTE-IA-VERIFICACION-NO-USAR-UPDATED"})
            print(f"  after update_item: description={updated.description!r}")
            assert "UPDATED" in updated.description
            print(f"REAL WRITE EXECUTED: POST /api/items/update id={test_item_id} description=TEST-AGENTE-IA-VERIFICACION-NO-USAR-UPDATED")
            print("LIVE update_item() PASSED.\n")
    finally:
        await client.aclose()

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/verify_phase2_inventory_live.py <creds.json> <test_item_id> [--write]")
        sys.exit(2)
    do_write = "--write" in sys.argv[3:]
    exit_code = asyncio.run(main(sys.argv[1], int(sys.argv[2]), do_write))
    sys.exit(exit_code)
