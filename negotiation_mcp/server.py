#!/usr/bin/env python3
"""MCP server for the Siloam procurement negotiation engine.

Exposes the decision layer that the Blueprint AI process catalogue describes but
does not have: Principal Contract Negotiation steps 18 and 20, and Consumables
Seasonal Price Increase step 8, all branch on thresholds that the catalogue's own
gap lists say do not exist.

Every tool is a pure calculation — no external API, no database, no writes. Data
arrives as parameters, which is deliberate: nine data elements in the register are
still blocking, and the engine refuses to invent them. A tool that needs a missing
element fails with the element's register ID and its owner rather than substituting
a plausible default.

Transport: stdio. Run with ``python -m negotiation_mcp.server``.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import engine as E
from .formatting import (
    ResponseFormat,
    binding_markdown,
    comparison_markdown,
    enuc_markdown,
    realization_markdown,
    to_json,
    verdict_markdown,
)

mcp = FastMCP("negotiation_mcp")

SERVER_VERSION = "0.1.0"


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
             "count": int, "note": str}
    """
    items = [{"key": k, "register": v} for k, v in E.BLOCKING_DATA_ELEMENTS.items()]
    payload = {
        "blocking": items,
        "count": len(items),
        "note": (
            "These are the blocking elements the engine touches directly. The full "
            "register holds 32 elements, 9 of them blocking."
        ),
    }
    md = "\n".join(
        ["# Blocking data elements", ""]
        + [f"- `{i['key']}` — {i['register']}" for i in items]
        + ["", payload["note"]]
    )
    return _respond(params.response_format, md, payload)


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
