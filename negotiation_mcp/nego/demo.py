"""Load the fictional sample principals so the workflow can be tried end to end.

Creates the principals in ``data/nego_sample``, opens negotiations for three of them,
prepares each from the sample PO export, formulary and MOU tracker, and plays the
principal's part for the first one: it fills Template_Nego's RFQ like a principal
would (with a few typical mistakes) and imports it back, so the anomaly queue has
real work in it.
"""

from __future__ import annotations

import io
import random
from datetime import date, timedelta
from pathlib import Path

from ..engine import EngineError
from . import service, store, tabular, template_io

SAMPLE = Path(__file__).resolve().parent.parent.parent / "data" / "nego_sample"
PRINCIPAL_FIELDS = ("name", "distributor", "category", "binding", "mou_start", "mou_end", "contact_name", "contact_email",
                    "contact_phone")
OPEN = ("PT Medika Nusantara", "PT Farmasi Sejahtera", "PT Bio Diagnostik")


def import_principals(filename: str, content: bytes, by: str) -> dict:
    rows, mapping = tabular.read_rows(filename, content, PRINCIPAL_FIELDS, required=("name",))
    res = store.upsert_principals(rows, by)
    res["columns_used"] = mapping
    return res


def _file(name: str) -> tuple[str, bytes]:
    p = SAMPLE / name
    if not p.exists():
        raise EngineError("Sample files are missing; run scripts/generate_nego_sample.py")
    return name, p.read_bytes()


def fill_rfq_like_a_principal(xlsx: bytes, seed: int = 11) -> bytes:
    """Fill identification and RFQ columns as a principal would, mistakes included."""
    import openpyxl

    rng = random.Random(seed)
    wb = openpyxl.load_workbook(io.BytesIO(xlsx))
    ws = wb[template_io.SHEET]
    r = template_io.FIRST_ROW
    i = 0
    while ws[f"B{r}"].value:
        mq, mh, md = ws[f"G{r}"].value, ws[f"H{r}"].value, ws[f"I{r}"].value
        ws[f"E{r}"] = "Active"
        if not ws[f"C{r}"].value:
            ws[f"C{r}"] = "OneMed"
        if not ws[f"D{r}"].value:
            ws[f"D{r}"] = f"REF-{rng.randint(10000, 99999)}"
        if i % 17 == 16:
            r, i = r + 1, i + 1
            continue  # left blank: the principal didn't quote it
        qty = mq or 1
        hna = (mh or 50_000) * rng.choice([1.0, 1.0, 1.03, 1.05, 1.08])
        disc = md or 0.0
        if i == 10:
            disc = 15  # typed 15 instead of 15%
        elif i == 20 and qty > 1:
            qty = 1  # HNA per box but Qty as 1: pack-multiple
        elif i == 30:
            qty, hna = qty * 2, hna * 2.1  # new pack size
        elif i == 40:
            hna = hna * 1.4  # big increase
        elif i == 50:
            hna = hna * 0.7  # big decrease
        elif i == 60:
            ws[f"E{r}"] = "Discontinue"
        ws[f"K{r}"], ws[f"L{r}"], ws[f"M{r}"] = qty, round(hna, -2), disc
        r, i = r + 1, i + 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def seed(by: str, today: date | None = None) -> dict:
    today = today or date.today()
    res = import_principals(*_file("principals.csv"), by)
    files = {k: _file(f) for k, f in (("po", "po_export.csv"), ("formulary", "formulary.csv"), ("mou", "mou_tracker.csv"))}
    opened = []
    by_name = {p["name"]: p for p in store.list_principals()}
    for name in OPEN:
        p = by_name.get(name)
        if not p or (p["cycle"] and p["cycle"]["status"] == "open"):
            continue
        start = date.fromisoformat(p["mou_end"]) + timedelta(days=1) if p.get("mou_end") else today
        end = start.replace(year=start.year + 3) - timedelta(days=1)
        c = service.create_cycle(p["id"], {"contract_start": start.isoformat(), "contract_end": end.isoformat()}, by)
        service.prepare(c["id"], files, by)
        if name == OPEN[0]:
            service.set_step(c["id"], "rfq", by, "Sample: RFQ sent to the principal")
            _, xlsx = service.export_xlsx(c["id"])
            service.import_template(c["id"], fill_rfq_like_a_principal(xlsx), f"principal:{name}", apply=True)
            service.set_step(c["id"], "counter_offer", by, "Sample: RFQ received")
        opened.append({"principal": name, "cycle_id": c["id"]})
    return {"principals": res, "opened": opened}
