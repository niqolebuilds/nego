"""The warehouse must show exactly the numbers the MCP tools compute."""

from __future__ import annotations

import sqlite3

import pytest

from negotiation_mcp import engine as E
from negotiation_mcp import intelligence as I
from negotiation_mcp.warehouse import build


@pytest.fixture(scope="module")
def db(sample_book, tmp_path_factory):
    path = tmp_path_factory.mktemp("wh") / "warehouse.db"
    counts = build(sample_book, path, E.Parameters(rebate_breakage_rate=0.10))
    con = sqlite3.connect(path)
    yield con, counts
    con.close()


def test_every_table_is_populated(db):
    _, counts = db
    for table in ("price_history", "mart_savings_opportunities", "mart_site_prices", "mart_vendor_prices",
                  "mart_price_trend_monthly", "mart_vendor_spend", "mart_alerts", "meta"):
        assert counts[table] > 0, table


def test_savings_parity_with_the_kernel(db, sample_book):
    con, _ = db
    expected = {r["sku"]: r["total_opportunity"] for r in I.savings_opportunities(sample_book, top_n=0)}
    got = dict(con.execute("SELECT sku, total_opportunity FROM mart_savings_opportunities").fetchall())
    assert got.keys() == expected.keys()
    for sku, value in expected.items():
        assert got[sku] == pytest.approx(value)


def test_spend_parity_between_view_and_mart(db):
    con, _ = db
    mart = con.execute("SELECT SUM(spend_12m) FROM mart_vendor_spend").fetchone()[0]
    view = con.execute("SELECT SUM(spend_12m) FROM v_spend_by_group").fetchone()[0]
    assert view == pytest.approx(mart)


def test_price_history_carries_normalised_prices(db, sample_book):
    con, _ = db
    n = con.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    assert n == len(sample_book.observations)
    row = con.execute(
        "SELECT net_price, clinical_units_per_quoted_unit, net_price_per_clinical_unit FROM price_history LIMIT 1"
    ).fetchone()
    assert row[2] == pytest.approx(row[0] / row[1])


def test_meta_marks_sample_data(db):
    con, _ = db
    assert con.execute("SELECT is_sample FROM meta").fetchone()[0] == 1
