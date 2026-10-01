"""Generate fictional principal-negotiation sample files in data/nego_sample/.

Everything here is invented: principals, items, prices. The files have the messy
shapes the real exports have (different headers per system, mixed unit text, rows
older than 12 months, items listed in the formulary but never bought) plus a few
planted problems the anomaly scan should find:

* a PO line priced per piece while the unit says BOX@50 (pack-multiple);
* a PO unit that disagrees with the MOU pack size;
* MOU end dates spread so some principals are inside the 6-month alert.

    python scripts/generate_nego_sample.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "nego_sample"
SEED = 7
AS_OF = date(2026, 9, 30)

PRINCIPALS = [
    # name, distributor, category, binding, months until MOU end, items
    ("PT Medika Nusantara", "PT Distribusi Medis Utama", "Consumables", "Nett", 4, 180),
    ("PT Farmasi Sejahtera", "PT Anugerah Distribusi", "Drugs", "Disc", 5, 70),
    ("PT Alkes Prima", "PT Alkes Prima", "Consumables", "Nett", 11, 45),
    ("PT Bio Diagnostik", "PT Sarana Lab", "Reagents", "Nett", 2, 30),
    ("PT Sarana Steril", "PT Distribusi Medis Utama", "Consumables", "Nett", 16, 40),
    ("PT Global Infus", "PT Anugerah Distribusi", "Consumables", "Disc", 3, 25),
]
HOSPITALS = ["Siloam Kebon Jeruk", "Siloam Lippo Village", "Siloam Surabaya", "Siloam Makassar", "Siloam Bali"]

CONSUMABLES = [
    ("IV Catheter", ["18G", "20G", "22G", "24G"], [("BOX@50", 50), ("BX 50 PCS", 50), ("Box isi 50", 50)], 9_000),
    ("Spuit", ["1cc", "3cc", "5cc", "10cc", "20cc"], [("BOX@100", 100), ("1 BOX = 100 PCS", 100)], 1_500),
    ("Sarung Tangan Nitrile", ["S", "M", "L"], [("BOX@100", 100), ("Box isi 100", 100)], 900),
    ("Kasa Steril", ["5x5", "10x10", "16x16"], [("PACK/10", 10), ("PAK 10", 10)], 6_000),
    ("Infusion Set", ["Dewasa", "Anak", "Mikro"], [("PCS", 1), ("pcs", 1)], 11_000),
    ("Masker Bedah 3 Ply", ["Hijau", "Biru"], [("BOX@50", 50)], 700),
    ("Foley Catheter", ["12Fr", "14Fr", "16Fr", "18Fr"], [("PCS", 1), ("BOX@10", 10)], 18_000),
    ("Urine Bag", ["2000ml", "1000ml"], [("PCS", 1)], 6_500),
    ("Plester Roll", ["1 inch", "2 inch"], [("ROLL", 1), ("BOX@12", 12)], 14_000),
    ("Needle Hypodermic", ["21G", "23G", "25G"], [("BOX@100", 100)], 600),
    ("Three Way Stopcock", ["Standard", "Extension 10cm"], [("PCS", 1), ("BOX@50", 50)], 4_000),
    ("Underpad", ["60x60", "60x90"], [("PACK/10", 10)], 5_500),
    ("Suction Catheter", ["10Fr", "12Fr", "14Fr"], [("PCS", 1)], 3_800),
    ("Alcohol Swab", ["Standard"], [("BOX@100", 100)], 250),
    ("ECG Electrode", ["Adult", "Pediatric"], [("PACK/50", 50)], 1_900),
    ("Blood Lancet", ["28G", "30G"], [("BOX@200", 200)], 350),
]
DRUGS = [
    ("Ceftriaxone", ["1g Inj"], [("VIAL", 1), ("BOX 10 VIAL", 10)], 9_500),
    ("Omeprazole", ["40mg Inj", "20mg Cap"], [("VIAL", 1), ("STRIP 10 CAP", 10)], 25_000),
    ("Paracetamol", ["500mg Tab", "1g/100ml Inf"], [("STRIP 10 TAB", 10), ("BTL", 1)], 450),
    ("Ondansetron", ["4mg/2ml Amp", "8mg/4ml Amp"], [("BOX 5 AMP", 5), ("AMP", 1)], 12_000),
    ("Ranitidine", ["50mg/2ml Amp"], [("BOX 10 AMP", 10)], 6_000),
    ("Ketorolac", ["30mg/ml Amp"], [("BOX 10 AMP", 10)], 7_500),
    ("Meropenem", ["1g Inj"], [("VIAL", 1)], 120_000),
    ("Amlodipine", ["5mg Tab", "10mg Tab"], [("STRIP 10 TAB", 10), ("BOX 30 TAB", 30)], 600),
    ("Metformin", ["500mg Tab", "850mg Tab"], [("STRIP 10 TAB", 10)], 300),
    ("Dexamethasone", ["5mg/ml Amp"], [("BOX 10 AMP", 10)], 3_500),
    ("Furosemide", ["20mg/2ml Amp", "40mg Tab"], [("BOX 5 AMP", 5), ("STRIP 10 TAB", 10)], 3_000),
    ("Ringer Laktat", ["500ml"], [("BTL", 1), ("CTN 20 BTL", 20)], 9_000),
    ("NaCl 0.9%", ["500ml", "100ml"], [("BTL", 1)], 8_000),
]
REAGENTS = [
    ("Glucose Reagent", ["4x50ml", "2x100ml"], [("KIT", 1)], 450_000),
    ("HbA1c Test", ["Cartridge"], [("BOX 10 TEST", 10)], 85_000),
    ("Urinalysis Strip", ["10 Param"], [("BOTTLE 100 TEST", 100)], 3_000),
    ("CRP Latex", ["100 test"], [("KIT", 1)], 650_000),
    ("Blood Tube EDTA", ["3ml", "2ml"], [("BOX@100", 100)], 1_700),
    ("Blood Tube Serum", ["5ml"], [("BOX@100", 100)], 1_900),
]
BRANDS = {"Consumables": ["Terumo", "BD", "OneMed", "B. Braun", "Medisafe", "GEA"],
          "Drugs": ["Kalbe", "Dexa", "Sanbe", "Novell", "Hexpharm"],
          "Reagents": ["Roche", "Sysmex", "Diasys", "Biosystems"]}


def catalogue(cat: str):
    return {"Consumables": CONSUMABLES, "Drugs": DRUGS, "Reagents": REAGENTS}[cat]


def make_items(rng: random.Random, name: str, cat: str, n: int, start_code: int) -> list[dict]:
    out, code = [], start_code
    base = catalogue(cat)
    variant_round = 0
    while len(out) < n:
        for prod, sizes, units, piece_price in base:
            for size in sizes:
                if len(out) >= n:
                    break
                unit_text, pack = rng.choice(units)
                brand = rng.choice(BRANDS[cat])
                suffix = f" {brand}" if variant_round == 0 else f" {brand} V{variant_round + 1}"
                hna_piece = piece_price * rng.uniform(0.7, 1.4)
                disc = rng.choice([0, 0, 0.05, 0.1, 0.15, 0.2, 0.25])
                out.append({
                    "erp_code": f"{'MED' if cat != 'Drugs' else 'OBT'}{code:06d}",
                    "item_name": f"{prod} {size}{suffix}".upper() if rng.random() < 0.3 else f"{prod} {size}{suffix}",
                    "brand": brand, "catalog_no": f"{brand.replace('. ', '')[:3].upper()}-{rng.randint(1000, 99999)}",
                    "unit_text": unit_text, "pack": pack,
                    "mou_hna": round(hna_piece * pack / 100) * 100 or 100, "mou_disc": disc,
                    "monthly_units": max(1, int(rng.lognormvariate(2.2, 1.0))),
                })
                code += 1
            if len(out) >= n:
                break
        variant_round += 1
    return out


def main() -> None:
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    principals, po, formulary, mou = [], [], [], []
    code = 100
    for name, dist, cat, binding, months_left, n in PRINCIPALS:
        end = AS_OF + timedelta(days=round(months_left * 30.44))
        start = end - timedelta(days=3 * 365)
        slug = name.split()[-1].lower()
        principals.append({"Principal": name, "Distributor": dist, "Category": cat, "Binding": binding,
                           "MOU Start": start.isoformat(), "MOU End": end.isoformat(), "PIC": f"Bapak/Ibu {slug.title()}",
                           "Email": f"tender@{slug}.example", "WhatsApp": f"+62 812 0000 {rng.randint(1000, 9999)}"})
        items = make_items(rng, name, cat, n, code)
        code += n + 50
        never_bought = set(rng.sample(range(len(items)), max(2, n // 12)))
        not_in_mou = set(rng.sample(range(len(items)), max(1, n // 20)))
        for i, it in enumerate(items):
            status = "Active" if rng.random() > 0.04 else "Inactive"
            formulary.append({"Kode Barang": it["erp_code"], "Nama Barang": it["item_name"], "Principal": name,
                              "Merk": it["brand"], "Status": status, "Satuan": it["unit_text"]})
            if i not in not_in_mou:
                mou.append({"Principal": name, "ERP Code": it["erp_code"], "Item Name": it["item_name"], "Brand": it["brand"],
                            "Catalog No. (REF)": it["catalog_no"], "MOU_Qty/PO Unit": it["pack"],
                            "MOU_HNA/PO Unit (excl. PPN)": it["mou_hna"], "MOU_Disc%": it["mou_disc"]})
            if i in never_bought:
                continue
            net_po_unit = it["mou_hna"] * (1 - it["mou_disc"])
            months = sorted(rng.sample(range(14), rng.randint(2, 7)))
            planted_piece_price = i == 3  # priced per piece although the unit is a box
            planted_unit = i == 5  # PO unit says a different pack size
            for m in months:
                d = AS_OF - timedelta(days=30 * m + rng.randint(0, 25))
                for hosp in rng.sample(HOSPITALS, rng.randint(1, 2)):
                    qty = max(1, int(it["monthly_units"] * rng.uniform(0.5, 1.5)))
                    price = net_po_unit * rng.uniform(0.98, 1.04)
                    unit_text = it["unit_text"]
                    if planted_piece_price and it["pack"] > 1:
                        price = price / it["pack"]
                    if planted_unit and it["pack"] > 1:
                        unit_text = f"BOX@{it['pack'] * 2}"
                    po.append({"PO Date": d.strftime("%d/%m/%Y"), "Hospital": hosp, "Vendor Name": name.upper() if rng.random() < 0.2 else name,
                               "Item Number": it["erp_code"], "Item Description": it["item_name"], "Purch Unit": unit_text,
                               "Quantity": qty, "Unit Price": round(price, 2), "Line Amount": round(price * qty, 2)})
    # Market benchmark (fictional INAPROC-style export) for the first principal: some rows carry the
    # catalogue number, some only a name in a different word order, prices per pack incl. PPN.
    first = PRINCIPALS[0][0]
    bench = []
    for row in mou:
        if row["Principal"] != first or rng.random() < 0.35:
            continue
        piece = row["MOU_HNA/PO Unit (excl. PPN)"] * (1 - row["MOU_Disc%"]) / row["MOU_Qty/PO Unit"] * 1.11
        pack = row["MOU_Qty/PO Unit"]
        words = row["Item Name"].split()
        name = " ".join(words[1:] + words[:1]) if rng.random() < 0.5 else row["Item Name"].upper()
        bench.append({"Nama Produk": name, "Merek": row["Brand"],
                      "No Katalog": row["Catalog No. (REF)"] if rng.random() < 0.6 else "",
                      "Satuan": f"BOX isi {pack}" if pack > 1 else "PCS",
                      "Harga": round(piece * pack * rng.uniform(0.82, 1.08), -1), "Tanggal": "2026-08-15"})
    write(OUT / "inaproc_sample.csv", bench)
    po.sort(key=lambda r: (r["PO Date"][6:] + r["PO Date"][3:5] + r["PO Date"][:2]))
    write(OUT / "principals.csv", principals)
    write(OUT / "po_export.csv", po)
    write(OUT / "formulary.csv", formulary)
    write(OUT / "mou_tracker.csv", mou)
    (OUT / "SAMPLE_DATA").write_text("Fictional data generated by scripts/generate_nego_sample.py. Not real prices.\n")
    print(f"{len(principals)} principals, {len(po)} PO lines, {len(formulary)} formulary rows, {len(mou)} MOU rows -> {OUT}")


def write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
