"""Price intelligence: targets beat every reference, and every counter-offer recomputes.

The tiny book in conftest.py is small enough to check by hand:

    IVC-A (Alpha, box of 50)  Site North 10,000/unit, Site South 11,000/unit, 2024 low 9,600
    IVC-B (Beta, each)        bought at 9,800, quoted at 9,500
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from negotiation_mcp import engine as E
from negotiation_mcp import intelligence as I

PARAMS = E.Parameters(rebate_breakage_rate=0.10)


def alpha_offer(**kw) -> E.Offer:
    base = dict(
        vendor="Alpha", sku_group="Cannula 22G", quoted_annual_quantity=300,
        list_price_per_quoted_unit=550_000, uom=E.UomConversion("box of 50", 50),
    )
    base.update(kw)
    return E.Offer(**base)


# ----------------------------------------------------------------------
# Benchmark
# ----------------------------------------------------------------------


def test_benchmark_finds_best_ever_internal_best_and_group_best(tiny_book):
    b = I.benchmark(tiny_book, "IVC-A")
    assert b.best_ever.price == pytest.approx(9_600)
    assert b.best_ever.date == "2024-03-15"
    assert b.internal_best.hospital == "Site North"
    assert b.internal_best.price == pytest.approx(10_000)
    assert b.group_best.price == pytest.approx(9_500)
    assert b.group_best.vendor == "Beta"


def test_internal_price_variance_is_volume_times_premium(tiny_book):
    b = I.benchmark(tiny_book, "IVC-A")
    # Site South paid 1,000 more per unit on 5,000 units in the last 12 months.
    assert b.internal_price_variance_12m == pytest.approx(5_000_000)
    assert b.annual_volume_12m == pytest.approx(15_000)


# ----------------------------------------------------------------------
# Targets
# ----------------------------------------------------------------------


def test_target_beats_every_reference(tiny_book):
    t = I.recommend_targets(tiny_book, "IVC-A", "Alpha")
    assert all(t.target_price < r.price for r in t.references)
    assert t.lowest_reference.kind == "competitor_best"
    assert t.target_price == pytest.approx(9_500 * 0.99)


def test_opening_ask_is_below_target_and_walk_away_above(tiny_book):
    t = I.recommend_targets(tiny_book, "IVC-A", "Alpha")
    assert t.opening_ask == pytest.approx(t.target_price * 0.95)
    assert t.proposed_walk_away >= t.target_price
    assert t.proposed_walk_away == pytest.approx(155_000_000 / 15_000)


def test_walk_away_is_only_proposed(tiny_book):
    t = I.recommend_targets(tiny_book, "IVC-A", "Alpha")
    assert "PROPOSED" in t.walk_away_status and "D-21" in t.walk_away_status


def test_target_is_also_given_in_the_vendors_pack(tiny_book):
    t = I.recommend_targets(tiny_book, "IVC-A", "Alpha")
    assert t.vendor_quoted_unit == "box of 50"
    assert t.target_per_quoted_unit == pytest.approx(t.target_price * 50)


def test_annual_saving_uses_todays_price_and_group_volume(tiny_book):
    t = I.recommend_targets(tiny_book, "IVC-A", "Alpha")
    assert t.annual_saving_at_target == pytest.approx((155_000_000 / 15_000 - 9_405) * 15_000)


def test_lowest_reference_being_a_quote_is_called_out(tiny_book):
    t = I.recommend_targets(tiny_book, "IVC-A", "Alpha")
    assert t.lowest_reference.source == "quote"
    assert any("quote, not a price paid" in c for c in t.caveats)


def test_competitor_excludes_the_vendor_itself(tiny_book):
    t = I.recommend_targets(tiny_book, "IVC-B", "Beta")
    assert all(r.vendor != "Beta" for r in t.references if r.kind == "competitor_best")


def test_margins_are_bounded(tiny_book):
    with pytest.raises(E.EngineError):
        I.recommend_targets(tiny_book, "IVC-A", "Alpha", beat_margin=0.6)


# ----------------------------------------------------------------------
# Beat check
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "price, status",
    [(9_400, "BEATS_ALL"), (9_700, "BEATS_SOME"), (12_000, "BEATS_NONE")],
)
def test_beat_check_status(tiny_book, price, status):
    res = I.beat_check(tiny_book, "IVC-A", price, "Alpha")
    assert res.status == status
    assert res.price_to_beat_all == pytest.approx(9_500)


def test_equal_to_a_reference_does_not_beat_it(tiny_book):
    res = I.beat_check(tiny_book, "IVC-A", 9_500, "Alpha")
    assert res.status == "BEATS_SOME"
    tied = [c for c in res.checks if c["reference_price"] == pytest.approx(9_500)]
    assert tied and not any(c["beats"] for c in tied)


# ----------------------------------------------------------------------
# Counter-offer — every figure must recompute through compute_enuc
# ----------------------------------------------------------------------


def test_enuc_at_invoice_price_equals_price_for_a_plain_offer():
    assert I.enuc_at_invoice_price(alpha_offer(), PARAMS, 9_000) == pytest.approx(9_000)


def test_each_feasible_lever_reaches_the_target():
    offer = alpha_offer(on_invoice_discount=0.05)
    target = 9_900.0
    co = I.counter_offer(offer, target, PARAMS)
    solved = {l["lever"]: l for l in co.levers}
    assert solved["on_invoice_discount"]["feasible"]
    d = solved["on_invoice_discount"]["required"]
    assert E.compute_enuc(replace(offer, on_invoice_discount=d), PARAMS).enuc_ledger_a == pytest.approx(target, rel=1e-6)
    for lever in co.levers:
        if lever["feasible"]:
            assert lever["resulting_enuc"] <= target * (1 + 1e-6)


def test_every_package_recomputes_to_the_target():
    offer = alpha_offer(
        on_invoice_discount=0.05,
        rebate_tiers=[E.RebateTier(10_000, 0.02, 0.9), E.RebateTier(15_000, 0.04, 0.4)],
    )
    target = 9_600.0
    co = I.counter_offer(offer, target, PARAMS)
    assert co.packages[0]["name"].startswith("Convert rebate")
    for p in co.packages:
        assert p["meets_target"]
        changed = offer
        for k, v in p["changes"].items():
            changed = replace(changed, rebate_tiers=[]) if k == "rebate_tiers" else replace(changed, **{k: v})
        assert E.compute_enuc(changed, PARAMS).enuc_ledger_a == pytest.approx(target, rel=1e-6)


def test_terms_alone_cannot_close_a_large_gap():
    co = I.counter_offer(alpha_offer(), 8_000, PARAMS, max_terms_days=90)
    terms = next(l for l in co.levers if l["lever"] == "payment_terms_days")
    assert not terms["feasible"]


def test_offer_already_at_target_trades_nothing():
    co = I.counter_offer(alpha_offer(), 20_000, PARAMS)
    assert co.already_at_target and not co.packages


# ----------------------------------------------------------------------
# Portfolio: savings, alerts
# ----------------------------------------------------------------------


def test_savings_opportunity_is_spend_above_best_available(tiny_book):
    rows = I.savings_opportunities(tiny_book, top_n=0)
    assert rows[0]["sku"] == "IVC-A"
    # North 2 x 5,000 x 500 + South 2 x 2,500 x 1,500 above the 9,500 best available.
    assert rows[0]["total_opportunity"] == pytest.approx(12_500_000)
    assert rows[1]["total_opportunity"] == pytest.approx(300_000)


def test_site_paying_above_best_is_alerted(tiny_book):
    a = [x for x in I.alerts(tiny_book) if x["kind"] == "above_group_best"]
    assert len(a) == 1 and a[0]["hospital"] == "Site South"
    assert a[0]["annual_impact"] == pytest.approx(5_000_000)


def test_renewal_window(tiny_book):
    assert not [x for x in I.alerts(tiny_book, renewal_days=120) if x["kind"] == "renewal"]
    assert [x for x in I.alerts(tiny_book, renewal_days=300) if x["kind"] == "renewal"]


def test_rebate_alerts_refuse_without_breakage(sample_book):
    kinds = {a["kind"] for a in I.alerts(sample_book)}
    assert "rebate_blocked" in kinds and "rebate_at_risk" not in kinds


def test_sample_plants_are_detected(sample_book):
    alerts = I.alerts(sample_book, params=PARAMS)
    kinds = {a["kind"] for a in alerts}
    assert {"price_creep", "above_group_best", "quote_above_history", "renewal", "rebate_at_risk"} <= kinds
    assert any(a["kind"] == "price_creep" and a["sku"] == "IVC22-PRI" for a in alerts)
    assert any(a["kind"] == "above_group_best" and a["hospital"] == "Site Makassar" for a in alerts)


def test_single_source_is_flagged(sample_book):
    t = I.recommend_targets(sample_book, "DES-SUR", "Surgika")
    assert t.single_source and any("Single-source" in c for c in t.caveats)


def test_every_sample_target_beats_every_reference(sample_book):
    for info in sample_book.skus.values():
        vendors = {o.vendor for o in sample_book.observations if o.sku == info.sku}
        for v in vendors:
            t = I.recommend_targets(sample_book, info.sku, v)
            assert all(t.target_price < r.price for r in t.references), (info.sku, v)
            assert t.opening_ask < t.target_price <= t.proposed_walk_away


# ----------------------------------------------------------------------
# The brief
# ----------------------------------------------------------------------


def test_brief_without_sign_off_gives_no_verdict(tiny_book):
    br = I.negotiation_brief(tiny_book, "IVC-A", "Alpha", PARAMS, offer=alpha_offer())
    assert br["verdict"] is None
    assert "D-21" in br["verdict_note"]
    assert br["counter_offer"] is not None


def test_brief_with_sign_off_gives_a_verdict(tiny_book):
    br = I.negotiation_brief(tiny_book, "IVC-A", "Alpha", PARAMS, offer=alpha_offer(), reservation_approved_by="Heldra")
    # 11,000 per unit is above the proposed walk-away of 10,333: walk.
    assert br["verdict"].verdict == "WALK"
    assert "Heldra" in br["verdict_note"]


def test_brief_without_offer_checks_todays_price(tiny_book):
    br = I.negotiation_brief(tiny_book, "cannula", "Alpha", PARAMS)
    assert br["targets"].sku == "IVC-A"
    assert br["beat_check"].offered_price == pytest.approx(155_000_000 / 15_000)
    assert br["counter_offer"] is None and br["verdict"] is None


def test_as_of_moves_the_window(tiny_book):
    b = I.benchmark(tiny_book, "IVC-A", as_of=date(2025, 12, 31))
    assert b.as_of == "2025-12-31"
    assert b.annual_volume_12m == pytest.approx(7_500)


def test_three_price_options(tiny_book):
    t = I.recommend_targets(tiny_book, "IVC-A", "Alpha")
    opts = I.price_options(t)
    assert [o["key"] for o in opts] == ["stretch", "target", "fallback"]
    assert opts[0]["price_per_clinical_unit"] < opts[1]["price_per_clinical_unit"] < opts[2]["price_per_clinical_unit"]
    assert opts[2]["price_per_clinical_unit"] == pytest.approx(t.lowest_reference.price)
    assert opts[1]["price_per_quoted_unit"] == pytest.approx(t.target_price * 50)


def test_offer_from_history_uses_latest_price_and_group_volume(tiny_book):
    offer = I.offer_from_history(tiny_book, "IVC-A", "Alpha")
    assert offer.uom.clinical_units_per_quoted_unit == 50
    assert offer.invoice_price_per_clinical_unit in (pytest.approx(10_000), pytest.approx(11_000))
    assert offer.clinical_units_paid == pytest.approx(15_000)
    assert I.offer_from_history(tiny_book, "IVC-A", "Beta") is None


def test_renewal_calendar_window(tiny_book):
    assert I.renewal_calendar(tiny_book, min_days=30, max_days=90)["renewals"] == []
    cal = I.renewal_calendar(tiny_book, min_days=200, max_days=300)
    (r,) = cal["renewals"]
    assert r["hospital"] == "Site South" and r["days_left"] == 291
    assert r["volume_multiple"] == pytest.approx(15_000 / 30_000)
    with pytest.raises(E.EngineError):
        I.renewal_calendar(tiny_book, min_days=90, max_days=30)
