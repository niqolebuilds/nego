"""Price book loading: UoM normalisation, validation that names the row, inflation index."""

from __future__ import annotations

from datetime import date

import pytest

from negotiation_mcp.engine import EngineError
from negotiation_mcp.pricebook import PriceBook

from conftest import ROWS, write_book


def test_prices_are_normalised_per_clinical_unit(tiny_book):
    north = [o for o in tiny_book.observations if o.hospital == "Site North" and o.sku == "IVC-A"
             and o.date == date(2025, 9, 15)][0]
    assert north.net_price_per_clinical_unit == pytest.approx(10_000)
    assert north.clinical_units == 5_000


def test_box_and_single_at_the_same_unit_price_compare_equal(tmp_path):
    rows = [
        ("2026-01-15", "A", "V", "S1", "x", "g", "box of 100", 100, 1, "", 0, 150000, "po", 30, ""),
        ("2026-01-15", "A", "V", "S2", "x", "g", "each", 1, 100, "", 0, 1500, "po", 30, ""),
    ]
    book = PriceBook.load(write_book(tmp_path, rows))
    a, b = book.observations
    assert a.net_price_per_clinical_unit == b.net_price_per_clinical_unit


def test_list_price_and_discount_derive_net(tmp_path):
    rows = [("2026-01-15", "A", "V", "S1", "x", "g", "each", 1, 10, 1000, 0.2, "", "po", 30, "")]
    book = PriceBook.load(write_book(tmp_path, rows))
    assert book.observations[0].net_price == pytest.approx(800)


def test_missing_pack_size_is_refused_by_name_and_row(tmp_path):
    rows = list(ROWS)
    rows[2] = rows[2][:7] + ("",) + rows[2][8:]
    with pytest.raises(EngineError, match=r"price_history.csv row 4.*D-13"):
        PriceBook.load(write_book(tmp_path, rows))


def test_inconsistent_net_price_is_refused(tmp_path):
    rows = [("2026-01-15", "A", "V", "S1", "x", "g", "each", 1, 10, 1000, 0.2, 900, "po", 30, "")]
    with pytest.raises(EngineError, match="inconsistent"):
        PriceBook.load(write_book(tmp_path, rows))


def test_unknown_source_is_refused(tmp_path):
    rows = [("2026-01-15", "A", "V", "S1", "x", "g", "each", 1, 10, "", 0, 900, "invoice", 30, "")]
    with pytest.raises(EngineError, match="source"):
        PriceBook.load(write_book(tmp_path, rows))


def test_missing_directory_explains_what_to_do(tmp_path):
    with pytest.raises(EngineError, match="NEGOTIATION_DATA_DIR"):
        PriceBook.load(tmp_path / "nothing-here")


def test_index_brings_old_prices_into_todays_money(tmp_path):
    book = PriceBook.load(write_book(tmp_path, index=True))
    old = [o for o in book.observations if o.date == date(2024, 3, 15)][0]
    price, adjusted = book.adjusted_unit_price(old, date(2026, 3, 15))
    assert adjusted
    assert price == pytest.approx(9_600 * 1.10)


def test_without_index_prices_stay_nominal(tiny_book):
    old = [o for o in tiny_book.observations if o.date == date(2024, 3, 15)][0]
    assert tiny_book.adjusted_unit_price(old, date(2026, 3, 15)) == (pytest.approx(9_600), False)


def test_sku_resolution_by_fragment_and_vendor(tiny_book):
    assert tiny_book.resolve_sku("IVC-A").sku == "IVC-A"
    with pytest.raises(EngineError, match="matches 2 SKUs"):
        tiny_book.resolve_sku("cannula")
    assert tiny_book.resolve_sku("cannula", vendor="beta").sku == "IVC-B"
    with pytest.raises(EngineError, match="No SKU matches"):
        tiny_book.resolve_sku("stent")


def test_sample_data_is_marked(sample_book, tiny_book):
    assert sample_book.is_sample
    assert not tiny_book.is_sample
