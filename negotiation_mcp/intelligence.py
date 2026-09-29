"""Price intelligence: what to ask for, and why it is the best price available.

Pure and deterministic, like ``engine.py``: every function takes a ``PriceBook``
and returns plain data. No MCP, no network, no writes.

Every price here is a **net invoice price per clinical unit**. That is the price
history's basis: purchase orders record the invoice, not the rebate, the placed
analyser or the payment terms. ENUC remains the award basis. When a full offer
is known, ``counter_offer`` and ``negotiation_brief`` translate the invoice-price
target into ENUC through ``engine.compute_enuc``.

Where the targets come from
---------------------------
The *reference ladder* for one SKU and one vendor collects the best evidence:

* the best price Siloam has ever paid or been quoted for the SKU (index-adjusted);
* the best price any Siloam hospital pays today (internal price variance);
* this vendor's own best historical price;
* the best competing vendor's price on a clinically equivalent SKU.

The **target** sits ``beat_margin`` below the lowest rung, so a deal at target beats
every historical price and every competitor. The **opening ask** anchors below
that. The **walk-away** is only *proposed*: data element D-21 has to be signed off
by someone other than the negotiator, so the engine never uses it for a verdict
unless an approver is named.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Callable, Sequence

from . import engine as E
from .engine import EngineError
from .numfmt import num, pct, rp
from .pricebook import PriceBook, PriceObservation, SkuInfo, add_months, month_key

PRICE_BASIS = (
    "Net invoice price per clinical unit (after on-invoice discount, before rebate, "
    "terms, placement and service). This is what the purchase history records. "
    "ENUC stays the award basis; see counter_offer for the ENUC translation."
)
SIGN_OFF_STATUS = "PROPOSED — requires sign-off (D-21)"
PAID_SOURCES = ("po", "contract")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise EngineError("No prices to take a percentile of")
    k = (len(ordered) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _weighted_price(book: PriceBook, obs: Sequence[PriceObservation], as_of: date) -> float | None:
    units = sum(o.clinical_units for o in obs)
    if not units:
        return None
    return sum(book.adjusted_unit_price(o, as_of)[0] * o.clinical_units for o in obs) / units


@dataclass(frozen=True)
class Reference:
    """One rung of evidence: a real price someone paid or offered, in ``as_of`` money."""

    kind: str
    label: str
    price: float
    nominal_price: float
    date: str | None
    vendor: str | None
    hospital: str | None
    sku: str | None
    source: str | None
    inflation_adjusted: bool


def _ref_from_obs(book: PriceBook, o: PriceObservation, as_of: date, kind: str, label: str) -> Reference:
    price, adjusted = book.adjusted_unit_price(o, as_of)
    return Reference(
        kind=kind,
        label=label,
        price=price,
        nominal_price=o.net_price_per_clinical_unit,
        date=o.date.isoformat(),
        vendor=o.vendor,
        hospital=o.hospital,
        sku=o.sku,
        source=o.source,
        inflation_adjusted=adjusted,
    )


def _best(book: PriceBook, obs: Sequence[PriceObservation], as_of: date, kind: str, label: str) -> Reference | None:
    if not obs:
        return None
    o = min(obs, key=lambda x: book.adjusted_unit_price(x, as_of)[0])
    return _ref_from_obs(book, o, as_of, kind, label)


def _window(as_of: date, months: int) -> tuple[date, date]:
    return add_months(as_of, -months), as_of


# --------------------------------------------------------------------------
# Benchmark
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkResult:
    sku: str
    sku_name: str
    equivalence_group: str
    clinical_unit: str
    single_source: bool | None
    as_of: str
    currency: str
    price_basis: str
    inflation_adjusted: bool
    observations: int
    best_ever: Reference
    best_12m: Reference | None
    p25_12m: float | None
    median_12m: float | None
    weighted_avg_paid_12m: float | None
    annual_volume_12m: float
    annual_spend_12m: float
    internal_best: Reference | None
    internal_price_variance_12m: float
    group_best: Reference | None
    by_vendor: list[dict]
    by_hospital: list[dict]
    by_group_sku: list[dict]
    is_sample: bool


def _hospital_prices(book: PriceBook, po: Sequence[PriceObservation], as_of: date) -> list[dict]:
    by_site: dict[str, list[PriceObservation]] = {}
    for o in po:
        by_site.setdefault(o.hospital, []).append(o)
    rows = []
    for site, obs in by_site.items():
        rows.append(
            {
                "hospital": site,
                "weighted_price": _weighted_price(book, obs, as_of),
                "volume": sum(o.clinical_units for o in obs),
                "spend": sum(o.spend for o in obs),
                "vendors": sorted({o.vendor for o in obs}),
            }
        )
    rows.sort(key=lambda r: r["weighted_price"])
    best = rows[0]["weighted_price"] if rows else None
    for r in rows:
        r["premium_vs_internal_best"] = (r["weighted_price"] / best - 1.0) if best else 0.0
        r["excess_spend_vs_internal_best"] = (r["weighted_price"] - best) * r["volume"] if best else 0.0
    return rows


def benchmark(
    book: PriceBook,
    sku_query: str,
    as_of: date | None = None,
    lookback_months: int = 24,
    vendor_hint: str | None = None,
) -> BenchmarkResult:
    """Everything known about the price of one SKU: best, typical, by vendor, by site."""
    info = book.resolve_sku(sku_query, vendor_hint)
    as_of = book.resolve_as_of(as_of)
    since_12, _ = _window(as_of, 12)
    since_lb, _ = _window(as_of, lookback_months)

    all_obs = book.query(skus=[info.sku], until=as_of)
    if not all_obs:
        raise EngineError(f"No price history for {info.sku} on or before {as_of.isoformat()}")
    recent = [o for o in all_obs if o.date >= since_12]
    recent_po = [o for o in recent if o.source == "po"]

    recent_prices = [book.adjusted_unit_price(o, as_of)[0] for o in recent]

    by_vendor = []
    for vendor in sorted({o.vendor for o in all_obs}):
        v_obs = [o for o in all_obs if o.vendor == vendor and o.date >= since_lb]
        v_po = [o for o in recent_po if o.vendor == vendor]
        latest = max((o for o in all_obs if o.vendor == vendor), key=lambda o: o.date)
        best = _best(book, v_obs, as_of, "vendor_best", f"{vendor} best")
        by_vendor.append(
            {
                "vendor": vendor,
                "best_price": best.price if best else None,
                "best_date": best.date if best else None,
                "latest_price": latest.net_price_per_clinical_unit,
                "latest_date": latest.date.isoformat(),
                "latest_source": latest.source,
                "weighted_price_12m": _weighted_price(book, v_po, as_of),
                "volume_12m": sum(o.clinical_units for o in v_po),
                "spend_12m": sum(o.spend for o in v_po),
            }
        )
    by_vendor.sort(key=lambda r: r["best_price"] if r["best_price"] is not None else math.inf)

    by_hospital = _hospital_prices(book, recent_po, as_of)
    internal_best = None
    if by_hospital:
        top = by_hospital[0]
        internal_best = Reference(
            kind="internal_best",
            label=f"Best Siloam site today ({top['hospital']}, 12-month volume-weighted)",
            price=top["weighted_price"],
            nominal_price=top["weighted_price"],
            date=None,
            vendor=", ".join(top["vendors"]),
            hospital=top["hospital"],
            sku=info.sku,
            source="po",
            inflation_adjusted=bool(book.price_index),
        )

    group_rows = []
    group_recent: list[PriceObservation] = []
    for s in book.group_skus(info.equivalence_group):
        s_obs = book.query(skus=[s.sku], since=since_12, until=as_of)
        group_recent += s_obs
        b = _best(book, s_obs, as_of, "group_sku_best", s.sku)
        group_rows.append(
            {
                "sku": s.sku,
                "sku_name": s.sku_name,
                "vendors": sorted({o.vendor for o in s_obs}),
                "best_price_12m": b.price if b else None,
                "weighted_price_12m": _weighted_price(book, [o for o in s_obs if o.source == "po"], as_of),
            }
        )
    group_rows.sort(key=lambda r: r["best_price_12m"] if r["best_price_12m"] is not None else math.inf)

    best_ever = _best(book, all_obs, as_of, "best_ever", "Best price ever paid or quoted for this SKU")
    assert best_ever is not None
    return BenchmarkResult(
        sku=info.sku,
        sku_name=info.sku_name,
        equivalence_group=info.equivalence_group,
        clinical_unit=info.clinical_unit,
        single_source=info.single_source,
        as_of=as_of.isoformat(),
        currency=book.currency,
        price_basis=PRICE_BASIS,
        inflation_adjusted=bool(book.price_index),
        observations=len(all_obs),
        best_ever=best_ever,
        best_12m=_best(book, recent, as_of, "best_12m", "Best price in the last 12 months"),
        p25_12m=_percentile(recent_prices, 0.25) if recent_prices else None,
        median_12m=_percentile(recent_prices, 0.5) if recent_prices else None,
        weighted_avg_paid_12m=_weighted_price(book, recent_po, as_of),
        annual_volume_12m=sum(o.clinical_units for o in recent_po),
        annual_spend_12m=sum(o.spend for o in recent_po),
        internal_best=internal_best,
        internal_price_variance_12m=sum(r["excess_spend_vs_internal_best"] for r in by_hospital),
        group_best=_best(
            book, group_recent, as_of, "group_best", "Best price in the equivalence group, last 12 months"
        ),
        by_vendor=by_vendor,
        by_hospital=by_hospital,
        by_group_sku=group_rows,
        is_sample=book.is_sample,
    )


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetRecommendation:
    sku: str
    sku_name: str
    vendor: str
    as_of: str
    currency: str
    price_basis: str
    references: list[Reference]
    lowest_reference: Reference
    beat_margin: float
    anchor_margin: float
    target_price: float
    opening_ask: float
    proposed_walk_away: float
    walk_away_basis: str
    walk_away_status: str
    incumbent_current_price: float | None
    vendor_clinical_units_per_quoted_unit: float
    vendor_quoted_unit: str
    target_per_quoted_unit: float
    opening_ask_per_quoted_unit: float
    annual_volume: float
    annual_saving_at_target: float | None
    competing_vendors: list[str]
    single_source: bool | None
    leverage: list[str]
    caveats: list[str]
    is_sample: bool


def _vendor_pack(book: PriceBook, info: SkuInfo, vendor: str) -> tuple[str, float]:
    """The unit this vendor quotes the SKU in, so targets can be said in their language."""
    for vendor_only in (True, False):
        obs = [o for o in book.query(skus=[info.sku]) if not vendor_only or o.vendor == vendor]
        if obs:
            latest = max(obs, key=lambda o: o.date)
            return latest.uom.quoted_unit, latest.uom.clinical_units_per_quoted_unit
    return info.clinical_unit, 1.0


def recommend_targets(
    book: PriceBook,
    sku_query: str,
    vendor_query: str,
    as_of: date | None = None,
    beat_margin: float = 0.01,
    anchor_margin: float = 0.05,
    lookback_months: int = 24,
    annual_volume: float | None = None,
) -> TargetRecommendation:
    """Target, opening ask and proposed walk-away for one SKU from one vendor."""
    if not 0 <= beat_margin < 0.5:
        raise EngineError("beat_margin must be between 0 and 0.5")
    if not 0 <= anchor_margin < 0.5:
        raise EngineError("anchor_margin must be between 0 and 0.5")

    bm = benchmark(book, sku_query, as_of, lookback_months, vendor_hint=vendor_query)
    info = book.skus[bm.sku]
    vendor = book.resolve_vendor(vendor_query)
    as_of_d = date.fromisoformat(bm.as_of)
    since_12, _ = _window(as_of_d, 12)
    since_lb, _ = _window(as_of_d, lookback_months)

    refs: list[Reference] = [bm.best_ever]
    if bm.internal_best:
        refs.append(bm.internal_best)
    own_obs = [o for o in book.query(skus=[info.sku], since=since_lb, until=as_of_d) if o.vendor == vendor]
    own = _best(
        book, own_obs, as_of_d, "vendor_own_best",
        f"{vendor}'s own best price on this SKU, last {lookback_months} months",
    )
    if own:
        refs.append(own)

    group_skus = [s.sku for s in book.group_skus(info.equivalence_group)]
    competitor_obs = [
        o for o in book.query(skus=group_skus, since=since_12, until=as_of_d) if o.vendor != vendor
    ]
    competitor = _best(
        book,
        competitor_obs,
        as_of_d,
        "competitor_best",
        "Best competing vendor on a clinically equivalent SKU, last 12 months",
    )
    if competitor:
        refs.append(competitor)
    refs.sort(key=lambda r: r.price)

    lowest = refs[0]
    target = lowest.price * (1.0 - beat_margin)
    opening = target * (1.0 - anchor_margin)

    incumbent_po = [
        o for o in book.query(skus=[info.sku], since=since_12, until=as_of_d, sources=["po"]) if o.vendor == vendor
    ]
    incumbent = _weighted_price(book, incumbent_po, as_of_d)
    if incumbent is not None:
        walk, walk_basis = incumbent, "What Siloam pays this vendor today (12-month volume-weighted)"
    elif bm.median_12m is not None:
        walk, walk_basis = bm.median_12m, "Median price for this SKU over the last 12 months"
    else:
        walk, walk_basis = bm.best_ever.price * 1.05, "Best-ever price plus 5% (thin history)"
    if walk < target:
        walk, walk_basis = target, walk_basis + ", floored at the target"

    volume = annual_volume if annual_volume is not None else bm.annual_volume_12m
    reference_now = incumbent if incumbent is not None else bm.weighted_avg_paid_12m
    saving = (reference_now - target) * volume if reference_now is not None else None

    competing = sorted({o.vendor for o in competitor_obs})
    leverage: list[str] = []
    if bm.annual_volume_12m:
        biggest_site = max((r["volume"] for r in bm.by_hospital), default=0.0)
        if biggest_site and bm.annual_volume_12m > biggest_site * 1.2:
            leverage.append(
                f"Consolidated group volume is {num(bm.annual_volume_12m)} {info.clinical_unit}s a year, "
                f"{bm.annual_volume_12m / biggest_site:.1f}x the largest single site. Offer the "
                "group volume in exchange for the target price; don't give it away free."
            )
    if competing:
        leverage.append(
            f"{len(competing)} competing vendor(s) supply an equivalent product: {', '.join(competing)}. "
            "Name the competitor's price, not the competitor."
        )
    if bm.internal_best and bm.by_hospital and len(bm.by_hospital) > 1:
        worst = bm.by_hospital[-1]
        leverage.append(
            f"Internal price variance: {worst['hospital']} pays {pct(worst['premium_vs_internal_best'], 1)} more "
            f"than {bm.internal_best.hospital}. Ask for one group price at the best site's level."
        )
    creep = _vendor_price_change(book, info.sku, vendor, as_of_d)
    if creep is not None and creep["excess_over_index"] > 0.02:
        leverage.append(
            f"{vendor} raised this price {pct(creep['nominal_change'], 1)} in a year against an index move of "
            f"{pct(creep['index_change'], 1)}. Ask them to justify the {pct(creep['excess_over_index'], 1)} excess."
        )

    caveats = [PRICE_BASIS]
    if info.single_source:
        caveats.append(
            "Single-source SKU (D-23): there is no credible walk-away. Treat the walk-away as an "
            "escalation point, and lean on volume, rebate realization and internal variance."
        )
    if not book.price_index:
        caveats.append("No price index loaded: historical prices are compared in nominal money.")
    if lowest.source == "quote":
        caveats.append(
            "The lowest reference is a quote, not a price paid. Confirm the quote is still valid "
            "and on comparable terms before anchoring on it."
        )

    q_unit, cu = _vendor_pack(book, info, vendor)
    return TargetRecommendation(
        sku=info.sku,
        sku_name=info.sku_name,
        vendor=vendor,
        as_of=bm.as_of,
        currency=book.currency,
        price_basis=PRICE_BASIS,
        references=refs,
        lowest_reference=lowest,
        beat_margin=beat_margin,
        anchor_margin=anchor_margin,
        target_price=target,
        opening_ask=opening,
        proposed_walk_away=walk,
        walk_away_basis=walk_basis,
        walk_away_status=SIGN_OFF_STATUS,
        incumbent_current_price=incumbent,
        vendor_clinical_units_per_quoted_unit=cu,
        vendor_quoted_unit=q_unit,
        target_per_quoted_unit=target * cu,
        opening_ask_per_quoted_unit=opening * cu,
        annual_volume=volume,
        annual_saving_at_target=saving,
        competing_vendors=competing,
        single_source=info.single_source,
        leverage=leverage,
        caveats=caveats,
        is_sample=book.is_sample,
    )


# --------------------------------------------------------------------------
# Beat check
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BeatCheckResult:
    sku: str
    vendor: str | None
    offered_price: float
    status: str  # BEATS_ALL | BEATS_SOME | BEATS_NONE
    price_to_beat_all: float
    checks: list[dict]
    headline: str
    price_basis: str
    currency: str
    is_sample: bool


def beat_check(
    book: PriceBook,
    sku_query: str,
    offered_price_per_clinical_unit: float,
    vendor_query: str | None = None,
    as_of: date | None = None,
) -> BeatCheckResult:
    """Is this price better than everything Siloam has seen — history, sites and competitors?"""
    if offered_price_per_clinical_unit <= 0:
        raise EngineError("offered_price_per_clinical_unit must be positive")
    bm = benchmark(book, sku_query, as_of, vendor_hint=vendor_query)
    refs: list[Reference] = []
    if vendor_query:
        rec = recommend_targets(book, bm.sku, vendor_query, as_of)
        refs = list(rec.references)
        vendor = rec.vendor
    else:
        vendor = None
        refs = [r for r in (bm.best_ever, bm.internal_best, bm.group_best) if r]
    if bm.best_12m and all(r.kind != "best_12m" for r in refs):
        refs.append(bm.best_12m)
    if bm.median_12m is not None:
        refs.append(
            Reference("median_12m", "Median price, last 12 months", bm.median_12m, bm.median_12m,
                      None, None, None, bm.sku, None, bm.inflation_adjusted)
        )

    checks = []
    for r in sorted(refs, key=lambda x: x.price):
        gap = offered_price_per_clinical_unit - r.price
        checks.append(
            {
                "reference": r.label,
                "kind": r.kind,
                "reference_price": r.price,
                "vendor": r.vendor,
                "hospital": r.hospital,
                "date": r.date,
                "beats": gap < 0,
                "gap_per_unit": gap,
                "gap_pct": gap / r.price if r.price else 0.0,
            }
        )
    beaten = sum(c["beats"] for c in checks)
    status = "BEATS_ALL" if beaten == len(checks) else "BEATS_NONE" if beaten == 0 else "BEATS_SOME"
    price_to_beat = min(c["reference_price"] for c in checks)
    if status == "BEATS_ALL":
        headline = "Beats every historical, internal and competitor reference."
    else:
        failed = [c for c in checks if not c["beats"]]
        headline = (
            f"Does not beat {len(failed)} of {len(checks)} references. It must fall below "
            f"{rp(price_to_beat)} per clinical unit to beat all of them."
        )
    return BeatCheckResult(
        sku=bm.sku,
        vendor=vendor,
        offered_price=offered_price_per_clinical_unit,
        status=status,
        price_to_beat_all=price_to_beat,
        checks=checks,
        headline=headline,
        price_basis=PRICE_BASIS,
        currency=book.currency,
        is_sample=book.is_sample,
    )


# --------------------------------------------------------------------------
# Counter-offer
# --------------------------------------------------------------------------


def enuc_at_invoice_price(offer: E.Offer, params: E.Parameters, net_price_per_clinical_unit: float) -> float:
    """ENUC this offer would have if its invoice price per clinical unit were the given price.

    Every other term (rebate, terms, placement, service) stays as offered, so this is
    the like-for-like translation of an invoice-price target into ENUC.
    """
    cu = offer.uom.clinical_units_per_quoted_unit
    new_list = net_price_per_clinical_unit * cu / (1.0 - offer.on_invoice_discount)
    return E.compute_enuc(replace(offer, list_price_per_quoted_unit=new_list), params).enuc_ledger_a


def _solve(f: Callable[[float], float], lo: float, hi: float, target: float, iters: int = 80) -> float | None:
    """Smallest x in [lo, hi] with f(x) <= target, for f non-increasing. None if unreachable."""
    if f(lo) <= target:
        return lo
    if f(hi) > target:
        return None
    for _ in range(iters):
        mid = (lo + hi) / 2
        if f(mid) <= target:
            hi = mid
        else:
            lo = mid
    return hi


@dataclass(frozen=True)
class CounterOfferResult:
    vendor: str
    current_enuc: float
    target_enuc: float
    gap_per_unit: float
    already_at_target: bool
    levers: list[dict]
    packages: list[dict]
    currency: str
    note: str


def counter_offer(
    offer: E.Offer,
    target_enuc: float,
    params: E.Parameters,
    max_terms_days: int = 90,
    max_free_goods_ratio: float = 0.25,
    package_terms_days: int = 60,
    package_free_goods_ratio: float = 0.05,
) -> CounterOfferResult:
    """What, and how much of it, gets this offer to the target ENUC.

    Each lever is solved on its own through ``compute_enuc``, then combined into
    packages. The rebate-to-invoice conversion comes first when the offer has a
    rebate: a rebate point is worth less to Siloam than an invoice point, so the
    swap costs the vendor little and gains Siloam a lot.
    """
    if target_enuc <= 0:
        raise EngineError("target_enuc must be positive")
    enuc = lambda o: E.compute_enuc(o, params).enuc_ledger_a  # noqa: E731
    current = enuc(offer)
    gap = current - target_enuc
    note = (
        "Each lever is solved alone against compute_enuc; packages combine them. "
        "Every figure recomputes to the target through the same ENUC kernel used for award."
    )
    if gap <= 0:
        return CounterOfferResult(offer.vendor, current, target_enuc, gap, True, [], [], params.currency,
                                  "The offer is already at or below target. Bank it; don't trade anything away.")

    cu = offer.uom.clinical_units_per_quoted_unit
    levers: list[dict] = []

    def add(lever: str, description: str, current_value, solved, fmt: Callable[[float], float], apply):
        feasible = solved is not None
        value = fmt(solved) if feasible else None
        levers.append(
            {
                "lever": lever,
                "description": description,
                "current": current_value,
                "required": value,
                "feasible": feasible,
                "resulting_enuc": enuc(apply(value)) if feasible else None,
            }
        )

    d = _solve(lambda x: enuc(replace(offer, on_invoice_discount=x)), offer.on_invoice_discount, 0.95, target_enuc)
    add("on_invoice_discount", "Raise the on-invoice discount", offer.on_invoice_discount, d,
        lambda x: x, lambda v: replace(offer, on_invoice_discount=v))
    if d is not None:
        levers.append(
            {
                "lever": "net_invoice_price",
                "description": "Equivalent net invoice price per clinical unit",
                "current": offer.invoice_price_per_clinical_unit,
                "required": offer.list_price_per_quoted_unit * (1 - d) / cu,
                "feasible": True,
                "resulting_enuc": enuc(replace(offer, on_invoice_discount=d)),
            }
        )

    t = _solve(lambda x: enuc(replace(offer, payment_terms_days=int(math.ceil(x)))),
               offer.payment_terms_days, max(max_terms_days, offer.payment_terms_days), target_enuc)
    add("payment_terms_days", f"Extend payment terms (capped at {max_terms_days} days)", offer.payment_terms_days,
        t, lambda x: int(math.ceil(x)), lambda v: replace(offer, payment_terms_days=v))

    fg = _solve(lambda x: enuc(replace(offer, free_goods_ratio=x)), offer.free_goods_ratio,
                max(max_free_goods_ratio, offer.free_goods_ratio), target_enuc)
    add("free_goods_ratio", f"Bonus stock (capped at {pct(max_free_goods_ratio, 0)})", offer.free_goods_ratio,
        fg, lambda x: x, lambda v: replace(offer, free_goods_ratio=v))

    packages: list[dict] = []

    def package(name: str, rationale: str, base: E.Offer, changes: dict) -> None:
        extra = _solve(lambda x: enuc(replace(base, on_invoice_discount=x)), base.on_invoice_discount, 0.95, target_enuc)
        if extra is None:
            packages.append({"name": name, "rationale": rationale, "changes": changes, "meets_target": False,
                             "resulting_enuc": enuc(base)})
            return
        final = replace(base, on_invoice_discount=extra)
        ch = dict(changes)
        if extra > base.on_invoice_discount + 1e-9:
            ch["on_invoice_discount"] = extra
        packages.append({
            "name": name,
            "rationale": rationale,
            "changes": ch,
            "net_invoice_price_per_clinical_unit": final.invoice_price_per_clinical_unit,
            "meets_target": True,
            "resulting_enuc": enuc(final),
        })

    if offer.rebate_tiers:
        r = E.rebate_expected_value(offer.rebate_tiers, offer.invoice_price_per_clinical_unit, params,
                                    offer.rebate_structure)
        invoice_spend = offer.quoted_annual_quantity * offer.list_price_per_quoted_unit * (1 - offer.on_invoice_discount)
        vendor_cost = r.gross_expected / invoice_spend if invoice_spend else 0.0
        package(
            "Convert rebate to on-invoice discount",
            f"The rebate costs the vendor about {pct(vendor_cost, 2)} of invoice in expectation but is worth only "
            f"{pct(r.net_expected / invoice_spend if invoice_spend else 0, 2)} to Siloam after breakage, tax and lag. "
            "Swap it for invoice discount first.",
            replace(offer, rebate_tiers=[]),
            {"rebate_tiers": "removed"},
        )

    terms = max(offer.payment_terms_days, min(package_terms_days, max_terms_days))
    if terms > offer.payment_terms_days:
        package(
            f"Terms to {terms} days, then price",
            "Terms cost the vendor its cost of capital, not margin; bank them before asking for price.",
            replace(offer, payment_terms_days=terms),
            {"payment_terms_days": terms},
        )
    fgr = max(offer.free_goods_ratio, package_free_goods_ratio)
    if fgr > offer.free_goods_ratio:
        package(
            f"Bonus stock {pct(fgr, 0)}, then price",
            "Bonus stock costs the vendor its production cost, not its price. Only take it for "
            "units you will use before expiry.",
            replace(offer, free_goods_ratio=fgr),
            {"free_goods_ratio": fgr},
        )
    package("Price only", "The simplest ask: one number on the invoice.", offer, {})

    return CounterOfferResult(offer.vendor, current, target_enuc, gap, False, levers, packages, params.currency, note)


# --------------------------------------------------------------------------
# Portfolio views: savings, vendors, trends, alerts
# --------------------------------------------------------------------------


def savings_opportunities(book: PriceBook, as_of: date | None = None, top_n: int = 10) -> list[dict]:
    """Where to negotiate first: SKUs ranked by spend above the best available price."""
    as_of = book.resolve_as_of(as_of)
    since, _ = _window(as_of, 12)
    rows = []
    for info in book.skus.values():
        po = book.query(skus=[info.sku], since=since, until=as_of, sources=["po"])
        if not po:
            continue
        hospitals = _hospital_prices(book, po, as_of)
        internal_best = hospitals[0]["weighted_price"]
        group_obs = book.query(skus=[s.sku for s in book.group_skus(info.equivalence_group)], since=since, until=as_of)
        group_best = _best(book, group_obs, as_of, "group_best", "Group best")
        best_available = min(internal_best, group_best.price) if group_best else internal_best
        spend = sum(o.spend for o in po)
        internal_variance = sum(h["excess_spend_vs_internal_best"] for h in hospitals)
        total = sum(
            max(0.0, book.adjusted_unit_price(o, as_of)[0] - best_available) * o.clinical_units for o in po
        )
        rows.append(
            {
                "sku": info.sku,
                "sku_name": info.sku_name,
                "equivalence_group": info.equivalence_group,
                "vendors": sorted({o.vendor for o in po}),
                "spend_12m": spend,
                "volume_12m": sum(o.clinical_units for o in po),
                "weighted_price_12m": _weighted_price(book, po, as_of),
                "internal_best_price": internal_best,
                "internal_best_site": hospitals[0]["hospital"],
                "group_best_price": group_best.price if group_best else None,
                "group_best_vendor": group_best.vendor if group_best else None,
                "group_best_sku": group_best.sku if group_best else None,
                "best_available_price": best_available,
                "internal_price_variance": internal_variance,
                "total_opportunity": total,
                "opportunity_pct_of_spend": total / spend if spend else 0.0,
                "single_source": info.single_source,
            }
        )
    rows.sort(key=lambda r: r["total_opportunity"], reverse=True)
    return rows[:top_n] if top_n else rows


def vendor_spend(book: PriceBook, as_of: date | None = None) -> list[dict]:
    """Spend and share of wallet by vendor, this year against last."""
    as_of = book.resolve_as_of(as_of)
    since, _ = _window(as_of, 12)
    prior_since = add_months(as_of, -24)
    current = book.query(since=since, until=as_of, sources=["po"])
    prior = [o for o in book.query(since=prior_since, until=since, sources=["po"]) if o.date < since]
    total = sum(o.spend for o in current) or 1.0
    vendors = sorted({o.vendor for o in current} | {o.vendor for o in prior})
    rows = []
    for v in vendors:
        cur = [o for o in current if o.vendor == v]
        pri = [o for o in prior if o.vendor == v]
        s_cur, s_pri = sum(o.spend for o in cur), sum(o.spend for o in pri)
        rows.append(
            {
                "vendor": v,
                "spend_12m": s_cur,
                "spend_prior_12m": s_pri,
                "growth": (s_cur / s_pri - 1.0) if s_pri else None,
                "share_of_wallet": s_cur / total,
                "skus": len({o.sku for o in cur}),
                "hospitals": len({o.hospital for o in cur}),
            }
        )
    rows.sort(key=lambda r: r["spend_12m"], reverse=True)
    return rows


def price_trend(
    book: PriceBook, sku_query: str, by: str = "vendor", months: int = 36, as_of: date | None = None
) -> dict:
    """Monthly volume-weighted nominal price per clinical unit, split by vendor or hospital."""
    if by not in ("vendor", "hospital"):
        raise EngineError("by must be 'vendor' or 'hospital'")
    info = book.resolve_sku(sku_query)
    as_of = book.resolve_as_of(as_of)
    start = add_months(as_of, -(months - 1))
    labels = [month_key(add_months(start, i)) for i in range(months)]
    po = book.query(skus=[info.sku], since=start.replace(day=1), until=as_of, sources=["po"])
    buckets: dict[str, dict[str, list[float]]] = {}
    for o in po:
        key = o.vendor if by == "vendor" else o.hospital
        acc = buckets.setdefault(key, {}).setdefault(month_key(o.date), [0.0, 0.0])
        acc[0] += o.net_price_per_clinical_unit * o.clinical_units
        acc[1] += o.clinical_units
    series = {
        name: [(m[lab][0] / m[lab][1]) if lab in m and m[lab][1] else None for lab in labels]
        for name, m in sorted(buckets.items())
    }
    index = None
    if book.price_index:
        index = [book.price_index.get(lab) for lab in labels]
    return {"sku": info.sku, "sku_name": info.sku_name, "by": by, "months": labels, "series": series,
            "price_index": index, "currency": book.currency}


def _vendor_price_change(book: PriceBook, sku: str, vendor: str, as_of: date) -> dict | None:
    """Last three months against the same three months a year earlier, nominal and index."""
    recent = [o for o in book.query(skus=[sku], since=add_months(as_of, -3), until=as_of, sources=["po"])
              if o.vendor == vendor]
    year_ago = [o for o in book.query(skus=[sku], since=add_months(as_of, -15), until=add_months(as_of, -12),
                                      sources=["po"]) if o.vendor == vendor]
    if not recent or not year_ago:
        return None

    def wavg(obs):
        u = sum(o.clinical_units for o in obs)
        return sum(o.net_price_per_clinical_unit * o.clinical_units for o in obs) / u

    change = wavg(recent) / wavg(year_ago) - 1.0
    f = book.index_factor(add_months(as_of, -12), as_of)
    index_change = (f - 1.0) if f else 0.0
    return {"nominal_change": change, "index_change": index_change, "excess_over_index": change - index_change,
            "spend_12m": sum(o.spend for o in book.query(skus=[sku], since=add_months(as_of, -12), until=as_of,
                                                          sources=["po"]) if o.vendor == vendor)}


def alerts(
    book: PriceBook,
    as_of: date | None = None,
    params: E.Parameters | None = None,
    creep_threshold: float = 0.03,
    variance_threshold: float = 0.05,
    renewal_days: int = 120,
    quote_window_days: int = 120,
) -> list[dict]:
    """Savings alerts: price creep, site overpayment, expensive quotes, renewals, rebates at risk."""
    as_of = book.resolve_as_of(as_of)
    out: list[dict] = []

    def alert(kind, severity, title, detail, impact=0.0, sku=None, vendor=None, hospital=None):
        out.append({"kind": kind, "severity": severity, "title": title, "detail": detail,
                    "annual_impact": impact, "sku": sku, "vendor": vendor, "hospital": hospital})

    pairs = {(o.sku, o.vendor) for o in book.query(since=add_months(as_of, -3), until=as_of, sources=["po"])}
    for sku, vendor in sorted(pairs):
        ch = _vendor_price_change(book, sku, vendor, as_of)
        if ch and ch["excess_over_index"] > creep_threshold:
            alert("price_creep", "high" if ch["excess_over_index"] > 2 * creep_threshold else "medium",
                  f"{vendor} raised {book.skus[sku].sku_name} {pct(ch['nominal_change'], 1)}",
                  f"Up {pct(ch['nominal_change'], 1)} year on year against an index move of {pct(ch['index_change'], 1)}.",
                  ch["excess_over_index"] * ch["spend_12m"], sku, vendor)

    since, _ = _window(as_of, 12)
    for info in book.skus.values():
        po = book.query(skus=[info.sku], since=since, until=as_of, sources=["po"])
        if not po:
            continue
        hospitals = _hospital_prices(book, po, as_of)
        best = hospitals[0]
        for h in hospitals[1:]:
            if h["premium_vs_internal_best"] > variance_threshold:
                alert("above_group_best", "high" if h["premium_vs_internal_best"] > 2 * variance_threshold else "medium",
                      f"{h['hospital']} pays {pct(h['premium_vs_internal_best'], 1)} over the group's best for {info.sku_name}",
                      f"{best['hospital']} pays {rp(best['weighted_price'])} per unit; {h['hospital']} pays "
                      f"{rp(h['weighted_price'])}.",
                      h["excess_spend_vs_internal_best"], info.sku, ", ".join(h["vendors"]), h["hospital"])

    lb_since = add_months(as_of, -24)
    for q in book.query(since=as_of - timedelta(days=quote_window_days), until=as_of, sources=["quote"]):
        history = [o for o in book.query(skus=[q.sku], since=lb_since, until=as_of, sources=PAID_SOURCES)
                   if o.vendor == q.vendor]
        if not history:
            continue
        best = _best(book, history, as_of, "vendor_own_best", "")
        qp = book.adjusted_unit_price(q, as_of)[0]
        if best and qp > best.price * 1.02:
            alert("quote_above_history", "medium",
                  f"{q.vendor} quoted {book.skus[q.sku].sku_name} {pct(qp / best.price - 1, 1)} above its own best",
                  f"Quote of {rp(q.net_price_per_clinical_unit)} per unit to {q.hospital} on {q.date}; "
                  f"{q.vendor} has sold it at {rp(best.price)} (in today's money).",
                  (qp - best.price) * q.clinical_units, q.sku, q.vendor, q.hospital)

    horizon = as_of + timedelta(days=renewal_days)
    for c in book.query(sources=["contract"]):
        if c.contract_end and as_of < c.contract_end <= horizon:
            days = (c.contract_end - as_of).days
            alert("renewal", "high" if days <= 45 else "medium",
                  f"{c.hospital}: {c.vendor} contract for {book.skus[c.sku].sku_name} ends in {days} days",
                  f"Ends {c.contract_end}. Run negotiation_brief for {c.sku} / {c.vendor} now.",
                  c.spend, c.sku, c.vendor, c.hospital)

    for p in book.rebate_programs:
        start = p.period_start
        months = []
        for i in range(p.months_in_period):
            m = add_months(start, i)
            if month_key(m) > month_key(as_of):
                break
            obs = [o for o in book.query(skus=[p.sku], sources=["po"]) if o.vendor == p.vendor
                   and month_key(o.date) == month_key(m)]
            months.append(sum(o.clinical_units for o in obs))
        if not months:
            continue
        breakage = params.rebate_breakage_rate if params else None
        if breakage is None:
            alert("rebate_blocked", "low", f"Cannot assess {p.vendor} rebate on {p.sku}",
                  f"Needs the rebate breakage rate: {E.BLOCKING_DATA_ELEMENTS['rebate_breakage_rate']}.",
                  0.0, p.sku, p.vendor)
            continue
        po = [o for o in book.query(skus=[p.sku], since=start, until=as_of, sources=["po"]) if o.vendor == p.vendor]
        units = sum(o.clinical_units for o in po)
        price = sum(o.net_price_per_clinical_unit * o.clinical_units for o in po) / units if units else 0.0
        r = E.rebate_realization(months, p.tier_target_units, p.tier_rate, price, breakage,
                                 p.collected_to_date, p.months_in_period)
        if not r.tier_will_be_met:
            alert("rebate_at_risk", "high", f"{p.vendor} rebate tier on {p.sku} will be missed",
                  r.message, p.tier_rate * p.tier_target_units * price, p.sku, p.vendor)
        elif r.breakage_to_date > 0:
            alert("rebate_uncollected", "medium", f"{p.vendor} rebate on {p.sku}: cash earned, not collected",
                  r.message, r.breakage_to_date, p.sku, p.vendor)

    rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda a: (rank[a["severity"]], -a["annual_impact"]))
    return out


def lookup(
    book: PriceBook,
    text: str | None = None,
    vendor: str | None = None,
    hospital: str | None = None,
    since: date | None = None,
    sources: Sequence[str] | None = None,
    limit: int = 50,
) -> dict:
    """What has been paid or quoted, by whom and where, summarised per SKU and vendor."""
    obs = book.query(text=text, vendor=vendor, hospital=hospital, since=since, sources=sources)
    if not obs:
        raise EngineError("Nothing matches that search. Try a shorter term or drop a filter.")
    as_of = book.latest_date
    summary: dict[tuple[str, str], list[PriceObservation]] = {}
    for o in obs:
        summary.setdefault((o.sku, o.vendor), []).append(o)
    rows = []
    for (sku, v), lst in sorted(summary.items()):
        latest = max(lst, key=lambda o: o.date)
        best = min(lst, key=lambda o: book.adjusted_unit_price(o, as_of)[0])
        rows.append(
            {
                "sku": sku,
                "sku_name": latest.sku_name,
                "vendor": v,
                "quoted_unit": latest.uom.quoted_unit,
                "clinical_units_per_quoted_unit": latest.uom.clinical_units_per_quoted_unit,
                "latest_date": latest.date.isoformat(),
                "latest_source": latest.source,
                "latest_price_per_quoted_unit": latest.net_price,
                "latest_price_per_clinical_unit": latest.net_price_per_clinical_unit,
                "best_price_per_clinical_unit": best.net_price_per_clinical_unit,
                "best_date": best.date.isoformat(),
                "best_hospital": best.hospital,
                "observations": len(lst),
                "hospitals": sorted({o.hospital for o in lst}),
            }
        )
    rows.sort(key=lambda r: (r["sku"], r["latest_price_per_clinical_unit"]))
    return {"matches": len(rows), "rows": rows[:limit], "truncated": len(rows) > limit,
            "currency": book.currency, "is_sample": book.is_sample}


# --------------------------------------------------------------------------
# The one-call brief
# --------------------------------------------------------------------------


def negotiation_brief(
    book: PriceBook,
    sku_query: str,
    vendor_query: str,
    params: E.Parameters,
    offer: E.Offer | None = None,
    as_of: date | None = None,
    beat_margin: float = 0.01,
    anchor_margin: float = 0.05,
    reservation_approved_by: str | None = None,
) -> dict:
    """Everything needed to walk into the room: evidence, targets, the gap and what to trade."""
    bm = benchmark(book, sku_query, as_of, vendor_hint=vendor_query)
    rec = recommend_targets(book, bm.sku, vendor_query, as_of, beat_margin, anchor_margin)
    offered = offer.invoice_price_per_clinical_unit if offer else rec.incumbent_current_price
    check = beat_check(book, bm.sku, offered, rec.vendor, as_of) if offered else None

    counter = None
    verdict = None
    verdict_note = None
    target_enuc = None
    if offer is not None:
        target_enuc = enuc_at_invoice_price(offer, params, rec.target_price)
        counter = counter_offer(offer, target_enuc, params)
        if reservation_approved_by and reservation_approved_by.strip():
            current = E.compute_enuc(offer, params)
            reservation_enuc = enuc_at_invoice_price(offer, params, rec.proposed_walk_away)
            competitor = next((r for r in rec.references if r.kind == "competitor_best"), None)
            batna = enuc_at_invoice_price(offer, params, competitor.price) if competitor else None
            verdict = E.decide(
                current_enuc=current.enuc_ledger_a,
                target_enuc=target_enuc,
                reservation_enuc=reservation_enuc,
                batna_enuc=batna,
                switching_cost_per_unit=0.0,
                annual_units=current.effective_usable_units,
                single_source=rec.single_source,
            )
            verdict_note = (
                f"Walk-away signed off by {reservation_approved_by.strip()}. The BATNA is the best competitor's "
                "invoice price on this offer's other terms, with no switching cost; pass a real BATNA to "
                "negotiation_verdict if you have one."
            )
        else:
            verdict_note = (
                f"No verdict: the walk-away of {rp(rec.proposed_walk_away)} per unit is {SIGN_OFF_STATUS}. "
                "Pass reservation_approved_by with the approver's name to get ACCEPT / PUSH / WALK."
            )

    headline = (
        f"Target for {rec.sku_name} from {rec.vendor}: {rp(rec.target_price, rec.currency)} per clinical unit "
        f"({rp(rec.target_per_quoted_unit, rec.currency)} per {rec.vendor_quoted_unit}). "
        f"That is {pct(rec.beat_margin, 0)} below the best reference ({rec.lowest_reference.label}: "
        f"{rp(rec.lowest_reference.price)}). Open at {rp(rec.opening_ask)}."
    )
    if rec.annual_saving_at_target:
        headline += f" Worth {rp(rec.annual_saving_at_target, rec.currency)} a year against today's price."

    return {
        "headline": headline,
        "is_sample": book.is_sample,
        "benchmark": bm,
        "targets": rec,
        "beat_check": check,
        "target_enuc": target_enuc,
        "counter_offer": counter,
        "verdict": verdict,
        "verdict_note": verdict_note,
    }

