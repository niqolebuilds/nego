"""Build a SQLite warehouse from the price book, for Metabase, Superset or any SQL tool.

The ``mart_*`` tables are computed by the same functions the MCP tools call and
written as plain tables. A dashboard therefore shows exactly the numbers Claude
quotes, and nobody has to reimplement the pack-size or inflation logic in SQL.

Tables
------
price_history              every row, plus clinical units, per-unit and index-adjusted prices
sku_master, price_index, rebate_programs
mart_savings_opportunities every SKU ranked by spend above the best available price
mart_site_prices           price per SKU per site, premium over the best site
mart_vendor_prices         price per SKU per vendor, premium over the best in the group
mart_price_trend_monthly   monthly volume-weighted price per SKU, vendor and site
mart_vendor_spend          spend, share of wallet and growth per vendor
mart_alerts                price creep, overpaying sites, quotes, renewals, rebates
meta                       when it was built, from which data, and whether it is sample data

Views
-----
v_monthly_spend            purchase-order spend by month, vendor and site
v_spend_by_group           purchase-order spend by equivalence group, last 12 months
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import engine as E
from . import intelligence as I
from .pricebook import PriceBook, add_months, month_key


def _cell(v: Any) -> Any:
    if isinstance(v, (list, tuple, set)):
        return ", ".join(str(x) for x in v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return int(v)
    return v


def _write(con: sqlite3.Connection, table: str, rows: Iterable[dict], columns: list[str] | None = None) -> int:
    rows = list(rows)
    if not rows and not columns:
        return 0
    cols = columns or list(rows[0].keys())
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    con.execute(f'CREATE TABLE "{table}" ({", ".join(f"{c!r}" for c in cols)})'.replace("'", '"'))
    con.executemany(
        f'INSERT INTO "{table}" VALUES ({", ".join("?" for _ in cols)})',
        [tuple(_cell(r.get(c)) for c in cols) for r in rows],
    )
    return len(rows)


def price_history_rows(book: PriceBook, as_of: date) -> list[dict]:
    out = []
    for o in book.observations:
        adjusted, was_adjusted = book.adjusted_unit_price(o, as_of)
        info = book.skus[o.sku]
        out.append(
            {
                "date": o.date,
                "month": month_key(o.date),
                "hospital": o.hospital,
                "vendor": o.vendor,
                "sku": o.sku,
                "sku_name": o.sku_name,
                "equivalence_group": info.equivalence_group,
                "quoted_unit": o.uom.quoted_unit,
                "clinical_units_per_quoted_unit": o.uom.clinical_units_per_quoted_unit,
                "quantity": o.quantity,
                "clinical_units": o.clinical_units,
                "list_price": o.list_price,
                "discount": o.discount,
                "net_price": o.net_price,
                "net_price_per_clinical_unit": o.net_price_per_clinical_unit,
                "adjusted_price_per_clinical_unit": adjusted,
                "inflation_adjusted": was_adjusted,
                "spend": o.spend,
                "source": o.source,
                "payment_terms_days": o.payment_terms_days,
                "contract_end": o.contract_end,
                "single_source": info.single_source,
            }
        )
    return out


def site_price_rows(book: PriceBook, as_of: date) -> list[dict]:
    rows = []
    for info in book.skus.values():
        try:
            bm = I.benchmark(book, info.sku, as_of)
        except E.EngineError:
            continue
        for h in bm.by_hospital:
            rows.append({"sku": info.sku, "sku_name": info.sku_name, "equivalence_group": info.equivalence_group,
                         **h})
    return rows


def vendor_price_rows(book: PriceBook, as_of: date) -> list[dict]:
    rows = []
    for info in book.skus.values():
        try:
            bm = I.benchmark(book, info.sku, as_of)
        except E.EngineError:
            continue
        group_best = bm.group_best.price if bm.group_best else None
        for v in bm.by_vendor:
            price = v["weighted_price_12m"] or v["best_price"]
            rows.append(
                {
                    "sku": info.sku,
                    "sku_name": info.sku_name,
                    "equivalence_group": info.equivalence_group,
                    **v,
                    "group_best_price": group_best,
                    "group_best_vendor": bm.group_best.vendor if bm.group_best else None,
                    "premium_vs_group_best": (price / group_best - 1.0) if price and group_best else None,
                }
            )
    return rows


def trend_rows(book: PriceBook) -> list[dict]:
    acc: dict[tuple, list[float]] = {}
    for o in book.observations:
        if o.source != "po":
            continue
        key = (month_key(o.date), o.sku, o.vendor, o.hospital)
        a = acc.setdefault(key, [0.0, 0.0, 0.0])
        a[0] += o.net_price_per_clinical_unit * o.clinical_units
        a[1] += o.clinical_units
        a[2] += o.spend
    return [
        {
            "month": m,
            "sku": sku,
            "sku_name": book.skus[sku].sku_name,
            "equivalence_group": book.skus[sku].equivalence_group,
            "vendor": v,
            "hospital": h,
            "price_per_clinical_unit": a[0] / a[1] if a[1] else None,
            "clinical_units": a[1],
            "spend": a[2],
            "price_index": book.price_index.get(m),
        }
        for (m, sku, v, h), a in sorted(acc.items())
    ]


def build(book: PriceBook, db_path: str | Path, params: E.Parameters | None = None, as_of: date | None = None) -> dict:
    """(Re)build the warehouse. Returns row counts per table."""
    as_of = book.resolve_as_of(as_of)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    counts: dict[str, int] = {}
    try:
        counts["price_history"] = _write(con, "price_history", price_history_rows(book, as_of))
        counts["sku_master"] = _write(
            con, "sku_master",
            [{"sku": s.sku, "sku_name": s.sku_name, "equivalence_group": s.equivalence_group,
              "clinical_unit": s.clinical_unit, "single_source": s.single_source} for s in book.skus.values()],
        )
        counts["price_index"] = _write(
            con, "price_index", [{"month": m, "index": v} for m, v in sorted(book.price_index.items())],
            ["month", "index"],
        )
        counts["rebate_programs"] = _write(
            con, "rebate_programs", [p.__dict__ for p in book.rebate_programs],
            ["vendor", "sku", "period_start", "months_in_period", "tier_target_units", "tier_rate", "collected_to_date"],
        )
        counts["mart_savings_opportunities"] = _write(
            con, "mart_savings_opportunities", I.savings_opportunities(book, as_of, top_n=0)
        )
        counts["mart_site_prices"] = _write(con, "mart_site_prices", site_price_rows(book, as_of))
        counts["mart_vendor_prices"] = _write(con, "mart_vendor_prices", vendor_price_rows(book, as_of))
        counts["mart_price_trend_monthly"] = _write(con, "mart_price_trend_monthly", trend_rows(book))
        counts["mart_vendor_spend"] = _write(con, "mart_vendor_spend", I.vendor_spend(book, as_of))
        counts["mart_alerts"] = _write(
            con, "mart_alerts", I.alerts(book, as_of, params),
            ["kind", "severity", "title", "detail", "annual_impact", "sku", "vendor", "hospital"],
        )
        since_12 = add_months(as_of, -12).isoformat()
        con.executescript(
            f"""
            DROP VIEW IF EXISTS v_monthly_spend;
            CREATE VIEW v_monthly_spend AS
              SELECT month, vendor, hospital, SUM(spend) AS spend, SUM(clinical_units) AS clinical_units
              FROM price_history WHERE source = 'po' GROUP BY month, vendor, hospital;
            DROP VIEW IF EXISTS v_spend_by_group;
            CREATE VIEW v_spend_by_group AS
              SELECT equivalence_group, SUM(spend) AS spend_12m, COUNT(DISTINCT vendor) AS vendors,
                     COUNT(DISTINCT sku) AS skus
              FROM price_history WHERE source = 'po' AND date >= '{since_12}' AND date <= '{as_of.isoformat()}'
              GROUP BY equivalence_group;
            CREATE INDEX IF NOT EXISTS ix_ph_sku ON price_history(sku);
            CREATE INDEX IF NOT EXISTS ix_ph_vendor ON price_history(vendor);
            CREATE INDEX IF NOT EXISTS ix_trend_sku ON mart_price_trend_monthly(sku);
            """
        )
        counts["meta"] = _write(
            con, "meta",
            [{"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": as_of,
              "data_dir": book.source_dir, "is_sample": book.is_sample, "currency": book.currency}],
        )
        con.commit()
    finally:
        con.close()
    tmp.replace(db_path)
    return counts
