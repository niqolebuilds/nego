"""Indonesian display format: Rp 9.905, Rp 5,36 M, 10,6%."""

from __future__ import annotations

import pytest

from negotiation_mcp.formatting import money, pct, unit_money
from negotiation_mcp.numfmt import compact, num, rp, rp_compact


@pytest.mark.parametrize(
    "value, expected",
    [
        (9_905.39, "Rp 9.905"),
        (107_157_799, "Rp 107.157.799"),
        (7.654, "Rp 7,65"),
        (50, "Rp 50"),
        (-1_234.4, "-Rp 1.234"),
        (None, "—"),
    ],
)
def test_rp(value, expected):
    assert rp(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [(5.36e9, "Rp 5,36 M"), (539.6e6, "Rp 539,6 jt"), (2.1e12, "Rp 2,10 T"), (12_700, "Rp 12,7 rb"), (950, "Rp 950")],
)
def test_rp_compact(value, expected):
    assert rp_compact(value) == expected


def test_grouping_and_percent():
    assert num(1_234_567.891, 2) == "1.234.567,89"
    assert compact(52_750) == "52,8 rb"
    assert pct(0.106, 1) == "10,6%"


def test_other_currencies_keep_international_style():
    assert rp(1_234.56, "USD") == "USD 1,234.56"
    assert money(1_234.56, "USD") == "USD 1,235"


def test_markdown_helpers_use_rupiah():
    assert money(107_157_799.4) == "Rp 107.157.799"
    assert unit_money(9_905.39) == "Rp 9.905"
    assert pct(0.2924) == "29,24%"
