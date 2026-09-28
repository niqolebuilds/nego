"""Pure calculation kernel for the Siloam procurement negotiation engine.

No MCP, no I/O, no external calls — every function here is deterministic and
unit-testable. The MCP layer in ``server.py`` is a thin wrapper over this module.

Vocabulary
----------
ENUC
    Effective Net Unit Cost. One comparable number per *usable clinical unit*,
    after price, discount, rebate, bonus stock, wastage, payment terms,
    instrument capex or placement, service and logistics.

Ledger A
    ENUC **excluding** sponsorship. The only basis for ranking and award.

Ledger B
    ENUC **including** sponsorship. Monitoring and disclosure only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

DAYS_PER_YEAR = 365
MONTHS_PER_YEAR = 12

#: Data elements from the register that have no source system yet. Tools that
#: need one of these refuse rather than silently substituting a guess.
BLOCKING_DATA_ELEMENTS = {
    "escalation_thresholds": "D-20 — escalation thresholds (owner: Andreas Tanjaya)",
    "reservation_enuc": "D-21 — target / reservation / BATNA (owner: you + Heldra)",
    "rebate_breakage_rate": "D-18 — rebate breakage, earned vs collected (owner: Finance / AP)",
    "uom_conversion": "D-13 — pack size / UoM conversion factors (owner: IT / Master Data)",
    "single_source_flag": "D-23 — single-source flag per SKU (owner: Pharmacy)",
}

BindingType = Literal["net", "discount"]
RebateStructure = Literal["retro", "incremental"]
Verdict = Literal["ACCEPT", "PUSH_ABOVE_TARGET", "PUSH_ALTERNATIVE_CHEAPER", "WALK"]


class EngineError(ValueError):
    """Raised when an input is missing or inconsistent.

    The message always names what is missing and who owns it, so the caller can
    act rather than guess.
    """


def _require_blocking(name: str, value: float | None) -> float:
    if value is None:
        raise EngineError(
            f"Missing required input '{name}'. This is a known blocking data element: "
            f"{BLOCKING_DATA_ELEMENTS.get(name, name)}. "
            f"The engine will not substitute a default, because a plausible-looking "
            f"wrong number here is worse than no number."
        )
    return value


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Parameters:
    """Scoped, effective-dated assumptions.

    In production these resolve through the hierarchy
    group -> region -> hospital -> principal -> contract -> SKU -> deal,
    most specific wins. Here they arrive flat, per call.
    """

    wacc: float = 0.12
    baseline_payment_days: int = 30
    contract_years: float = 3.0
    rebate_breakage_rate: float | None = None
    rebate_collection_lag_months: float = 6.0
    tax_efficiency_on_invoice: float = 1.0
    tax_efficiency_off_invoice: float = 0.88
    currency: str = "IDR"

    def __post_init__(self) -> None:
        if not 0 <= self.wacc < 1:
            raise EngineError(f"wacc must be between 0 and 1, got {self.wacc}")
        if self.contract_years <= 0:
            raise EngineError("contract_years must be greater than zero")
        if self.rebate_breakage_rate is not None and not 0 <= self.rebate_breakage_rate <= 1:
            raise EngineError("rebate_breakage_rate must be between 0 and 1")

    @property
    def rebate_pv_factor(self) -> float:
        """Present-value factor applied to rebate cash, for the collection lag."""
        return 1.0 / (1.0 + self.wacc) ** (self.rebate_collection_lag_months / MONTHS_PER_YEAR)


# --------------------------------------------------------------------------
# Unit of measure
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class UomConversion:
    """Converts a vendor's quoting unit into the canonical clinical unit.

    Vendors quote in boxes of 50, boxes of 100 and 'each', deliberately. Every
    ENUC denominator passes through here.
    """

    quoted_unit: str
    clinical_units_per_quoted_unit: float

    def __post_init__(self) -> None:
        if self.clinical_units_per_quoted_unit <= 0:
            raise EngineError(
                f"clinical_units_per_quoted_unit must be positive, got "
                f"{self.clinical_units_per_quoted_unit} for unit '{self.quoted_unit}'"
            )

    def to_clinical_units(self, quoted_quantity: float) -> float:
        return quoted_quantity * self.clinical_units_per_quoted_unit

    def price_per_clinical_unit(self, price_per_quoted_unit: float) -> float:
        return price_per_quoted_unit / self.clinical_units_per_quoted_unit


# --------------------------------------------------------------------------
# Rebate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RebateTier:
    threshold_units: float
    rate: float
    probability: float

    def __post_init__(self) -> None:
        if self.threshold_units < 0:
            raise EngineError("Rebate tier threshold cannot be negative")
        if not 0 <= self.rate <= 1:
            raise EngineError(f"Rebate rate must be between 0 and 1, got {self.rate}")
        if not 0 <= self.probability <= 1:
            raise EngineError(f"Tier probability must be between 0 and 1, got {self.probability}")


@dataclass(frozen=True)
class RebateResult:
    gross_expected: float
    after_breakage: float
    after_tax: float
    net_expected: float
    headline_top_rate: float
    effective_net_rate: float
    per_tier: list[dict]

    @property
    def headline_haircut(self) -> float:
        """How much smaller the effective rate is than the rate the vendor quotes."""
        if self.headline_top_rate == 0:
            return 0.0
        return 1.0 - (self.effective_net_rate / self.headline_top_rate)


def rebate_expected_value(
    tiers: Sequence[RebateTier],
    invoice_price_per_unit: float,
    params: Parameters,
    structure: RebateStructure = "retro",
) -> RebateResult:
    """Probability-weight a tiered rebate schedule down to expected cash.

    Never books the headline rate. Tiers must be supplied in ascending threshold
    order with non-increasing cumulative probabilities, because ``probability``
    means *P(reach this tier or beyond)*.

    Retroactive tiers apply the achieved rate to all volume from unit one.
    Incremental tiers apply each rate only to the volume inside its band.
    """
    if not tiers:
        raise EngineError("At least one rebate tier is required")
    if invoice_price_per_unit < 0:
        raise EngineError("invoice_price_per_unit cannot be negative")

    breakage = _require_blocking("rebate_breakage_rate", params.rebate_breakage_rate)

    ordered = list(tiers)
    for a, b in zip(ordered, ordered[1:]):
        if b.threshold_units <= a.threshold_units:
            raise EngineError(
                "Rebate tiers must be in ascending threshold order; "
                f"tier at {b.threshold_units} does not exceed {a.threshold_units}"
            )
        if b.probability > a.probability:
            raise EngineError(
                "Tier probabilities are cumulative P(reach tier or beyond) and must be "
                f"non-increasing; {b.probability} follows {a.probability}"
            )

    # Marginal probability of landing in each tier band.
    marginals = [
        t.probability - (ordered[i + 1].probability if i + 1 < len(ordered) else 0.0)
        for i, t in enumerate(ordered)
    ]

    per_tier: list[dict] = []
    cumulative_incremental = 0.0
    previous_threshold = 0.0
    gross = 0.0

    for tier, marginal in zip(ordered, marginals):
        retro_value = tier.rate * tier.threshold_units * invoice_price_per_unit
        cumulative_incremental += (
            tier.rate * (tier.threshold_units - previous_threshold) * invoice_price_per_unit
        )
        previous_threshold = tier.threshold_units

        value_if_achieved = retro_value if structure == "retro" else cumulative_incremental
        expected = marginal * value_if_achieved
        gross += expected

        per_tier.append(
            {
                "threshold_units": tier.threshold_units,
                "rate": tier.rate,
                "cumulative_probability": tier.probability,
                "marginal_probability": marginal,
                "value_if_achieved": value_if_achieved,
                "expected_value": expected,
            }
        )

    after_breakage = gross * (1.0 - breakage)
    after_tax = after_breakage * params.tax_efficiency_off_invoice
    net = after_tax * params.rebate_pv_factor

    top_tier = max(ordered, key=lambda t: t.threshold_units)
    # Effective rate is measured against spend at the most probable achieved tier,
    # so it is comparable with the headline rate the vendor quotes.
    reference_units = max(
        (t.threshold_units for t in ordered if t.probability >= 0.5),
        default=ordered[0].threshold_units,
    )
    reference_spend = reference_units * invoice_price_per_unit
    effective_rate = net / reference_spend if reference_spend else 0.0

    return RebateResult(
        gross_expected=gross,
        after_breakage=after_breakage,
        after_tax=after_tax,
        net_expected=net,
        headline_top_rate=top_tier.rate,
        effective_net_rate=effective_rate,
        per_tier=per_tier,
    )


# --------------------------------------------------------------------------
# Canonical offer and ENUC
# --------------------------------------------------------------------------


@dataclass
class Offer:
    """Any vendor proposal, normalised to one shape.

    Quantities and prices are expressed in the vendor's *quoted* unit; ``uom``
    converts them to clinical units. If a proposal cannot be forced into this
    shape it cannot be compared, and that normalisation step is the product.
    """

    vendor: str
    sku_group: str
    quoted_annual_quantity: float
    list_price_per_quoted_unit: float
    uom: UomConversion
    on_invoice_discount: float = 0.0
    free_goods_ratio: float = 0.0
    payment_terms_days: int = 30
    wastage_rate: float = 0.0
    instrument_capex_paid: float = 0.0
    instrument_placed_free_value: float = 0.0
    annual_service_cost: float = 0.0
    annual_logistics_cost: float = 0.0
    switching_cost: float = 0.0
    sponsorship_annual_value: float = 0.0
    sponsorship_volume_linked: bool = False
    beneficiary_selection_independent: bool = True
    rebate_tiers: list[RebateTier] = field(default_factory=list)
    rebate_structure: RebateStructure = "retro"
    single_source: bool | None = None

    def __post_init__(self) -> None:
        if self.quoted_annual_quantity <= 0:
            raise EngineError(f"{self.vendor}: quoted_annual_quantity must be positive")
        if self.list_price_per_quoted_unit < 0:
            raise EngineError(f"{self.vendor}: list price cannot be negative")
        for name, value in (
            ("on_invoice_discount", self.on_invoice_discount),
            ("wastage_rate", self.wastage_rate),
        ):
            if not 0 <= value <= 1:
                raise EngineError(f"{self.vendor}: {name} must be between 0 and 1, got {value}")
        if self.free_goods_ratio < 0:
            raise EngineError(f"{self.vendor}: free_goods_ratio cannot be negative")

    @property
    def clinical_units_paid(self) -> float:
        return self.uom.to_clinical_units(self.quoted_annual_quantity)

    @property
    def invoice_price_per_clinical_unit(self) -> float:
        return self.uom.price_per_clinical_unit(
            self.list_price_per_quoted_unit * (1.0 - self.on_invoice_discount)
        )

    @property
    def effective_usable_units(self) -> float:
        """Paid units plus bonus stock, less what expires before use."""
        gross_units = self.clinical_units_paid * (1.0 + self.free_goods_ratio)
        return gross_units * (1.0 - self.wastage_rate)


@dataclass(frozen=True)
class EnucResult:
    vendor: str
    sku_group: str
    waterfall: list[tuple[str, float]]
    total_annual_cost_ledger_a: float
    effective_usable_units: float
    enuc_ledger_a: float
    sponsorship_annual_value: float
    enuc_ledger_b: float
    sponsorship_share_of_invoice: float
    compliance_flags: list[str]
    rebate: RebateResult | None

    @property
    def waterfall_per_unit(self) -> list[tuple[str, float]]:
        if not self.effective_usable_units:
            return [(label, 0.0) for label, _ in self.waterfall]
        return [(label, v / self.effective_usable_units) for label, v in self.waterfall]


def compute_enuc(offer: Offer, params: Parameters) -> EnucResult:
    """Collapse one offer into Effective Net Unit Cost, Ledger A and Ledger B.

    Sign convention: positive entries add cost, negative entries reduce it.
    """
    gross_spend = offer.quoted_annual_quantity * offer.list_price_per_quoted_unit
    discount = -gross_spend * offer.on_invoice_discount
    invoice_spend = gross_spend + discount

    rebate: RebateResult | None = None
    rebate_value = 0.0
    if offer.rebate_tiers:
        rebate = rebate_expected_value(
            offer.rebate_tiers,
            offer.invoice_price_per_clinical_unit,
            params,
            offer.rebate_structure,
        )
        rebate_value = -rebate.net_expected

    capex = offer.instrument_capex_paid / params.contract_years
    # A placed instrument is avoided capex, so it reduces cost. This is what a
    # "free" analyser is actually worth, and what a reagent-rental premium buys.
    placed = -offer.instrument_placed_free_value / params.contract_years
    switching = offer.switching_cost / params.contract_years
    terms_benefit = -(
        invoice_spend
        * params.wacc
        * (offer.payment_terms_days - params.baseline_payment_days)
        / DAYS_PER_YEAR
    )

    waterfall: list[tuple[str, float]] = [
        ("Gross list spend", gross_spend),
        ("On-invoice discount", discount),
        ("Expected net rebate", rebate_value),
        ("Instrument capex paid (amortised)", capex),
        ("Instrument placed free (amortised benefit)", placed),
        ("Annual service contract", offer.annual_service_cost),
        ("Logistics / MOQ carrying cost", offer.annual_logistics_cost),
        ("Switching cost (amortised)", switching),
        ("Payment-terms carry benefit", terms_benefit),
    ]

    total_a = sum(v for _, v in waterfall)
    units = offer.effective_usable_units
    if units <= 0:
        raise EngineError(
            f"{offer.vendor}: effective usable units resolved to {units}. "
            "Check wastage_rate is not 1.0 and the UoM conversion is correct."
        )

    enuc_a = total_a / units
    total_b = total_a - offer.sponsorship_annual_value
    enuc_b = total_b / units

    flags: list[str] = []
    if offer.sponsorship_volume_linked:
        flags.append(
            "RED FLAG: sponsorship is contractually linked to purchase volume. "
            "This must be removed from the contract before award."
        )
    if not offer.beneficiary_selection_independent:
        flags.append(
            "RED FLAG: beneficiary selection is not independent of procurement. "
            "Selection must sit with a committee holding no procurement decision rights."
        )
    if offer.sponsorship_annual_value > 0 and invoice_spend > 0:
        share = offer.sponsorship_annual_value / invoice_spend
        if share > 0.05:
            flags.append(
                f"WATCH: sponsorship is {share:.1%} of invoice spend. A vendor quoting above "
                "market while sponsoring heavily may be buying the price gap."
            )

    return EnucResult(
        vendor=offer.vendor,
        sku_group=offer.sku_group,
        waterfall=waterfall,
        total_annual_cost_ledger_a=total_a,
        effective_usable_units=units,
        enuc_ledger_a=enuc_a,
        sponsorship_annual_value=offer.sponsorship_annual_value,
        enuc_ledger_b=enuc_b,
        sponsorship_share_of_invoice=(
            offer.sponsorship_annual_value / invoice_spend if invoice_spend else 0.0
        ),
        compliance_flags=flags,
        rebate=rebate,
    )


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def compare_offers(offers: Sequence[Offer], params: Parameters) -> dict:
    """Rank offers on Ledger A and report whether sponsorship would change the ranking."""
    if len(offers) < 2:
        raise EngineError("Comparison needs at least two offers")

    sku_groups = {o.sku_group for o in offers}
    if len(sku_groups) > 1:
        raise EngineError(
            "All offers must belong to the same SKU group for the comparison to be valid. "
            f"Got: {sorted(sku_groups)}"
        )

    results = [compute_enuc(o, params) for o in offers]

    order_a = sorted(range(len(results)), key=lambda i: results[i].enuc_ledger_a)
    order_b = sorted(range(len(results)), key=lambda i: results[i].enuc_ledger_b)
    rank_a = {i: pos + 1 for pos, i in enumerate(order_a)}
    rank_b = {i: pos + 1 for pos, i in enumerate(order_b)}

    winner = results[order_a[0]]
    worst = results[order_a[-1]]
    spread = worst.enuc_ledger_a - winner.enuc_ledger_a

    ranking = [
        {
            "vendor": r.vendor,
            "enuc_ledger_a": r.enuc_ledger_a,
            "rank_ledger_a": rank_a[i],
            "enuc_ledger_b": r.enuc_ledger_b,
            "rank_ledger_b": rank_b[i],
            "rank_changed_by_sponsorship": rank_a[i] != rank_b[i],
            "compliance_flags": r.compliance_flags,
        }
        for i, r in enumerate(results)
    ]

    return {
        "sku_group": next(iter(sku_groups)),
        "award_basis": "Ledger A — sponsorship excluded",
        "ranking": ranking,
        "winner": winner.vendor,
        "winner_enuc": winner.enuc_ledger_a,
        "spread_best_vs_worst_per_unit": spread,
        "annual_value_of_choosing_best": spread * winner.effective_usable_units,
        "sponsorship_changes_ranking": any(r["rank_changed_by_sponsorship"] for r in ranking),
        "results": results,
    }


# --------------------------------------------------------------------------
# Trade ratios
# --------------------------------------------------------------------------


def trade_ratios(offer: Offer, params: Parameters) -> dict:
    """What one percent of price is worth in every other lever.

    The negotiator's live cheat sheet: no concession is given away unilaterally,
    each one is traded at these rates.
    """
    breakage = _require_blocking("rebate_breakage_rate", params.rebate_breakage_rate)

    gross_spend = offer.quoted_annual_quantity * offer.list_price_per_quoted_unit
    invoice_spend = gross_spend * (1.0 - offer.on_invoice_discount)
    one_percent = invoice_spend * 0.01

    rebate_efficiency = (
        (1.0 - breakage) * params.tax_efficiency_off_invoice * params.rebate_pv_factor
    )
    enuc = compute_enuc(offer, params).enuc_ledger_a

    return {
        "vendor": offer.vendor,
        "invoice_spend": invoice_spend,
        "value_of_one_percent_price": one_percent,
        "equivalent_payment_terms_days": 0.01 * DAYS_PER_YEAR / params.wacc,
        "equivalent_off_invoice_rebate_points": 0.01 / rebate_efficiency,
        "equivalent_bonus_clinical_units": one_percent / enuc if enuc else 0.0,
        "equivalent_free_goods_ratio": (
            (one_percent / enuc) / offer.clinical_units_paid if enuc else 0.0
        ),
        "equivalent_placement_value_over_term": one_percent * params.contract_years,
        "equivalent_annual_service_value": one_percent,
        "note": (
            "One point of off-invoice rebate is worth less than one point on-invoice, "
            "so trading rebate points for on-invoice price is cheaper for the vendor and "
            "better for you. Find that trade first."
        ),
    }


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    headline: str
    current_enuc: float
    target_enuc: float
    reservation_enuc: float
    batna_enuc_including_switching: float
    gap_to_target_per_unit: float
    gap_to_target_pct: float
    headroom_to_reservation_per_unit: float
    annual_value_of_closing_gap: float
    rationale: list[str]


def decide(
    current_enuc: float,
    target_enuc: float,
    reservation_enuc: float | None,
    batna_enuc: float | None,
    switching_cost_per_unit: float,
    annual_units: float,
    single_source: bool | None = None,
) -> VerdictResult:
    """Fill the decision gate that PCN steps 18 and 20 branch on but never define.

    ``reservation_enuc`` is mandatory and must be set by someone other than the
    negotiator. Without it there is no walk-away point and the gate is empty.
    """
    reservation = _require_blocking("reservation_enuc", reservation_enuc)

    if target_enuc > reservation:
        raise EngineError(
            f"Target ENUC ({target_enuc:,.0f}) is above the reservation price "
            f"({reservation:,.0f}). The target must be the better outcome; check the inputs."
        )

    rationale: list[str] = []

    if batna_enuc is None:
        batna_adjusted = float("inf")
        rationale.append(
            "No BATNA supplied, so no alternative-is-cheaper test was run. "
            "A BATNA you cannot name is a BATNA you do not have."
        )
    else:
        batna_adjusted = batna_enuc + switching_cost_per_unit
        rationale.append(
            f"BATNA {batna_enuc:,.0f} plus switching cost {switching_cost_per_unit:,.0f} "
            f"= {batna_adjusted:,.0f}. That is the number the incumbent must beat."
        )

    if single_source:
        rationale.append(
            "This SKU is flagged single-source: there is no credible walk-away, so the "
            "leverage here is rebate realization and internal price variance, not a threat "
            "to switch. Treat a WALK verdict as an escalation, not an instruction."
        )

    if current_enuc > reservation:
        verdict: Verdict = "WALK"
        headline = "WALK / RE-TENDER — the offer is worse than your walk-away point"
    elif current_enuc > batna_adjusted:
        verdict = "PUSH_ALTERNATIVE_CHEAPER"
        headline = "PUSH — a real alternative is cheaper than this offer"
    elif current_enuc > target_enuc:
        verdict = "PUSH_ABOVE_TARGET"
        headline = "PUSH — still above target, there is room left"
    else:
        verdict = "ACCEPT"
        headline = "ACCEPT — at or below target"

    gap = current_enuc - target_enuc
    return VerdictResult(
        verdict=verdict,
        headline=headline,
        current_enuc=current_enuc,
        target_enuc=target_enuc,
        reservation_enuc=reservation,
        batna_enuc_including_switching=batna_adjusted,
        gap_to_target_per_unit=gap,
        gap_to_target_pct=gap / target_enuc if target_enuc else 0.0,
        headroom_to_reservation_per_unit=reservation - current_enuc,
        annual_value_of_closing_gap=gap * annual_units,
        rationale=rationale,
    )


# --------------------------------------------------------------------------
# Seasonal price increase — binding conversion
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BindingConversionResult:
    binding_type: BindingType
    old_list_price: float
    new_list_price: float
    list_increase_pct: float
    old_net_price: float
    new_net_price: float
    old_discount: float
    required_or_resulting_discount: float
    net_price_change_pct: float
    annual_cost_impact: float
    explanation: str
    caveat: str


def convert_price_increase(
    binding_type: BindingType,
    old_list_price: float,
    new_list_price: float,
    old_discount: float,
    annual_quantity: float,
    max_discount_cap: float | None = None,
) -> BindingConversionResult:
    """Convert a principal's seasonal price increase under the contract binding rule.

    Derived from Consumables Seasonal Price Increase Management:

    * step 5, net binding — "calculate conversion discount to keep the net price
      constant", so the discount flexes and the net price is held;
    * step 6, discount binding — "compute the appropriate conversion discount
      corresponding to the price increase", so the discount is held and the net
      price moves with the list price.

    That reading comes from the process document, not from a contract. Confirm it
    against a real agreement before anyone relies on the output.
    """
    if old_list_price <= 0 or new_list_price <= 0:
        raise EngineError("List prices must be positive")
    if not 0 <= old_discount < 1:
        raise EngineError(f"old_discount must be between 0 and 1, got {old_discount}")

    old_net = old_list_price * (1.0 - old_discount)
    list_increase = (new_list_price / old_list_price) - 1.0

    if binding_type == "net":
        required_discount = 1.0 - (old_net / new_list_price)
        new_net = old_net
        explanation = (
            f"Net binding holds the net price at {old_net:,.2f}. To absorb a "
            f"{list_increase:.2%} list increase the discount must rise from "
            f"{old_discount:.2%} to {required_discount:.2%}."
        )
        if required_discount < 0:
            raise EngineError(
                f"Holding the net price at {old_net:,.2f} would require a NEGATIVE discount "
                f"of {required_discount:.2%}, because the new list price ({new_list_price:,.2f}) "
                f"is already below it. Net binding cannot apply here — either the list price "
                f"fell, or the old discount is wrong. Check the inputs before proceeding."
            )
        if max_discount_cap is not None and required_discount > max_discount_cap:
            explanation += (
                f" This exceeds the agreed discount cap of {max_discount_cap:.2%} — "
                "escalate rather than accept."
            )
        resulting = required_discount
    else:
        resulting = old_discount
        new_net = new_list_price * (1.0 - old_discount)
        explanation = (
            f"Discount binding holds the discount at {old_discount:.2%}, so the "
            f"{list_increase:.2%} list increase passes through to the net price, "
            f"moving it from {old_net:,.2f} to {new_net:,.2f}."
        )

    net_change = (new_net / old_net) - 1.0 if old_net else 0.0

    return BindingConversionResult(
        binding_type=binding_type,
        old_list_price=old_list_price,
        new_list_price=new_list_price,
        list_increase_pct=list_increase,
        old_net_price=old_net,
        new_net_price=new_net,
        old_discount=old_discount,
        required_or_resulting_discount=resulting,
        net_price_change_pct=net_change,
        annual_cost_impact=(new_net - old_net) * annual_quantity,
        explanation=explanation,
        caveat=(
            "Binding-type definitions are inferred from the Seasonal Price Increase "
            "process document (steps 4-6), not from a contract. Data element D-08 is "
            "still open — confirm with the category owner before relying on this."
        ),
    )


# --------------------------------------------------------------------------
# Rebate realization
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RealizationResult:
    tier_target_units: float
    cumulative_units: float
    period_elapsed: float
    tier_achieved_pct: float
    pace_index: float
    status: str
    accrued_rebate: float
    collected_rebate: float
    breakage_to_date: float
    projected_year_end_units: float
    tier_will_be_met: bool
    message: str


def rebate_realization(
    monthly_units: Sequence[float],
    tier_target_units: float,
    tier_rate: float,
    net_invoice_price_per_unit: float,
    breakage_rate: float | None,
    collected_to_date: float = 0.0,
    months_in_period: int = MONTHS_PER_YEAR,
) -> RealizationResult:
    """Track a signed rebate from accrual to cash.

    A rebate negotiated and never collected is a discount given away for free,
    and it is usually the largest single recoverable amount in a procurement book.
    """
    if tier_target_units <= 0:
        raise EngineError("tier_target_units must be positive")
    if not monthly_units:
        raise EngineError("At least one month of volume is required")
    if len(monthly_units) > months_in_period:
        raise EngineError(
            f"Got {len(monthly_units)} months of data for a {months_in_period}-month period"
        )

    breakage = _require_blocking("rebate_breakage_rate", breakage_rate)

    observed = [u for u in monthly_units if u is not None]
    months_elapsed = len(observed)
    cumulative = sum(observed)
    elapsed = months_elapsed / months_in_period
    achieved = cumulative / tier_target_units
    pace = achieved / elapsed if elapsed else 0.0

    run_rate = cumulative / months_elapsed if months_elapsed else 0.0
    projected = run_rate * months_in_period
    will_meet = projected >= tier_target_units

    if pace < 0.9:
        status = "BEHIND — tier at risk"
    elif pace > 1.1:
        status = "AHEAD"
    else:
        status = "ON TRACK"

    accrued = cumulative * tier_rate * net_invoice_price_per_unit * (1.0 - breakage)
    breakage_to_date = accrued - collected_to_date

    if not will_meet:
        message = (
            f"Projected year-end volume {projected:,.0f} falls short of the "
            f"{tier_target_units:,.0f} tier. Renegotiate the tier or accept the lower rate — "
            "do not buy volume you do not need to reach it."
        )
    elif breakage_to_date > 0:
        message = (
            f"{breakage_to_date:,.0f} of rebate has been earned and accrued but not yet "
            "collected. This is cash already won; chase it."
        )
    else:
        message = "On track and fully collected to date."

    return RealizationResult(
        tier_target_units=tier_target_units,
        cumulative_units=cumulative,
        period_elapsed=elapsed,
        tier_achieved_pct=achieved,
        pace_index=pace,
        status=status,
        accrued_rebate=accrued,
        collected_rebate=collected_to_date,
        breakage_to_date=breakage_to_date,
        projected_year_end_units=projected,
        tier_will_be_met=will_meet,
        message=message,
    )
