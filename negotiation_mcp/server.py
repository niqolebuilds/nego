#!/usr/bin/env python3
"""MCP server for the Siloam procurement negotiation engine.

Exposes the decision layer that the Blueprint AI process catalogue describes but
does not have: Principal Contract Negotiation steps 18 and 20, and Consumables
Seasonal Price Increase step 8, all branch on thresholds that the catalogue's own
gap lists say do not exist.

Every tool is read-only — no external API, no writes. The calculation tools take
their data as parameters. The price-intelligence tools also read the local price book
(CSV exports in ``NEGOTIATION_DATA_DIR``) and never write to it. Nine data elements in
the register are still blocking, and the engine refuses to invent them. A tool that
needs a missing element fails with the element's register ID and its owner rather
than substituting a plausible default.

Transport: stdio. Run with ``python -m negotiation_mcp.server``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import engine as E
from . import intelligence as I
from .formatting import (
    ResponseFormat,
    alerts_markdown,
    beat_markdown,
    benchmark_markdown,
    binding_markdown,
    brief_markdown,
    comparison_markdown,
    counter_markdown,
    enuc_markdown,
    lookup_markdown,
    realization_markdown,
    savings_markdown,
    targets_markdown,
    to_json,
    vendor_spend_markdown,
    verdict_markdown,
)
from .pricebook import SAMPLE_BANNER, PriceBook, cached_book

mcp = FastMCP("negotiation_mcp")

SERVER_VERSION = "0.2.0"


# ==========================================================================
# Shared input models
# ==========================================================================


class StrictModel(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True, validate_assignment=True, extra="forbid"
    )


class ParametersInput(StrictModel):
    """Global assumptions. In production these resolve through the parameter
    hierarchy (group to deal, most specific wins) with effective dating."""

    wacc: float = Field(
        default=0.12, description="Annual cost of capital, as a fraction (0.12 = 12%)", ge=0, lt=1
    )
    baseline_payment_days: int = Field(
        default=30, description="Group standard payment terms in days", ge=0, le=365
    )
    contract_years: float = Field(
        default=3.0, description="Contract term in years, used to amortise capex", gt=0, le=20
    )
    rebate_breakage_rate: Optional[float] = Field(
        default=None,
        description=(
            "Share of EARNED rebate historically never claimed or collected, as a fraction. "
            "Data element D-18, owner Finance / AP. No default is supplied on purpose"
        ),
        ge=0,
        le=1,
    )
    rebate_collection_lag_months: float = Field(
        default=6.0, description="Average months between earning and receiving rebate cash", ge=0, le=60
    )
    tax_efficiency_off_invoice: float = Field(
        default=0.88,
        description=(
            "Value retained on an off-invoice rebate after VAT and income treatment, "
            "relative to an on-invoice discount. PLACEHOLDER — data element D-19, owner Tax"
        ),
        gt=0,
        le=1,
    )
    currency: str = Field(default="IDR", description="Currency code for display", max_length=8)

    def to_engine(self) -> E.Parameters:
        return E.Parameters(
            wacc=self.wacc,
            baseline_payment_days=self.baseline_payment_days,
            contract_years=self.contract_years,
            rebate_breakage_rate=self.rebate_breakage_rate,
            rebate_collection_lag_months=self.rebate_collection_lag_months,
            tax_efficiency_off_invoice=self.tax_efficiency_off_invoice,
            currency=self.currency,
        )


class RebateTierInput(StrictModel):
    threshold_units: float = Field(
        ..., description="Cumulative clinical units at which this tier is reached", gt=0
    )
    rate: float = Field(..., description="Rebate rate as a fraction (0.03 = 3%)", ge=0, le=1)
    probability: float = Field(
        ...,
        description=(
            "YOUR probability of reaching this tier or beyond, from your own volume "
            "history — never the vendor's forecast. Must be non-increasing across tiers"
        ),
        ge=0,
        le=1,
    )

    def to_engine(self) -> E.RebateTier:
        return E.RebateTier(self.threshold_units, self.rate, self.probability)


class OfferInput(StrictModel):
    """One vendor proposal, normalised. If a proposal cannot be forced into this
    shape it cannot be compared — that normalisation is the point."""

    vendor: str = Field(..., description="Vendor or principal name", min_length=1, max_length=200)
    sku_group: str = Field(
        ...,
        description="Clinical equivalence group. Offers are only comparable within one group",
        min_length=1,
        max_length=200,
    )
    quoted_annual_quantity: float = Field(
        ..., description="Annual quantity in the vendor's QUOTED unit (boxes, packs, each)", gt=0
    )
    list_price_per_quoted_unit: float = Field(
        ..., description="List price per quoted unit, before discount", ge=0
    )
    clinical_units_per_quoted_unit: float = Field(
        default=1.0,
        description=(
            "How many usable clinical units are in one quoted unit — e.g. 50 for a box "
            "of 50 tests. Data element D-13; getting this wrong makes every ENUC wrong "
            "in a way that still looks plausible"
        ),
        gt=0,
    )
    quoted_unit_name: str = Field(default="unit", description="Name of the quoted unit", max_length=50)
    on_invoice_discount: float = Field(
        default=0.0, description="On-invoice discount as a fraction. Reduces the VAT base", ge=0, le=1
    )
    free_goods_ratio: float = Field(
        default=0.0,
        description="Bonus stock as a fraction (0.10 = 10+1). A denominator effect, not a price cut",
        ge=0,
        le=10,
    )
    payment_terms_days: int = Field(default=30, description="Payment terms in days", ge=0, le=365)
    wastage_rate: float = Field(
        default=0.0,
        description="Share of units expiring or wasted before use. Applied to paid AND bonus units",
        ge=0,
        lt=1,
    )
    instrument_capex_paid: float = Field(
        default=0.0, description="Instrument capex the hospital pays, total over the term", ge=0
    )
    instrument_placed_free_value: float = Field(
        default=0.0,
        description=(
            "Fair value of an instrument the vendor places free. Treated as avoided capex — "
            "this is what a 'free' analyser is actually worth"
        ),
        ge=0,
    )
    annual_service_cost: float = Field(default=0.0, description="Annual service contract cost", ge=0)
    annual_logistics_cost: float = Field(
        default=0.0, description="Annual logistics and MOQ carrying cost", ge=0
    )
    switching_cost: float = Field(
        default=0.0, description="One-off switching cost: validation, training, interface", ge=0
    )
    sponsorship_annual_value: float = Field(
        default=0.0,
        description="Sponsorship, CME, grants and in-kind value per year. Ledger B only, never used to rank",
        ge=0,
    )
    sponsorship_volume_linked: bool = Field(
        default=False, description="True if sponsorship is contractually tied to purchase volume"
    )
    beneficiary_selection_independent: bool = Field(
        default=True, description="True if beneficiary selection sits outside the procurement chain"
    )
    rebate_tiers: list[RebateTierInput] = Field(
        default_factory=list, description="Tiered rebate schedule, ascending by threshold", max_length=12
    )
    rebate_structure: Literal["retro", "incremental"] = Field(
        default="retro",
        description=(
            "'retro' applies the achieved rate to all volume from unit one; "
            "'incremental' applies each rate only within its band"
        ),
    )
    single_source: Optional[bool] = Field(
        default=None,
        description=(
            "True if this SKU group has no clinically acceptable alternative. "
            "Data element D-23, owner Pharmacy. Decides whether a BATNA exists at all"
        ),
    )

    @field_validator("vendor", "sku_group")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("cannot be empty or whitespace")
        return v.strip()

    def to_engine(self) -> E.Offer:
        return E.Offer(
            vendor=self.vendor,
            sku_group=self.sku_group,
            quoted_annual_quantity=self.quoted_annual_quantity,
            list_price_per_quoted_unit=self.list_price_per_quoted_unit,
            uom=E.UomConversion(self.quoted_unit_name, self.clinical_units_per_quoted_unit),
            on_invoice_discount=self.on_invoice_discount,
            free_goods_ratio=self.free_goods_ratio,
            payment_terms_days=self.payment_terms_days,
            wastage_rate=self.wastage_rate,
            instrument_capex_paid=self.instrument_capex_paid,
            instrument_placed_free_value=self.instrument_placed_free_value,
            annual_service_cost=self.annual_service_cost,
            annual_logistics_cost=self.annual_logistics_cost,
            switching_cost=self.switching_cost,
            sponsorship_annual_value=self.sponsorship_annual_value,
            sponsorship_volume_linked=self.sponsorship_volume_linked,
            beneficiary_selection_independent=self.beneficiary_selection_independent,
            rebate_tiers=[t.to_engine() for t in self.rebate_tiers],
            rebate_structure=self.rebate_structure,
            single_source=self.single_source,
        )


def _error(e: Exception) -> str:
    """Errors name what is missing and who owns it, so the caller can act."""
    if isinstance(e, E.EngineError):
        return f"Error: {e}"
    if isinstance(e, (ValueError, TypeError)):
        return f"Error: invalid input — {e}"
    return f"Error: unexpected {type(e).__name__} — {e}"


def _respond(fmt: ResponseFormat, markdown: str, payload: Any) -> str:
    return markdown if fmt == ResponseFormat.MARKDOWN else to_json(payload)


# ==========================================================================
# Tools
# ==========================================================================


class ComputeEnucInput(StrictModel):
    offer: OfferInput = Field(..., description="The vendor offer to evaluate")
    parameters: ParametersInput = Field(
        default_factory=ParametersInput, description="Global assumptions"
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' to read, 'json' to process"
    )


@mcp.tool(
    name="negotiation_compute_enuc",
    annotations={
        "title": "Compute Effective Net Unit Cost",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def negotiation_compute_enuc(params: ComputeEnucInput) -> str:
    """Collapse one vendor offer into a single comparable cost per usable clinical unit.

    Folds price, on-invoice discount, probability-weighted rebate, bonus stock,
    wastage, payment terms, instrument capex or free placement, service and
    logistics into one number. Returns Ledger A (excludes sponsorship, the award
    basis) and Ledger B (includes it, monitoring only).

    Replaces the scattered Excel of Principal Contract Negotiation step 12.

    Args:
        params (ComputeEnucInput):
            - offer (OfferInput): the offer, in the vendor's quoted units
            - parameters (ParametersInput): WACC, contract term, breakage, tax factors
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: markdown report, or JSON with schema:
        {
          "vendor": str, "sku_group": str,
          "waterfall": [[str, float]],          # annual, signed: + adds cost
          "total_annual_cost_ledger_a": float,
          "effective_usable_units": float,
          "enuc_ledger_a": float,               # THE number — rank and award on this
          "sponsorship_annual_value": float,
          "enuc_ledger_b": float,               # monitoring only
          "sponsorship_share_of_invoice": float,
          "compliance_flags": [str],
          "rebate": {...} | null
        }

    Examples:
        - "What does this reagent deal actually cost per test?" -> one offer
        - "Is their free analyser really free?" -> set instrument_placed_free_value
        - Don't use when: comparing several offers — use negotiation_compare_offers

    Error Handling:
        - Missing rebate_breakage_rate when tiers are supplied returns an error naming
          data element D-18 rather than assuming a rate.
    """
    try:
        result = E.compute_enuc(params.offer.to_engine(), params.parameters.to_engine())
        return _respond(
            params.response_format,
            enuc_markdown(result, params.parameters.currency),
            result,
        )
    except Exception as e:  # noqa: BLE001 - surfaced to the agent as text
        return _error(e)


class CompareOffersInput(StrictModel):
    offers: list[OfferInput] = Field(
        ..., description="Two or more offers for the SAME SKU group", min_length=2, max_length=10
    )
    parameters: ParametersInput = Field(default_factory=ParametersInput)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="negotiation_compare_offers",
    annotations={
        "title": "Compare Offers and Rank",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def negotiation_compare_offers(params: CompareOffersInput) -> str:
    """Rank competing offers on ENUC and flag whether sponsorship would change the ranking.

    Ranks on Ledger A only. Also computes Ledger B and reports if the two orders
    differ — a vendor quoting above market while sponsoring heavily is buying the
    gap, and this is what surfaces it.

    Args:
        params (CompareOffersInput):
            - offers (list[OfferInput]): 2 to 10 offers, all in one SKU group
            - parameters (ParametersInput): global assumptions
            - response_format (ResponseFormat)

    Returns:
        str: markdown table, or JSON with schema:
        {
          "sku_group": str, "award_basis": str, "winner": str, "winner_enuc": float,
          "ranking": [{"vendor": str, "enuc_ledger_a": float, "rank_ledger_a": int,
                       "enuc_ledger_b": float, "rank_ledger_b": int,
                       "rank_changed_by_sponsorship": bool, "compliance_flags": [str]}],
          "spread_best_vs_worst_per_unit": float,
          "annual_value_of_choosing_best": float,
          "sponsorship_changes_ranking": bool
        }

    Examples:
        - "Which of these three principals is cheapest, properly?" -> three offers
        - "Does their sponsorship change who wins?" -> read sponsorship_changes_ranking

    Error Handling:
        - Offers from different SKU groups are refused: the comparison would be invalid.
    """
    try:
        result = E.compare_offers(
            [o.to_engine() for o in params.offers], params.parameters.to_engine()
        )
        payload = {k: v for k, v in result.items() if k != "results"}
        return _respond(
            params.response_format,
            comparison_markdown(result, params.parameters.currency),
            payload,
        )
    except Exception as e:  # noqa: BLE001
        return _error(e)


class RebateInput(StrictModel):
    tiers: list[RebateTierInput] = Field(
        ..., description="Tier schedule, ascending threshold", min_length=1, max_length=12
    )
    invoice_price_per_clinical_unit: float = Field(
        ..., description="Net invoice price per clinical unit, after on-invoice discount", ge=0
    )
    structure: Literal["retro", "incremental"] = Field(default="retro")
    parameters: ParametersInput = Field(default_factory=ParametersInput)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="negotiation_rebate_expected_value",
    annotations={
        "title": "Probability-Weighted Rebate Value",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def negotiation_rebate_expected_value(params: RebateInput) -> str:
    """Turn a headline rebate schedule into the cash you can actually expect.

    Probability-weights each tier from your own volume distribution, then deducts
    breakage, off-invoice tax inefficiency and the present-value cost of the
    collection lag. The gap between the headline rate and the effective rate is
    what the vendor is really offering, and it is typically 30 to 45 percent smaller.

    Args:
        params (RebateInput):
            - tiers (list[RebateTierInput]): threshold, rate, cumulative probability
            - invoice_price_per_clinical_unit (float)
            - structure ('retro' | 'incremental')
            - parameters (ParametersInput): must include rebate_breakage_rate

    Returns:
        str: markdown, or JSON with schema:
        {"gross_expected": float, "after_breakage": float, "after_tax": float,
         "net_expected": float, "headline_top_rate": float,
         "effective_net_rate": float, "per_tier": [{...}]}

    Examples:
        - "They're offering up to 6% — what's that really worth?" -> the tier schedule
        - Don't use when: you want the full cost picture — use negotiation_compute_enuc

    Error Handling:
        - Non-increasing probabilities or out-of-order thresholds are refused.
        - A missing breakage rate returns an error naming data element D-18.
    """
    try:
        result = E.rebate_expected_value(
            [t.to_engine() for t in params.tiers],
            params.invoice_price_per_clinical_unit,
            params.parameters.to_engine(),
            params.structure,
        )
        cur = params.parameters.currency
        md = "\n".join(
            [
                "# Rebate expected value",
                "",
                f"- Headline top-tier rate: **{result.headline_top_rate * 100:.2f}%**",
                f"- Effective net rate: **{result.effective_net_rate * 100:.2f}%**",
                f"- Haircut against headline: **{result.headline_haircut * 100:.0f}%**",
                "",
                "| Step | Amount |",
                "|---|---:|",
                f"| Gross expected | {cur} {result.gross_expected:,.0f} |",
                f"| After breakage | {cur} {result.after_breakage:,.0f} |",
                f"| After tax treatment | {cur} {result.after_tax:,.0f} |",
                f"| **Net expected** | **{cur} {result.net_expected:,.0f}** |",
                "",
                "| Tier | Threshold | Rate | P(reach) | Marginal P | Expected |",
                "|---:|---:|---:|---:|---:|---:|",
            ]
            + [
                f"| {i + 1} | {t['threshold_units']:,.0f} | {t['rate'] * 100:.2f}% | "
                f"{t['cumulative_probability'] * 100:.0f}% | "
                f"{t['marginal_probability'] * 100:.0f}% | {cur} {t['expected_value']:,.0f} |"
                for i, t in enumerate(result.per_tier)
            ]
        )
        return _respond(params.response_format, md, result)
    except Exception as e:  # noqa: BLE001
        return _error(e)


class TradeRatiosInput(StrictModel):
    offer: OfferInput = Field(..., description="The offer on the table")
    parameters: ParametersInput = Field(default_factory=ParametersInput)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="negotiation_trade_ratios",
    annotations={
        "title": "What 1% of Price Is Worth in Other Levers",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def negotiation_trade_ratios(params: TradeRatiosInput) -> str:
    """Compute the exchange rates between price and every other concession.

    The negotiator's live cheat sheet: how many days of payment terms, how many
    rebate points, how much bonus stock or placement value equal one percent of
    price. No concession should be given away unilaterally; each is traded at
    these rates. Feeds Principal Contract Negotiation step 9.

    Args:
        params (TradeRatiosInput): offer, parameters, response_format

    Returns:
        str: markdown, or JSON with schema:
        {"vendor": str, "invoice_spend": float, "value_of_one_percent_price": float,
         "equivalent_payment_terms_days": float,
         "equivalent_off_invoice_rebate_points": float,
         "equivalent_bonus_clinical_units": float,
         "equivalent_free_goods_ratio": float,
         "equivalent_placement_value_over_term": float,
         "equivalent_annual_service_value": float, "note": str}

    Examples:
        - "They won't move on price — what should I ask for instead?"
        - "Is 2% rebate a fair swap for 1% off the invoice?" -> compare to
          equivalent_off_invoice_rebate_points

    Error Handling:
        - A missing breakage rate returns an error naming data element D-18.
    """
    try:
        result = E.trade_ratios(params.offer.to_engine(), params.parameters.to_engine())
        cur = params.parameters.currency
        md = "\n".join(
            [
                f"# Trade ratios — {result['vendor']}",
                "",
                f"One percent of price is worth **{cur} "
                f"{result['value_of_one_percent_price']:,.0f}** per year.",
                "",
                "| 1% of price equals | |",
                "|---|---:|",
                f"| Payment-terms days | {result['equivalent_payment_terms_days']:.1f} days |",
                f"| Off-invoice rebate points | "
                f"{result['equivalent_off_invoice_rebate_points'] * 100:.2f}% |",
                f"| Bonus clinical units | "
                f"{result['equivalent_bonus_clinical_units']:,.0f} units |",
                f"| Free-goods ratio | {result['equivalent_free_goods_ratio'] * 100:.2f}% |",
                f"| Placement value over the term | {cur} "
                f"{result['equivalent_placement_value_over_term']:,.0f} |",
                f"| Annual service value | {cur} "
                f"{result['equivalent_annual_service_value']:,.0f} |",
                "",
                f"> {result['note']}",
            ]
        )
        return _respond(params.response_format, md, result)
    except Exception as e:  # noqa: BLE001
        return _error(e)


class VerdictInput(StrictModel):
    current_enuc: float = Field(..., description="ENUC of the offer currently on the table", gt=0)
    target_enuc: float = Field(
        ..., description="Best credible outcome — top-quartile internal or benchmark price", gt=0
    )
    reservation_enuc: Optional[float] = Field(
        default=None,
        description=(
            "Walk-away ENUC. Above this, switch or do not buy. Data element D-21 — "
            "must be set by someone who is not the negotiator. Required"
        ),
        gt=0,
    )
    batna_enuc: Optional[float] = Field(
        default=None, description="ENUC of the best real, sourced alternative. Not a bluff", gt=0
    )
    switching_cost_per_unit: float = Field(
        default=0.0, description="Switching cost spread over annual units", ge=0
    )
    annual_units: float = Field(..., description="Effective usable units per year", gt=0)
    single_source: Optional[bool] = Field(
        default=None, description="True if no clinically acceptable alternative exists (D-23)"
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="negotiation_verdict",
    annotations={
        "title": "Accept, Push or Walk",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def negotiation_verdict(params: VerdictInput) -> str:
    """Decide whether to accept the offer, keep pushing, or walk away.

    This fills the gate that Principal Contract Negotiation steps 18 and 20 branch
    on and that the catalogue's own gap list says has no defined threshold. The
    reservation price is mandatory: without it there is no walk-away point and the
    gate is empty.

    Args:
        params (VerdictInput):
            - current_enuc, target_enuc, reservation_enuc (required), batna_enuc
            - switching_cost_per_unit, annual_units, single_source

    Returns:
        str: markdown, or JSON with schema:
        {"verdict": "ACCEPT"|"PUSH_ABOVE_TARGET"|"PUSH_ALTERNATIVE_CHEAPER"|"WALK",
         "headline": str, "current_enuc": float, "target_enuc": float,
         "reservation_enuc": float, "batna_enuc_including_switching": float,
         "gap_to_target_per_unit": float, "gap_to_target_pct": float,
         "headroom_to_reservation_per_unit": float,
         "annual_value_of_closing_gap": float, "rationale": [str]}

    Examples:
        - "They've come back at 84,000 — do I take it?" -> with target and reservation
        - "Should we escalate this to management?" -> a WALK verdict is the trigger

    Error Handling:
        - Missing reservation_enuc returns an error naming data element D-21.
        - A target above the reservation price is refused as inconsistent.
        - When single_source is true, the rationale warns that a WALK is an
          escalation rather than an instruction.
    """
    try:
        result = E.decide(
            current_enuc=params.current_enuc,
            target_enuc=params.target_enuc,
            reservation_enuc=params.reservation_enuc,
            batna_enuc=params.batna_enuc,
            switching_cost_per_unit=params.switching_cost_per_unit,
            annual_units=params.annual_units,
            single_source=params.single_source,
        )
        return _respond(params.response_format, verdict_markdown(result), result)
    except Exception as e:  # noqa: BLE001
        return _error(e)


class BindingInput(StrictModel):
    binding_type: Literal["net", "discount"] = Field(
        ...,
        description=(
            "'net' holds the net price and flexes the discount; "
            "'discount' holds the discount and lets the net price move"
        ),
    )
    old_list_price: float = Field(..., description="List price before the increase", gt=0)
    new_list_price: float = Field(..., description="List price the principal is proposing", gt=0)
    old_discount: float = Field(..., description="Current discount as a fraction", ge=0, lt=1)
    annual_quantity: float = Field(..., description="Annual quantity in the same unit", gt=0)
    max_discount_cap: Optional[float] = Field(
        default=None, description="Agreed maximum discount, if any. Breaching it triggers escalation", ge=0, lt=1
    )
    currency: str = Field(default="IDR", max_length=8)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="negotiation_convert_price_increase",
    annotations={
        "title": "Seasonal Price Increase — Binding Conversion",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def negotiation_convert_price_increase(params: BindingInput) -> str:
    """Convert a principal's seasonal price increase under the contract binding rule.

    Implements Consumables Seasonal Price Increase steps 5 and 6. Under net
    binding the net price is held and the discount must rise to absorb the
    increase; under discount binding the discount is held and the increase passes
    through to the net price.

    The binding definitions are inferred from the process document, not from a
    contract — data element D-08 is still open. The output carries that caveat.

    Args:
        params (BindingInput): binding_type, old/new list price, old discount,
            annual quantity, optional max_discount_cap

    Returns:
        str: markdown, or JSON with schema:
        {"binding_type": str, "old_list_price": float, "new_list_price": float,
         "list_increase_pct": float, "old_net_price": float, "new_net_price": float,
         "old_discount": float, "required_or_resulting_discount": float,
         "net_price_change_pct": float, "annual_cost_impact": float,
         "explanation": str, "caveat": str}

    Examples:
        - "They're raising list 8% and we're on net binding — what discount do I need?"
        - "What does this increase cost us for the year?" -> annual_cost_impact

    Error Handling:
        - Refuses when holding the net price would require a discount of 100% or more.
    """
    try:
        result = E.convert_price_increase(
            binding_type=params.binding_type,
            old_list_price=params.old_list_price,
            new_list_price=params.new_list_price,
            old_discount=params.old_discount,
            annual_quantity=params.annual_quantity,
            max_discount_cap=params.max_discount_cap,
        )
        return _respond(
            params.response_format, binding_markdown(result, params.currency), result
        )
    except Exception as e:  # noqa: BLE001
        return _error(e)


class RealizationInput(StrictModel):
    monthly_units: list[float] = Field(
        ..., description="Clinical units per month so far, oldest first", min_length=1, max_length=12
    )
    tier_target_units: float = Field(..., description="Units needed to reach the target tier", gt=0)
    tier_rate: float = Field(..., description="Rebate rate at that tier, as a fraction", ge=0, le=1)
    net_invoice_price_per_unit: float = Field(..., description="Net invoice price per clinical unit", ge=0)
    rebate_breakage_rate: Optional[float] = Field(
        default=None, description="Historic breakage rate (D-18). Required", ge=0, le=1
    )
    collected_to_date: float = Field(
        default=0.0, description="Rebate actually invoiced or credited so far, from the ledger", ge=0
    )
    months_in_period: int = Field(default=12, description="Length of the rebate period in months", ge=1, le=24)
    currency: str = Field(default="IDR", max_length=8)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="negotiation_rebate_realization",
    annotations={
        "title": "Rebate Realization Tracker",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def negotiation_rebate_realization(params: RealizationInput) -> str:
    """Track a signed rebate from accrual to cash, and surface what has not been collected.

    Closes the loop the catalogue never closes: no process in the twelve documented
    workflows tracks rebate realization, so measured breakage has never fed back
    into the next cycle's rebate model. A rebate negotiated and never collected is
    a discount given away for free.

    Args:
        params (RealizationInput): monthly_units, tier_target_units, tier_rate,
            net_invoice_price_per_unit, rebate_breakage_rate, collected_to_date

    Returns:
        str: markdown, or JSON with schema:
        {"tier_target_units": float, "cumulative_units": float, "period_elapsed": float,
         "tier_achieved_pct": float, "pace_index": float, "status": str,
         "accrued_rebate": float, "collected_rebate": float, "breakage_to_date": float,
         "projected_year_end_units": float, "tier_will_be_met": bool, "message": str}

    Examples:
        - "Are we going to hit the tier this year?" -> pace_index and tier_will_be_met
        - "How much rebate have we earned but not been paid?" -> breakage_to_date

    Error Handling:
        - Missing rebate_breakage_rate returns an error naming data element D-18.
    """
    try:
        result = E.rebate_realization(
            monthly_units=params.monthly_units,
            tier_target_units=params.tier_target_units,
            tier_rate=params.tier_rate,
            net_invoice_price_per_unit=params.net_invoice_price_per_unit,
            breakage_rate=params.rebate_breakage_rate,
            collected_to_date=params.collected_to_date,
            months_in_period=params.months_in_period,
        )
        return _respond(
            params.response_format, realization_markdown(result, params.currency), result
        )
    except Exception as e:  # noqa: BLE001
        return _error(e)


class ReadinessInput(StrictModel):
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="negotiation_data_readiness",
    annotations={
        "title": "Which Data Is Still Blocking",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def negotiation_data_readiness(params: ReadinessInput) -> str:
    """List the data elements the engine needs that have no source system yet.

    The engine deliberately refuses to substitute defaults for these, because a
    plausible-looking wrong number is worse than no number. Call this to see what
    to go and get, and from whom.

    Args:
        params (ReadinessInput): response_format only

    Returns:
        str: markdown list, or JSON: {"blocking": [{"key": str, "register": str}],
             "count": int, "note": str, "price_book": {...status, or "error"}}
    """
    items = [{"key": k, "register": v} for k, v in E.BLOCKING_DATA_ELEMENTS.items()]
    try:
        price_book: dict = get_book().status()
    except Exception as e:  # noqa: BLE001
        price_book = {"error": str(e)}
    payload = {
        "blocking": items,
        "count": len(items),
        "note": (
            "These are the blocking elements the engine touches directly. The full "
            "register holds 32 elements, 9 of them blocking."
        ),
        "price_book": price_book,
    }
    if "error" in price_book:
        book_lines = [f"- Not loaded: {price_book['error']}"]
    else:
        book_lines = [f"- `{k}`: {v}" for k, v in price_book.items()]
        if price_book["is_sample"]:
            book_lines.insert(0, f"- **{SAMPLE_BANNER}**")
    md = "\n".join(
        ["# Blocking data elements", ""]
        + [f"- `{i['key']}` — {i['register']}" for i in items]
        + ["", payload["note"], "", "## Price book", ""]
        + book_lines
    )
    return _respond(params.response_format, md, payload)


# ==========================================================================
# Price intelligence — reads the price book, never writes it
# ==========================================================================

def get_book() -> PriceBook:
    return cached_book()


READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

AS_OF_FIELD = Field(
    default=None,
    description="Date to evaluate at (YYYY-MM-DD). Defaults to the latest date in the price book",
)


class LookupInput(StrictModel):
    search: Optional[str] = Field(
        default=None, description="SKU code or part of the product name, e.g. 'ceftriaxone' or 'IVC22'", max_length=200
    )
    vendor: Optional[str] = Field(default=None, description="Vendor name or fragment", max_length=200)
    hospital: Optional[str] = Field(default=None, description="Siloam site name or fragment", max_length=200)
    since: Optional[date] = Field(default=None, description="Only prices on or after this date")
    sources: Optional[list[Literal["po", "contract", "quote"]]] = Field(
        default=None, description="Restrict to purchase orders, contracts and/or quotes"
    )
    limit: int = Field(default=50, ge=1, le=500)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(name="negotiation_price_lookup", annotations={"title": "Look Up Prices Paid and Quoted", **READ_ONLY})
async def negotiation_price_lookup(params: LookupInput) -> str:
    """What has Siloam paid or been quoted for a product, by whom and where?

    Searches the price book and summarises each SKU/vendor pair: latest price per
    pack, per clinical unit, best price seen, and which sites buy it. Any vendor, any
    SKU — leave the search empty and give a vendor to list everything that vendor sells.

    Returns:
        str: markdown table, or JSON {"matches": int, "rows": [...], "truncated": bool}

    Examples:
        - "What do we pay for ceftriaxone?" -> search="ceftriaxone"
        - "Everything we buy from Medisindo" -> vendor="Medisindo"
    """
    try:
        res = I.lookup(get_book(), params.search, params.vendor, params.hospital, params.since,
                       params.sources, params.limit)
        return _respond(params.response_format, lookup_markdown(res), res)
    except Exception as e:  # noqa: BLE001
        return _error(e)


class BenchmarkInput(StrictModel):
    sku: str = Field(..., description="SKU code or a unique part of the product name", min_length=1, max_length=200)
    lookback_months: int = Field(default=24, ge=3, le=120, description="How far back vendor bests reach")
    as_of: Optional[date] = AS_OF_FIELD
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(name="negotiation_benchmark", annotations={"title": "Benchmark One SKU", **READ_ONLY})
async def negotiation_benchmark(params: BenchmarkInput) -> str:
    """Everything known about one SKU's price: best ever, best today, by vendor, by site.

    Includes internal price variance (what sites pay above the best Siloam site) and
    the equivalent SKUs from competing vendors. Prices are per clinical unit and,
    when a price index is loaded, in today's money.

    Examples:
        - "How does our troponin price compare?" -> sku="troponin" (if unique) or a code
        - "Which site pays most for IV cannulas?" -> read by_hospital
    """
    try:
        res = I.benchmark(get_book(), params.sku, params.as_of, params.lookback_months)
        return _respond(params.response_format, benchmark_markdown(res), res)
    except Exception as e:  # noqa: BLE001
        return _error(e)


class TargetsInput(StrictModel):
    sku: str = Field(..., description="SKU code or unique product-name fragment", min_length=1, max_length=200)
    vendor: str = Field(..., description="Vendor being negotiated with", min_length=1, max_length=200)
    beat_margin: float = Field(
        default=0.01, ge=0, lt=0.5, description="How far below the best reference the target sits (0.01 = 1%)"
    )
    anchor_margin: float = Field(
        default=0.05, ge=0, lt=0.5, description="How far below the target to open (0.05 = 5%)"
    )
    annual_volume: Optional[float] = Field(
        default=None, gt=0, description="Clinical units a year. Defaults to the group's last 12 months"
    )
    as_of: Optional[date] = AS_OF_FIELD
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(name="negotiation_recommend_targets", annotations={"title": "Recommend Target, Opening Ask, Walk-Away", **READ_ONLY})
async def negotiation_recommend_targets(params: TargetsInput) -> str:
    """The price to aim for: beats every historical, internal and competitor price.

    Builds the evidence ladder (best ever, best Siloam site today, the vendor's own
    best, the best competing vendor on an equivalent SKU), sets the target
    beat_margin below the lowest rung and the opening ask below that. The walk-away
    is PROPOSED only (D-21): it needs sign-off by someone other than the negotiator.

    Examples:
        - "What price should we get for IV cannula 22G from Prima?"
        - "What's our opening ask for the stent renewal?"
    """
    try:
        res = I.recommend_targets(get_book(), params.sku, params.vendor, params.as_of, params.beat_margin,
                                  params.anchor_margin, annual_volume=params.annual_volume)
        return _respond(params.response_format, targets_markdown(res), res)
    except Exception as e:  # noqa: BLE001
        return _error(e)


class BeatCheckInput(StrictModel):
    sku: str = Field(..., description="SKU code or unique product-name fragment", min_length=1, max_length=200)
    vendor: Optional[str] = Field(default=None, description="Vendor making the offer, adds its own history", max_length=200)
    offered_price_per_clinical_unit: Optional[float] = Field(
        default=None, gt=0, description="Net invoice price per clinical unit. Give this OR offer"
    )
    offer: Optional[OfferInput] = Field(
        default=None, description="Full offer; its net invoice price per clinical unit is checked"
    )
    as_of: Optional[date] = AS_OF_FIELD
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(name="negotiation_beat_check", annotations={"title": "Does This Price Beat Everything?", **READ_ONLY})
async def negotiation_beat_check(params: BeatCheckInput) -> str:
    """Check a quoted price against every reference: history, Siloam sites and competitors.

    Returns BEATS_ALL, BEATS_SOME or BEATS_NONE, the gap to each reference, and the
    price that would beat all of them. Quote in packs? Pass the full offer so the
    pack size is normalised.

    Examples:
        - "Prima quoted 520,000 a box of 50 — is that good?" -> offer with clinical_units_per_quoted_unit=50
    """
    try:
        if (params.offer is None) == (params.offered_price_per_clinical_unit is None):
            raise E.EngineError("Give exactly one of offered_price_per_clinical_unit or offer")
        price = (params.offer.to_engine().invoice_price_per_clinical_unit if params.offer
                 else params.offered_price_per_clinical_unit)
        vendor = params.vendor or (params.offer.vendor if params.offer else None)
        res = I.beat_check(get_book(), params.sku, price, vendor, params.as_of)
        return _respond(params.response_format, beat_markdown(res), res)
    except Exception as e:  # noqa: BLE001
        return _error(e)


class CounterInput(StrictModel):
    offer: OfferInput = Field(..., description="The vendor's current offer")
    target_enuc: Optional[float] = Field(default=None, gt=0, description="Target ENUC per usable unit")
    target_price_per_clinical_unit: Optional[float] = Field(
        default=None, gt=0,
        description="Target net invoice price per clinical unit (e.g. from negotiation_recommend_targets); "
                    "translated to ENUC on this offer's other terms",
    )
    max_terms_days: int = Field(default=90, ge=0, le=365, description="Longest payment terms you'd accept")
    max_free_goods_ratio: float = Field(default=0.25, ge=0, le=2, description="Most bonus stock you can use before expiry")
    parameters: ParametersInput = Field(default_factory=ParametersInput)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(name="negotiation_counter_offer", annotations={"title": "Build the Counter-Offer", **READ_ONLY})
async def negotiation_counter_offer(params: CounterInput) -> str:
    """How much of each lever closes the gap to target, alone and as packages.

    Solves discount, payment terms and bonus stock one at a time through the ENUC
    kernel, then builds packages: rebate-to-invoice conversion first (cheap for the
    vendor, valuable to Siloam), terms, bonus stock, then price only.

    Error Handling:
        - Give exactly one of target_enuc or target_price_per_clinical_unit.
        - Rebates need rebate_breakage_rate (D-18).
    """
    try:
        if (params.target_enuc is None) == (params.target_price_per_clinical_unit is None):
            raise E.EngineError("Give exactly one of target_enuc or target_price_per_clinical_unit")
        offer, p = params.offer.to_engine(), params.parameters.to_engine()
        target = params.target_enuc or I.enuc_at_invoice_price(offer, p, params.target_price_per_clinical_unit)
        res = I.counter_offer(offer, target, p, params.max_terms_days, params.max_free_goods_ratio)
        return _respond(params.response_format, counter_markdown(res), res)
    except Exception as e:  # noqa: BLE001
        return _error(e)


class BriefInput(StrictModel):
    sku: str = Field(..., description="SKU code or unique product-name fragment", min_length=1, max_length=200)
    vendor: str = Field(..., description="Vendor being negotiated with", min_length=1, max_length=200)
    offer: Optional[OfferInput] = Field(
        default=None, description="The vendor's current offer, if there is one. Enables counter-offer and verdict"
    )
    parameters: ParametersInput = Field(default_factory=ParametersInput)
    beat_margin: float = Field(default=0.01, ge=0, lt=0.5)
    anchor_margin: float = Field(default=0.05, ge=0, lt=0.5)
    reservation_approved_by: Optional[str] = Field(
        default=None, max_length=200,
        description="Name of the person (not the negotiator) who signed off the proposed walk-away (D-21). "
                    "Without it no ACCEPT/PUSH/WALK verdict is given",
    )
    as_of: Optional[date] = AS_OF_FIELD
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(name="negotiation_brief", annotations={"title": "Negotiation Brief — Best Achievable Price", **READ_ONLY})
async def negotiation_brief(params: BriefInput) -> str:
    """One call answers "what's the best price we can get for X from vendor Y?".

    Combines benchmark, target / opening ask / proposed walk-away, a beat-check of
    the current offer (or today's price), the counter-offer levers and — once the
    walk-away is signed off — the ACCEPT / PUSH / WALK verdict.

    Use this first for any price question about a specific SKU and vendor.

    Examples:
        - "Best price for troponin from Global Diagnostika?" -> sku, vendor
        - "Sehat offered 880,000 per box of 100 cannulas, 10% off, 30 days. Counter?" -> add offer
    """
    try:
        offer = params.offer.to_engine() if params.offer else None
        res = I.negotiation_brief(get_book(), params.sku, params.vendor, params.parameters.to_engine(), offer,
                                  params.as_of, params.beat_margin, params.anchor_margin,
                                  params.reservation_approved_by)
        return _respond(params.response_format, brief_markdown(res), res)
    except Exception as e:  # noqa: BLE001
        return _error(e)


class PortfolioInput(StrictModel):
    top_n: int = Field(default=10, ge=1, le=500, description="How many SKUs to return")
    as_of: Optional[date] = AS_OF_FIELD
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(name="negotiation_savings_opportunities", annotations={"title": "Where to Negotiate First", **READ_ONLY})
async def negotiation_savings_opportunities(params: PortfolioInput) -> str:
    """Rank SKUs by spend above the best available price (best site or best equivalent).

    Examples:
        - "Where are we overpaying the most?"
        - "Top 20 renegotiation targets this quarter" -> top_n=20
    """
    try:
        book = get_book()
        rows = I.savings_opportunities(book, params.as_of, params.top_n)
        return _respond(params.response_format, savings_markdown(rows, book.is_sample, book.currency),
                        {"is_sample": book.is_sample, "rows": rows})
    except Exception as e:  # noqa: BLE001
        return _error(e)


class AlertsInput(StrictModel):
    parameters: ParametersInput = Field(
        default_factory=ParametersInput, description="Needed for rebate alerts (rebate_breakage_rate, D-18)"
    )
    creep_threshold: float = Field(default=0.03, ge=0, le=1, description="Price rise above the index that triggers an alert")
    variance_threshold: float = Field(default=0.05, ge=0, le=1, description="Site premium over the best site that triggers an alert")
    renewal_days: int = Field(default=120, ge=1, le=730, description="Look-ahead window for contract renewals")
    severity: Optional[Literal["high", "medium", "low"]] = Field(default=None, description="Only this severity")
    limit: int = Field(default=30, ge=1, le=500)
    as_of: Optional[date] = AS_OF_FIELD
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(name="negotiation_alerts", annotations={"title": "Savings Alerts", **READ_ONLY})
async def negotiation_alerts(params: AlertsInput) -> str:
    """Price creep, sites paying above the group's best, quotes above history,
    contracts up for renewal and rebate tiers at risk — ranked by severity and value.

    Examples:
        - "Anything I should act on this week?"
        - "Which contracts renew in the next 60 days?" -> renewal_days=60
    """
    try:
        book = get_book()
        rows = I.alerts(book, params.as_of, params.parameters.to_engine(), params.creep_threshold,
                        params.variance_threshold, params.renewal_days)
        if params.severity:
            rows = [a for a in rows if a["severity"] == params.severity]
        rows = rows[: params.limit]
        return _respond(params.response_format, alerts_markdown(rows, book.is_sample, book.currency),
                        {"is_sample": book.is_sample, "count": len(rows), "alerts": rows})
    except Exception as e:  # noqa: BLE001
        return _error(e)


class VendorSpendInput(StrictModel):
    as_of: Optional[date] = AS_OF_FIELD
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(name="negotiation_vendor_spend", annotations={"title": "Spend by Vendor", **READ_ONLY})
async def negotiation_vendor_spend(params: VendorSpendInput) -> str:
    """Spend, share of wallet and growth by vendor over the last 12 months.

    Examples:
        - "Who are our biggest suppliers?" / "How much do we spend with Sehat Medika?"
    """
    try:
        book = get_book()
        rows = I.vendor_spend(book, params.as_of)
        return _respond(params.response_format, vendor_spend_markdown(rows, book.is_sample, book.currency),
                        {"is_sample": book.is_sample, "rows": rows})
    except Exception as e:  # noqa: BLE001
        return _error(e)


# ==========================================================================
# Resources
# ==========================================================================


@mcp.resource("negotiation://parameters/default")
async def default_parameters() -> str:
    """The default parameter set, with placeholders marked."""
    return to_json(
        {
            "wacc": 0.12,
            "baseline_payment_days": 30,
            "contract_years": 3.0,
            "rebate_breakage_rate": None,
            "rebate_collection_lag_months": 6.0,
            "tax_efficiency_off_invoice": 0.88,
            "currency": "IDR",
            "placeholders": {
                "wacc": "Confirm with Treasury (D-17)",
                "rebate_breakage_rate": "No default. Measure from AP history (D-18)",
                "tax_efficiency_off_invoice": "PLACEHOLDER. Confirm with Tax (D-19)",
            },
        }
    )


@mcp.resource("negotiation://schema/offer")
async def offer_schema() -> str:
    """JSON schema for the canonical offer object."""
    return to_json(OfferInput.model_json_schema())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
