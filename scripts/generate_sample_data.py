#!/usr/bin/env python3
"""Generate the deterministic synthetic price book in ``data/sample/``.

Everything here is invented: hospital sites, vendors and prices. The data is shaped
to exercise every path in the engine, so it deliberately plants:

* internal price variance — one site pays well above the group's best price;
* a pack-size trap — the same product quoted per box of 10, per box of 100 and each;
* a cheaper competing vendor in most equivalence groups;
* price creep — one vendor lifting a price well above inflation in the last months;
* a recent quote above the vendor's own history;
* contracts expiring soon after the as-of date;
* one single-source SKU, where walking away is not credible;
* rebate programs, one of them behind pace.

Run:  python scripts/generate_sample_data.py [output_dir]
"""

from __future__ import annotations

import csv
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from negotiation_mcp.pricebook import (  # noqa: E402
    PRICE_HISTORY_COLUMNS,
    REBATE_PROGRAM_COLUMNS,
    SAMPLE_MARKER,
    SKU_MASTER_COLUMNS,
    add_months,
    write_templates,
)

SEED = 20260928
START = date(2023, 9, 15)
MONTHS = 36  # through August 2026
INFLATION = 0.035

HOSPITALS = {
    # site: (size multiplier, price markup vs group best negotiator)
    "Site Jakarta West": (1.6, 1.00),
    "Site Jakarta South": (1.3, 1.03),
    "Site Tangerang": (1.1, 1.05),
    "Site Surabaya": (1.2, 1.02),
    "Site Medan": (0.8, 1.07),
    "Site Makassar": (0.6, 1.13),  # planted: pays well above the group
}

# sku, name, equivalence group, vendor, quoted unit, clinical units per quoted unit,
# base net price per clinical unit (IDR), base monthly clinical units per size-1.0 site
SKUS = [
    ("SYR3-MED", "Syringe 3 mL luer lock (Medisindo)", "Syringe 3 mL", "Medisindo", "box of 100", 100, 1_450, 9_000),
    ("SYR3-PRI", "Syringe 3 mL luer lock (Prima)", "Syringe 3 mL", "Prima Alkes", "box of 50", 50, 1_380, 9_000),
    ("SYR3-GLO", "Syringe 3 mL luer lock (Global)", "Syringe 3 mL", "Global Diagnostika", "each", 1, 1_540, 9_000),
    ("IVC22-PRI", "IV cannula 22G (Prima)", "IV cannula 22G", "Prima Alkes", "box of 50", 50, 9_800, 2_200),
    ("IVC22-SEH", "IV cannula 22G (Sehat)", "IV cannula 22G", "Sehat Medika", "box of 100", 100, 9_150, 2_200),
    ("IVC22-MED", "IV cannula 22G (Medisindo)", "IV cannula 22G", "Medisindo", "each", 1, 10_300, 2_200),
    ("GLV-M-NUS", "Nitrile exam gloves M (Nusantara)", "Nitrile gloves M", "Nusantara Farma", "box of 100", 100, 780, 30_000),
    ("GLV-M-MED", "Nitrile exam gloves M (Medisindo)", "Nitrile gloves M", "Medisindo", "box of 200", 200, 735, 30_000),
    ("TNI-GLO", "Troponin I reagent (Global)", "Troponin I reagent", "Global Diagnostika", "kit of 100 tests", 100, 86_000, 900),
    ("TNI-SEH", "Troponin I reagent (Sehat)", "Troponin I reagent", "Sehat Medika", "kit of 200 tests", 200, 91_000, 900),
    ("HBA1C-GLO", "HbA1c reagent (Global)", "HbA1c reagent", "Global Diagnostika", "kit of 100 tests", 100, 41_000, 1_400),
    ("HBA1C-SEH", "HbA1c reagent (Sehat)", "HbA1c reagent", "Sehat Medika", "kit of 50 tests", 50, 38_800, 1_400),
    ("HBA1C-PRI", "HbA1c reagent (Prima)", "HbA1c reagent", "Prima Alkes", "kit of 100 tests", 100, 44_000, 1_400),
    # the pack-size trap: same molecule per vial, per box of 10 and per box of 100
    ("CEF1G-NUS", "Ceftriaxone 1 g injection (Nusantara)", "Ceftriaxone 1 g inj", "Nusantara Farma", "vial", 1, 14_500, 3_500),
    ("CEF1G-MED", "Ceftriaxone 1 g injection (Medisindo)", "Ceftriaxone 1 g inj", "Medisindo", "box of 10 vials", 10, 13_300, 3_500),
    ("CEF1G-SEH", "Ceftriaxone 1 g injection (Sehat)", "Ceftriaxone 1 g inj", "Sehat Medika", "box of 100 vials", 100, 15_600, 3_500),
    ("PCT1G-NUS", "Paracetamol 1 g infusion (Nusantara)", "Paracetamol 1 g infusion", "Nusantara Farma", "bottle", 1, 28_000, 2_600),
    ("PCT1G-PRI", "Paracetamol 1 g infusion (Prima)", "Paracetamol 1 g infusion", "Prima Alkes", "box of 12 bottles", 12, 25_600, 2_600),
    ("SUT20-SUR", "Absorbable suture 2-0 (Surgika)", "Absorbable suture 2-0", "Surgika Utama", "box of 12", 12, 62_000, 700),
    ("SUT20-MED", "Absorbable suture 2-0 (Medisindo)", "Absorbable suture 2-0", "Medisindo", "box of 36", 36, 58_500, 700),
    ("DES-SUR", "Drug-eluting coronary stent (Surgika)", "Drug-eluting stent", "Surgika Utama", "each", 1, 11_500_000, 14),
]
SINGLE_SOURCE = {"DES-SUR": True}

# The same SKU is also sold by a distributor, at a premium on the principal's price.
DISTRIBUTORS = {"TNI-GLO": ("Medisindo", 1.04), "CEF1G-NUS": ("Prima Alkes", 1.03)}

PRICE_CREEP = ("IVC22-PRI", 30, 0.09)  # sku, from month index, extra increase
QUOTE_ABOVE_HISTORY = ("HBA1C-GLO", "Site Surabaya", 1.10)


def main(out: Path) -> None:
    rng = random.Random(SEED)
    out.mkdir(parents=True, exist_ok=True)

    sku_by_code = {s[0]: s for s in SKUS}
    groups: dict[str, list[str]] = {}
    for s in SKUS:
        groups.setdefault(s[2], []).append(s[0])

    discounts = {}
    terms = {}

    def list_and_discount(site: str, vendor: str, net: float) -> tuple[float, float, float]:
        d = discounts.setdefault((site, vendor), rng.choice([0.05, 0.08, 0.10, 0.12, 0.15, 0.20]))
        list_price = round(net / (1 - d), 2)
        return list_price, d, round(list_price * (1 - d), 2)

    def unit_price(code: str, site: str, m: int, vendor_premium: float = 1.0) -> float:
        _, _, _, _, _, _, base, _ = sku_by_code[code]
        infl = (1 + INFLATION) ** (m / 12)
        creep = 1.0
        if code == PRICE_CREEP[0] and m >= PRICE_CREEP[1]:
            creep += PRICE_CREEP[2]
        noise = 1 + rng.uniform(-0.012, 0.012)
        return base * infl * creep * HOSPITALS[site][1] * vendor_premium * noise

    rows: list[dict] = []

    # Each site buys each group from one SKU, sometimes switching at month 18.
    choice: dict[tuple[str, str], list[tuple[str, str, float]]] = {}
    for site in HOSPITALS:
        for group, codes in groups.items():
            first = rng.choice(codes)
            vendor, premium = sku_by_code[first][3], 1.0
            if first in DISTRIBUTORS and rng.random() < 0.5:
                vendor, premium = DISTRIBUTORS[first]
            plan = [(first, vendor, premium)]
            if len(codes) > 1 and rng.random() < 0.3:
                second = rng.choice([c for c in codes if c != first])
                plan.append((second, sku_by_code[second][3], 1.0))
            choice[(site, group)] = plan

    def supplier(plan, m):
        return plan[0] if m < 18 or len(plan) == 1 else plan[1]

    for site, (size, _) in HOSPITALS.items():
        for group in groups:
            plan = choice[(site, group)]
            # Contracts renew on a staggered anniversary, so renewals spread across the year.
            offset = rng.randint(0, 11)
            for period_start in range(offset - 12, MONTHS, 12):
                if period_start < 0:
                    continue
                code, vendor, premium = supplier(plan, period_start)
                s = sku_by_code[code]
                cu = s[5]
                terms.setdefault((site, vendor), rng.choice([30, 45, 60]))
                lp, d, net = list_and_discount(site, vendor, unit_price(code, site, period_start, premium) * cu)
                start = add_months(START, period_start).replace(day=1)
                end = date.fromordinal(add_months(start, 12).replace(day=1).toordinal() - 1)
                rows.append(
                    dict(
                        date=start, hospital=site, vendor=vendor, sku=code, sku_name=s[1],
                        equivalence_group=group, quoted_unit=s[4], clinical_units_per_quoted_unit=cu,
                        quantity=max(1, round(s[7] * size * 12 / cu)), list_price=lp, discount=d,
                        net_price=net, source="contract", payment_terms_days=terms[(site, vendor)],
                        contract_end=end,
                    )
                )
            for m in range(MONTHS):
                code_m, vendor_m, premium_m = supplier(plan, m)
                s_m = sku_by_code[code_m]
                demand = s_m[7] * size * (1 + rng.uniform(-0.12, 0.12))
                qty = max(1, round(demand / s_m[5]))
                lp, d, net = list_and_discount(site, vendor_m, unit_price(code_m, site, m, premium_m) * s_m[5])
                rows.append(
                    dict(
                        date=add_months(START, m), hospital=site, vendor=vendor_m, sku=code_m,
                        sku_name=s_m[1], equivalence_group=group, quoted_unit=s_m[4],
                        clinical_units_per_quoted_unit=s_m[5], quantity=qty, list_price=lp,
                        discount=d, net_price=net, source="po",
                        payment_terms_days=terms.setdefault((site, vendor_m), 45), contract_end="",
                    )
                )

    # Recent quotes from competing vendors, mostly sharper than the incumbent.
    for m in range(MONTHS - 4, MONTHS):
        for _ in range(4):
            group = rng.choice(list(groups))
            code = rng.choice(groups[group])
            s = sku_by_code[code]
            site = rng.choice(list(HOSPITALS))
            unit = unit_price(code, site, m) * rng.uniform(0.93, 1.00) / HOSPITALS[site][1]
            lp, d, net = list_and_discount(site, s[3], unit * s[5])
            rows.append(
                dict(
                    date=add_months(START, m), hospital=site, vendor=s[3], sku=code, sku_name=s[1],
                    equivalence_group=group, quoted_unit=s[4], clinical_units_per_quoted_unit=s[5],
                    quantity=max(1, round(s[7] * 12 / s[5])), list_price=lp, discount=d, net_price=net,
                    source="quote", payment_terms_days=45, contract_end="",
                )
            )
    code, site, factor = QUOTE_ABOVE_HISTORY
    s = sku_by_code[code]
    unit = unit_price(code, site, MONTHS - 1) * factor
    lp, d, net = list_and_discount(site, s[3], unit * s[5])
    rows.append(
        dict(
            date=add_months(START, MONTHS - 1), hospital=site, vendor=s[3], sku=code, sku_name=s[1],
            equivalence_group=s[2], quoted_unit=s[4], clinical_units_per_quoted_unit=s[5],
            quantity=round(s[7] * 12 / s[5]), list_price=lp, discount=d, net_price=net,
            source="quote", payment_terms_days=30, contract_end="",
        )
    )

    rows.sort(key=lambda r: (r["date"], r["hospital"], r["sku"], r["source"]))
    with (out / "price_history.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PRICE_HISTORY_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()})

    with (out / "sku_master.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SKU_MASTER_COLUMNS)
        w.writeheader()
        for s in SKUS:
            unit = "test" if "reagent" in s[2] else "piece"
            w.writerow(
                dict(sku=s[0], sku_name=s[1], equivalence_group=s[2], clinical_unit=unit,
                     single_source="true" if SINGLE_SOURCE.get(s[0]) else "false")
            )

    with (out / "price_index.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["month", "index"])
        for m in range(-12, MONTHS + 12):
            d = add_months(START, m)
            w.writerow([f"{d.year:04d}-{d.month:02d}", round(100 * (1 + INFLATION) ** (m / 12), 4)])

    with (out / "rebate_programs.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REBATE_PROGRAM_COLUMNS)
        w.writeheader()
        # Targets are set against the sample's own volume: one comfortable, one behind pace.
        w.writerow(dict(vendor="Global Diagnostika", sku="TNI-GLO", period_start="2026-01-01",
                        months_in_period=12, tier_target_units=25_000, tier_rate=0.03, collected_to_date=0))
        w.writerow(dict(vendor="Nusantara Farma", sku="GLV-M-NUS", period_start="2026-01-01",
                        months_in_period=12, tier_target_units=800_000, tier_rate=0.02,
                        collected_to_date=0))

    (out / SAMPLE_MARKER).write_text(
        "This directory holds synthetic data from scripts/generate_sample_data.py.\n"
        "Every price is invented. Do not use for real negotiations.\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(rows)} price rows, {len(SKUS)} SKUs to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "sample")
    write_templates(ROOT / "data" / "templates")
