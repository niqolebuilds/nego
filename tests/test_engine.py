"""Engine tests.

The primary test recomputes the exact scenario from
Procurement_Negotiation_Engine_v0.xlsx, whose figures were independently verified,
so the MCP kernel and the workbook cannot silently diverge.
"""

from __future__ import annotations

import math

import pytest

from negotiation_mcp.engine import (
    EngineError,
    Offer,
    Parameters,
    RebateTier,
    UomConversion,
    compare_offers,
    compute_enuc,
    convert_price_increase,
    decide,
    rebate_expected_value,
    rebate_realization,
    trade_ratios,
)

PARAMS = Parameters(
    wacc=0.12,
    baseline_payment_days=30,
    contract_years=3.0,
    rebate_breakage_rate=0.10,
    rebate_collection_lag_months=6.0,
    tax_efficiency_off_invoice=0.88,
)

ONE_TO_ONE = UomConversion("test", 1.0)


def offer_a() -> Offer:
    """Workbook Offer A — vendor sells the analyser outright."""
    return Offer(
        vendor="Vendor A - sells analyser",
        sku_group="Immunoassay reagent - Test X",
        quoted_annual_quantity=60_000,
        list_price_per_quoted_unit=78_000,
        uom=ONE_TO_ONE,
        on_invoice_discount=0.10,
        payment_terms_days=45,
        wastage_rate=0.01,
        instrument_capex_paid=2_400_000_000,
        annual_service_cost=180_000_000,
        annual_logistics_cost=25_000_000,
        rebate_tiers=[
            RebateTier(50_000, 0.010, 0.96),
            RebateTier(58_000, 0.020, 0.62),
            RebateTier(66_000, 0.030, 0.18),
            RebateTier(75_000, 0.040, 0.03),
        ],
        rebate_structure="retro",
    )


def offer_b() -> Offer:
    """Workbook Offer B — vendor places the analyser free, charges more per test."""
    return Offer(
        vendor="Vendor B - places analyser free",
        sku_group="Immunoassay reagent - Test X",
        quoted_annual_quantity=60_000,
        list_price_per_quoted_unit=108_000,
        uom=ONE_TO_ONE,
        on_invoice_discount=0.05,
        payment_terms_days=30,
        wastage_rate=0.01,
        instrument_placed_free_value=2_400_000_000,
        annual_logistics_cost=60_000_000,
        sponsorship_annual_value=400_000_000,
        rebate_tiers=[
            RebateTier(50_000, 0.015, 0.95),
            RebateTier(60_000, 0.030, 0.48),
            RebateTier(70_000, 0.045, 0.12),
            RebateTier(80_000, 0.060, 0.02),
        ],
        rebate_structure="incremental",
    )


# ----------------------------------------------------------------------
# Parity with the verified workbook
# ----------------------------------------------------------------------


def test_rebate_matches_workbook():
    r = rebate_expected_value(offer_a().rebate_tiers, 70_200, PARAMS, "retro")
    assert r.gross_expected == pytest.approx(74_931_480, abs=1)
    assert r.net_expected == pytest.approx(56_076_445.95, abs=0.5)


def test_enuc_offer_a_matches_workbook():
    r = compute_enuc(offer_a(), PARAMS)
    assert r.effective_usable_units == pytest.approx(59_400)
    assert r.total_annual_cost_ledger_a == pytest.approx(5_140_152_047.20, abs=1)
    assert r.enuc_ledger_a == pytest.approx(86_534.55, abs=0.05)


def test_enuc_offer_b_matches_workbook():
    r = compute_enuc(offer_b(), PARAMS)
    assert r.enuc_ledger_a == pytest.approx(89_985.99, abs=0.05)
    assert r.enuc_ledger_b == pytest.approx(83_251.99, abs=0.05)


def test_free_placement_is_a_benefit_not_a_cost():
    """B's 'free' analyser reduces cost, yet B is still the worse deal."""
    a, b = compute_enuc(offer_a(), PARAMS), compute_enuc(offer_b(), PARAMS)
    placed = dict(b.waterfall)["Instrument placed free (amortised benefit)"]
    assert placed < 0
    assert b.enuc_ledger_a > a.enuc_ledger_a


def test_sponsorship_flips_the_ranking_and_is_flagged():
    c = compare_offers([offer_a(), offer_b()], PARAMS)
    assert c["winner"] == "Vendor A - sells analyser"
    assert c["sponsorship_changes_ranking"] is True
    assert c["award_basis"].startswith("Ledger A")


def test_trade_ratios_rebate_point_costs_more_than_a_price_point():
    t = trade_ratios(offer_a(), PARAMS)
    assert t["equivalent_payment_terms_days"] == pytest.approx(30.4166, abs=0.01)
    assert t["equivalent_off_invoice_rebate_points"] > 0.01


# ----------------------------------------------------------------------
# Unit of measure — the denominator problem
# ----------------------------------------------------------------------


def test_pack_size_does_not_change_enuc():
    """Quoting in boxes of 50 instead of singles must give the same ENUC."""
    singles = offer_a()
    boxes = offer_a()
    boxes.quoted_annual_quantity = 1_200
    boxes.list_price_per_quoted_unit = 78_000 * 50
    boxes.uom = UomConversion("box-of-50", 50.0)
    assert compute_enuc(boxes, PARAMS).enuc_ledger_a == pytest.approx(
        compute_enuc(singles, PARAMS).enuc_ledger_a, rel=1e-9
    )


def test_bonus_stock_is_a_denominator_effect():
    base = compute_enuc(offer_a(), PARAMS)
    with_bonus = offer_a()
    with_bonus.free_goods_ratio = 0.10
    assert compute_enuc(with_bonus, PARAMS).enuc_ledger_a < base.enuc_ledger_a


def test_unusable_bonus_stock_is_worth_nothing():
    """Bonus units that all expire must not improve ENUC."""
    a = offer_a()
    a.free_goods_ratio = 0.10
    a.wastage_rate = 0.01
    b = offer_a()
    b.free_goods_ratio = 0.10
    b.wastage_rate = 1.0 - (1.0 - 0.01) / 1.10  # wastage exactly consumes the bonus
    assert compute_enuc(b, PARAMS).enuc_ledger_a > compute_enuc(a, PARAMS).enuc_ledger_a


# ----------------------------------------------------------------------
# Guard rails
# ----------------------------------------------------------------------


def test_missing_breakage_rate_is_refused_by_name():
    params = Parameters(rebate_breakage_rate=None)
    with pytest.raises(EngineError, match="D-18"):
        rebate_expected_value(offer_a().rebate_tiers, 70_200, params)


def test_missing_reservation_is_refused_by_name():
    with pytest.raises(EngineError, match="D-21"):
        decide(86_000, 76_000, None, 81_000, 0, 59_400)


def test_target_above_reservation_is_refused():
    with pytest.raises(EngineError, match="above the reservation"):
        decide(86_000, 90_000, 88_000, None, 0, 59_400)


def test_mixed_sku_groups_refused():
    other = offer_b()
    other.sku_group = "Something else"
    with pytest.raises(EngineError, match="same SKU group"):
        compare_offers([offer_a(), other], PARAMS)


def test_increasing_tier_probabilities_refused():
    tiers = [RebateTier(50_000, 0.01, 0.5), RebateTier(60_000, 0.02, 0.9)]
    with pytest.raises(EngineError, match="non-increasing"):
        rebate_expected_value(tiers, 70_200, PARAMS)


def test_out_of_order_thresholds_refused():
    tiers = [RebateTier(60_000, 0.01, 0.9), RebateTier(50_000, 0.02, 0.5)]
    with pytest.raises(EngineError, match="ascending"):
        rebate_expected_value(tiers, 70_200, PARAMS)


# ----------------------------------------------------------------------
# Verdict
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "current,expected",
    [
        (95_000, "WALK"),
        (85_000, "PUSH_ALTERNATIVE_CHEAPER"),
        (78_000, "PUSH_ABOVE_TARGET"),
        (74_000, "ACCEPT"),
    ],
)
def test_verdict_bands(current, expected):
    v = decide(current, 76_000, 88_000, 81_000, 0, 59_400)
    assert v.verdict == expected


def test_switching_cost_raises_the_bar_the_incumbent_must_beat():
    v = decide(82_000, 76_000, 88_000, 81_000, 2_000, 59_400)
    assert v.batna_enuc_including_switching == 83_000
    assert v.verdict == "PUSH_ABOVE_TARGET"  # 82,000 now beats the adjusted BATNA


def test_single_source_warns_that_walk_is_an_escalation():
    v = decide(95_000, 76_000, 88_000, None, 0, 59_400, single_source=True)
    assert v.verdict == "WALK"
    assert any("single-source" in r for r in v.rationale)


def test_absent_batna_is_called_out():
    v = decide(85_000, 76_000, 88_000, None, 0, 59_400)
    assert any("BATNA you cannot name" in r for r in v.rationale)


# ----------------------------------------------------------------------
# Seasonal price increase
# ----------------------------------------------------------------------


def test_net_binding_holds_the_net_price():
    r = convert_price_increase("net", 100_000, 110_000, 0.20, 60_000)
    assert r.new_net_price == pytest.approx(80_000)
    assert r.required_or_resulting_discount == pytest.approx(1 - 80_000 / 110_000)
    assert r.annual_cost_impact == pytest.approx(0, abs=1e-6)


def test_discount_binding_passes_the_increase_through():
    r = convert_price_increase("discount", 100_000, 110_000, 0.20, 60_000)
    assert r.new_net_price == pytest.approx(88_000)
    assert r.net_price_change_pct == pytest.approx(0.10)
    assert r.annual_cost_impact == pytest.approx(8_000 * 60_000)


def test_net_binding_refuses_a_list_price_below_the_held_net():
    """List 100k at 20% off holds net at 80k; a new list of 50k would need a
    negative discount, which means the inputs are wrong, not the deal."""
    with pytest.raises(EngineError, match="NEGATIVE discount"):
        convert_price_increase("net", 100_000, 50_000, 0.20, 60_000)


def test_net_binding_absorbs_a_very_large_increase():
    """Even a tripling of list is absorbable under net binding — the discount
    just has to get large. This is the case the old guard wrongly rejected."""
    r = convert_price_increase("net", 100_000, 300_000, 0.20, 60_000)
    assert r.new_net_price == pytest.approx(80_000)
    assert r.required_or_resulting_discount == pytest.approx(1 - 80_000 / 300_000)
    assert 0 < r.required_or_resulting_discount < 1


def test_discount_cap_breach_is_called_out():
    r = convert_price_increase("net", 100_000, 130_000, 0.20, 60_000, max_discount_cap=0.30)
    assert "exceeds the agreed discount cap" in r.explanation


# ----------------------------------------------------------------------
# Rebate realization
# ----------------------------------------------------------------------


def test_realization_detects_a_tier_at_risk():
    r = rebate_realization(
        monthly_units=[4_000] * 9,
        tier_target_units=58_000,
        tier_rate=0.02,
        net_invoice_price_per_unit=70_200,
        breakage_rate=0.10,
    )
    assert r.status.startswith("BEHIND")
    assert r.tier_will_be_met is False
    assert "Renegotiate the tier" in r.message


def test_realization_surfaces_uncollected_cash():
    r = rebate_realization(
        monthly_units=[5_000] * 12,
        tier_target_units=58_000,
        tier_rate=0.02,
        net_invoice_price_per_unit=70_200,
        breakage_rate=0.10,
        collected_to_date=0.0,
    )
    assert r.tier_will_be_met is True
    expected = 60_000 * 0.02 * 70_200 * 0.90
    assert r.accrued_rebate == pytest.approx(expected)
    assert r.breakage_to_date == pytest.approx(expected)
    assert "chase it" in r.message


def test_realization_requires_breakage_rate():
    with pytest.raises(EngineError, match="D-18"):
        rebate_realization([5_000], 58_000, 0.02, 70_200, None)


# ----------------------------------------------------------------------
# Rebate structure
# ----------------------------------------------------------------------


def test_retro_pays_more_than_incremental_for_the_same_schedule():
    tiers = offer_a().rebate_tiers
    retro = rebate_expected_value(tiers, 70_200, PARAMS, "retro")
    incr = rebate_expected_value(tiers, 70_200, PARAMS, "incremental")
    assert retro.gross_expected > incr.gross_expected


def test_effective_rate_is_well_below_the_headline():
    r = rebate_expected_value(offer_b().rebate_tiers, 102_600, PARAMS, "incremental")
    assert r.headline_top_rate == 0.06
    assert r.effective_net_rate < 0.02
    assert r.headline_haircut > 0.5


def test_pv_factor_matches_the_lag():
    assert PARAMS.rebate_pv_factor == pytest.approx(1 / (1.12 ** 0.5), rel=1e-9)
    assert not math.isnan(PARAMS.rebate_pv_factor)
