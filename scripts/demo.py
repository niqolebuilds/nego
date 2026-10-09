#!/usr/bin/env python3
"""Start the app on sample data with one person per role and a vendor link, so you can click through it.

    python scripts/demo.py            # then open the addresses it prints
    python scripts/demo.py --port 9000

Everything lives in data/workspace-demo (delete the folder to start fresh). Sign-in is email only,
which is the demo mode (NEGO_AUTH=dev): never use this for real data. Sample data is fictional.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PEOPLE = (("admin@example.com", "Admin Demo", "admin"), ("nina@demo.test", "Nina Negotiator", "negotiator"),
          ("vera@demo.test", "Vera Viewer", "viewer"))
FAMILIES = {"Amlodipine 10 mg": ("OBT000361", "OBT000381", "OBT000341"), "Amlodipine 5 mg": ("OBT000380", "OBT000360", "OBT000340")}


def prepare() -> str:
    from negotiation_mcp import appdb
    from negotiation_mcp.nego import demo, master, portal, service, store

    store.init()
    appdb.init()
    if not store.list_principals():
        demo.seed("demo")
    for email, name, role in PEOPLE:
        if not appdb.user_by_email(email):
            appdb.create_user(email, name, role, "demo")
    cid = 2  # PT Farmasi Sejahtera: the principal who is asked for prices
    store.set_step(cid, "rfq", "demo")
    quoted = [i for i in store.items(cid) if i.get("mou_hna") and i.get("rfq_hna") is None][:3]
    for it in quoted:  # a few quoted increases, so Findings has something to show
        store.update_item(cid, it["id"], {"rfq_qty": it["mou_qty"], "rfq_hna": it["mou_hna"] * 1.3, "rfq_disc": it["mou_disc"] or 0}, "demo")
    for generic, codes in FAMILIES.items():  # labelled brands, so the Brands tab has something to compare
        master.bulk_update(list(codes), "demo", generic_name=generic)
    service.scan(cid)
    return portal.create_link(cid, "demo", days=30)["path"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    os.environ.setdefault("NEGO_HOME", str(ROOT / "data" / "workspace-demo"))
    os.environ["NEGO_AUTH"] = "dev"
    os.environ.setdefault("NEGO_SCHEDULER", "0")
    os.environ.setdefault("NEGO_HELP_WHATSAPP", "+6281200000000")
    vendor = prepare()
    base = f"http://127.0.0.1:{args.port}"
    print(f"""
  DEMO: sample data only. Leave this window open; press Ctrl+C to stop.

  Staff (type the email on the sign-in page, no password):
    Administrator  {base}/    admin@example.com
    Negotiator     {base}/    nina@demo.test
    Viewer         {base}/    vera@demo.test

  Vendor (no sign-in; this is the link a principal receives):
    {base}{vendor}
""")
    import uvicorn

    from negotiation_mcp.dashboard.app import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
