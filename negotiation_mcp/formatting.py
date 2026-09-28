"""Shared response formatting. Every tool returns either markdown or JSON
through these helpers, so the two formats never drift apart."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from .engine import BindingConversionResult, EnucResult, RealizationResult, VerdictResult


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


def money(value: float, currency: str = "IDR") -> str:
    return f"{currency} {value:,.0f}"


def unit_money(value: float, currency: str = "IDR") -> str:
    return f"{currency} {value:,.2f}"


def pct(value: float, places: int = 2) -> str:
    return f"{value * 100:.{places}f}%"


def to_json(payload: Any) -> str:
    def default(o: Any) -> Any:
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        if isinstance(o, Enum):
            return o.value
        raise TypeError(f"Not JSON serialisable: {type(o).__name__}")

    return json.dumps(payload, indent=2, default=default, ensure_ascii=False)


def enuc_markdown(result: EnucResult, currency: str = "IDR") -> str:
    lines = [
        f"# ENUC — {result.vendor}",
        f"*{result.sku_group}*",
        "",
        f"## {unit_money(result.enuc_ledger_a, currency)} per usable unit  (Ledger A)",
        "",
        "Ledger A excludes sponsorship and is the only basis for ranking and award.",
        "",
        "### Annual cost bridge",
        "",
        "| Line | Amount | Per usable unit |",
        "|---|---:|---:|",
    ]
    per_unit = dict(result.waterfall_per_unit)
    for label, value in result.waterfall:
        lines.append(f"| {label} | {money(value, currency)} | {unit_money(per_unit[label], currency)} |")
    lines += [
        f"| **Total annual net cost** | **{money(result.total_annual_cost_ledger_a, currency)}** | "
        f"**{unit_money(result.enuc_ledger_a, currency)}** |",
        "",
        f"Effective usable units: **{result.effective_usable_units:,.0f}** per year",
        "",
    ]

    if result.rebate:
        r = result.rebate
        lines += [
            "### Rebate",
            "",
            f"- Headline top-tier rate quoted: **{pct(r.headline_top_rate)}**",
            f"- Effective net rate after probability, breakage, tax and lag: "
            f"**{pct(r.effective_net_rate)}**",
            f"- Haircut against the headline: **{pct(r.headline_haircut, 0)}**",
            f"- Expected net rebate: {money(r.net_expected, currency)}",
            "",
        ]

    lines += [
        "### Ledger B — monitoring and disclosure only",
        "",
        f"- Sponsorship value: {money(result.sponsorship_annual_value, currency)} "
        f"({pct(result.sponsorship_share_of_invoice)} of invoice spend)",
        f"- ENUC including sponsorship: {unit_money(result.enuc_ledger_b, currency)}",
        "",
        "Never rank or award on Ledger B.",
        "",
    ]

    if result.compliance_flags:
        lines.append("### Compliance")
        lines.append("")
        lines += [f"- {f}" for f in result.compliance_flags]
    return "\n".join(lines)


def verdict_markdown(v: VerdictResult, currency: str = "IDR") -> str:
    lines = [
        f"# {v.headline}",
        "",
        "| | Per usable unit |",
        "|---|---:|",
        f"| Current offer | {unit_money(v.current_enuc, currency)} |",
        f"| Target | {unit_money(v.target_enuc, currency)} |",
        f"| Reservation (walk-away) | {unit_money(v.reservation_enuc, currency)} |",
    ]
    if v.batna_enuc_including_switching != float("inf"):
        lines.append(
            f"| BATNA incl. switching | {unit_money(v.batna_enuc_including_switching, currency)} |"
        )
    lines += [
        "",
        f"- Gap to target: **{unit_money(v.gap_to_target_per_unit, currency)}** "
        f"({pct(v.gap_to_target_pct, 1)})",
        f"- Headroom to reservation: {unit_money(v.headroom_to_reservation_per_unit, currency)}",
        f"- Annual value of closing the gap: **{money(v.annual_value_of_closing_gap, currency)}**",
        "",
    ]
    if v.rationale:
        lines.append("### Notes")
        lines.append("")
        lines += [f"- {r}" for r in v.rationale]
    return "\n".join(lines)


def binding_markdown(b: BindingConversionResult, currency: str = "IDR") -> str:
    return "\n".join(
        [
            f"# Price increase — {b.binding_type} binding",
            "",
            b.explanation,
            "",
            "| | Before | After |",
            "|---|---:|---:|",
            f"| List price | {unit_money(b.old_list_price, currency)} | "
            f"{unit_money(b.new_list_price, currency)} |",
            f"| Discount | {pct(b.old_discount)} | {pct(b.required_or_resulting_discount)} |",
            f"| Net price | {unit_money(b.old_net_price, currency)} | "
            f"{unit_money(b.new_net_price, currency)} |",
            "",
            f"- List increase: **{pct(b.list_increase_pct)}**",
            f"- Net price change: **{pct(b.net_price_change_pct)}**",
            f"- Annual cost impact: **{money(b.annual_cost_impact, currency)}**",
            "",
            f"> {b.caveat}",
        ]
    )


def realization_markdown(r: RealizationResult, currency: str = "IDR") -> str:
    return "\n".join(
        [
            f"# Rebate realization — {r.status}",
            "",
            f"- Tier target: {r.tier_target_units:,.0f} units",
            f"- Cumulative to date: {r.cumulative_units:,.0f} units "
            f"({pct(r.tier_achieved_pct, 1)} of tier)",
            f"- Period elapsed: {pct(r.period_elapsed, 1)}",
            f"- Pace index: **{r.pace_index:.2f}** (1.00 = exactly on pace)",
            f"- Projected year-end: {r.projected_year_end_units:,.0f} units — "
            f"tier {'will' if r.tier_will_be_met else 'will NOT'} be met",
            "",
            "| | Amount |",
            "|---|---:|",
            f"| Accrued (net of breakage) | {money(r.accrued_rebate, currency)} |",
            f"| Collected to date | {money(r.collected_rebate, currency)} |",
            f"| **Uncollected** | **{money(r.breakage_to_date, currency)}** |",
            "",
            r.message,
        ]
    )


def comparison_markdown(c: dict, currency: str = "IDR") -> str:
    lines = [
        f"# Comparison — {c['sku_group']}",
        "",
        f"Award basis: **{c['award_basis']}**",
        "",
        "| Rank | Vendor | ENUC Ledger A | ENUC Ledger B | Rank on B | Sponsorship flips rank |",
        "|---:|---|---:|---:|---:|---|",
    ]
    for row in sorted(c["ranking"], key=lambda r: r["rank_ledger_a"]):
        lines.append(
            f"| {row['rank_ledger_a']} | {row['vendor']} | "
            f"{unit_money(row['enuc_ledger_a'], currency)} | "
            f"{unit_money(row['enuc_ledger_b'], currency)} | {row['rank_ledger_b']} | "
            f"{'YES' if row['rank_changed_by_sponsorship'] else 'no'} |"
        )
    lines += [
        "",
        f"**Winner on Ledger A: {c['winner']}** at {unit_money(c['winner_enuc'], currency)}",
        "",
        f"- Spread best vs worst: {unit_money(c['spread_best_vs_worst_per_unit'], currency)} per unit",
        f"- Annual value of choosing best over worst: "
        f"**{money(c['annual_value_of_choosing_best'], currency)}**",
        "",
    ]
    if c["sponsorship_changes_ranking"]:
        lines += [
            "> Sponsorship changes the ranking between the two ledgers. Award on Ledger A "
            "regardless, and document why.",
            "",
        ]
    flags = [(r["vendor"], f) for r in c["ranking"] for f in r["compliance_flags"]]
    if flags:
        lines.append("### Compliance")
        lines.append("")
        lines += [f"- **{v}** — {f}" for v, f in flags]
    return "\n".join(lines)


# ==========================================================================
# Price intelligence
# ==========================================================================


def _banner(is_sample: bool) -> list[str]:
    from .pricebook import SAMPLE_BANNER

    return [f"> **{SAMPLE_BANNER}**", ""] if is_sample else []


def _ref_row(r, currency: str) -> str:
    where = " · ".join(x for x in (r.vendor, r.hospital, r.date, r.source) if x)
    return f"| {r.label} | {unit_money(r.price, currency)} | {where} |"


def benchmark_markdown(b, banner: bool = True) -> str:
    c = b.currency
    lines = _banner(b.is_sample and banner) + [
        f"# Price benchmark — {b.sku_name}",
        f"*{b.sku} · group: {b.equivalence_group} · as of {b.as_of} · per {b.clinical_unit}*",
        "",
        "| Reference | Per clinical unit | Where / when |",
        "|---|---:|---|",
        _ref_row(b.best_ever, c),
    ]
    for r in (b.best_12m, b.internal_best, b.group_best):
        if r:
            lines.append(_ref_row(r, c))
    if b.p25_12m is not None:
        lines.append(f"| 25th percentile, last 12 months | {unit_money(b.p25_12m, c)} | |")
        lines.append(f"| Median, last 12 months | {unit_money(b.median_12m, c)} | |")
    if b.weighted_avg_paid_12m is not None:
        lines.append(f"| Volume-weighted average paid, last 12 months | {unit_money(b.weighted_avg_paid_12m, c)} | |")
    lines += [
        "",
        f"- Volume, last 12 months: **{b.annual_volume_12m:,.0f}** · spend **{money(b.annual_spend_12m, c)}**",
        f"- Internal price variance (paid above the best site): **{money(b.internal_price_variance_12m, c)}**",
    ]
    if b.single_source:
        lines.append("- **Single-source SKU** (D-23): no credible walk-away.")
    lines += ["", "### By vendor", "", "| Vendor | Best (24m) | Latest | Weighted 12m | Volume 12m |", "|---|---:|---:|---:|---:|"]
    for v in b.by_vendor:
        best = unit_money(v["best_price"], c) if v["best_price"] is not None else "—"
        wp = unit_money(v["weighted_price_12m"], c) if v["weighted_price_12m"] is not None else "—"
        lines.append(f"| {v['vendor']} | {best} | {unit_money(v['latest_price'], c)} ({v['latest_source']}, {v['latest_date']}) | {wp} | {v['volume_12m']:,.0f} |")
    if b.by_hospital:
        lines += ["", "### By Siloam site (last 12 months)", "", "| Site | Weighted price | Premium vs best site | Excess spend |", "|---|---:|---:|---:|"]
        for h in b.by_hospital:
            lines.append(f"| {h['hospital']} | {unit_money(h['weighted_price'], c)} | {pct(h['premium_vs_internal_best'], 1)} | {money(h['excess_spend_vs_internal_best'], c)} |")
    if len(b.by_group_sku) > 1:
        lines += ["", f"### Equivalent SKUs in '{b.equivalence_group}'", "", "| SKU | Vendors | Best 12m |", "|---|---|---:|"]
        for g in b.by_group_sku:
            best = unit_money(g["best_price_12m"], c) if g["best_price_12m"] is not None else "—"
            lines.append(f"| {g['sku_name']} | {', '.join(g['vendors'])} | {best} |")
    lines += ["", f"*Basis: {b.price_basis}*"]
    return "\n".join(lines)


def targets_markdown(t, banner: bool = True) -> str:
    c = t.currency
    lines = _banner(t.is_sample and banner) + [
        f"# Targets — {t.sku_name} from {t.vendor}",
        f"*as of {t.as_of}*",
        "",
        "| | Per clinical unit | Per " + t.vendor_quoted_unit + " |",
        "|---|---:|---:|",
        f"| **Opening ask** | **{unit_money(t.opening_ask, c)}** | {unit_money(t.opening_ask_per_quoted_unit, c)} |",
        f"| **Target** | **{unit_money(t.target_price, c)}** | {unit_money(t.target_per_quoted_unit, c)} |",
        f"| Walk-away — {t.walk_away_status} | {unit_money(t.proposed_walk_away, c)} | "
        f"{unit_money(t.proposed_walk_away * t.vendor_clinical_units_per_quoted_unit, c)} |",
    ]
    if t.incumbent_current_price is not None:
        lines.append(f"| Paying this vendor today | {unit_money(t.incumbent_current_price, c)} | "
                     f"{unit_money(t.incumbent_current_price * t.vendor_clinical_units_per_quoted_unit, c)} |")
    lines += [
        "",
        f"Target = {pct(t.beat_margin, 0)} below the lowest reference. Opening ask = {pct(t.anchor_margin, 0)} below target. "
        f"Walk-away basis: {t.walk_away_basis}.",
        "",
    ]
    if t.annual_saving_at_target is not None:
        lines.append(f"**Annual value at target: {money(t.annual_saving_at_target, c)}** on {t.annual_volume:,.0f} units.")
        lines.append("")
    lines += ["### Evidence (lowest first)", "", "| Reference | Per clinical unit | Where / when |", "|---|---:|---|"]
    lines += [_ref_row(r, c) for r in t.references]
    if t.leverage:
        lines += ["", "### Leverage", ""] + [f"- {x}" for x in t.leverage]
    lines += ["", "### Caveats", ""] + [f"- {x}" for x in t.caveats]
    return "\n".join(lines)


def beat_markdown(b, banner: bool = True) -> str:
    c = b.currency
    icon = {"BEATS_ALL": "✅", "BEATS_SOME": "⚠️", "BEATS_NONE": "❌"}[b.status]
    lines = _banner(b.is_sample and banner) + [
        f"# {icon} {b.status} — {unit_money(b.offered_price, c)} per clinical unit",
        "",
        b.headline,
        "",
        "| Reference | Price | Beats? | Gap |",
        "|---|---:|:---:|---:|",
    ]
    for ch in b.checks:
        lines.append(f"| {ch['reference']} | {unit_money(ch['reference_price'], c)} | "
                     f"{'yes' if ch['beats'] else '**no**'} | {unit_money(ch['gap_per_unit'], c)} ({pct(ch['gap_pct'], 1)}) |")
    lines += ["", f"*Basis: {b.price_basis}*"]
    return "\n".join(lines)


def counter_markdown(co) -> str:
    c = co.currency
    lines = [
        f"# Counter-offer — {co.vendor}",
        "",
        f"ENUC now **{unit_money(co.current_enuc, c)}**, target **{unit_money(co.target_enuc, c)}**, "
        f"gap {unit_money(co.gap_per_unit, c)} per usable unit.",
        "",
    ]
    if co.already_at_target:
        return "\n".join(lines + [co.note])
    lines += ["### One lever at a time", "", "| Lever | Now | Needed |", "|---|---:|---:|"]
    for lv in co.levers:
        fmt = (lambda v: pct(v)) if "ratio" in lv["lever"] or "discount" in lv["lever"] else (
            (lambda v: f"{v:,.0f} days") if "days" in lv["lever"] else (lambda v: unit_money(v, c)))
        need = fmt(lv["required"]) if lv["feasible"] else "not reachable alone"
        lines.append(f"| {lv['description']} | {fmt(lv['current'])} | {need} |")
    lines += ["", "### Packages (suggested order)", ""]
    for i, p in enumerate(co.packages, 1):
        parts = []
        for k, v in p["changes"].items():
            if k == "on_invoice_discount" or k == "free_goods_ratio":
                parts.append(f"{k.replace('_', ' ')} {pct(v)}")
            elif k == "payment_terms_days":
                parts.append(f"terms {v} days")
            else:
                parts.append(f"{k.replace('_', ' ')} {v}")
        status = "reaches target" if p["meets_target"] else "does NOT reach target"
        lines.append(f"{i}. **{p['name']}** — {', '.join(parts) or 'no change'} → ENUC "
                     f"{unit_money(p['resulting_enuc'], c)} ({status}). {p['rationale']}")
    lines += ["", f"*{co.note}*"]
    return "\n".join(lines)


def brief_markdown(br: dict) -> str:
    t = br["targets"]
    lines = _banner(br["is_sample"]) + [
        f"# Negotiation brief — {t.sku_name} / {t.vendor}",
        "",
        f"**{br['headline']}**",
        "",
        "---",
        targets_markdown(t, banner=False),
    ]
    if br["beat_check"]:
        lines += ["", "---", beat_markdown(br["beat_check"], banner=False)]
    if br["counter_offer"]:
        lines += ["", "---", counter_markdown(br["counter_offer"])]
    lines += ["", "---", "## Verdict", ""]
    if br["verdict"]:
        lines.append(verdict_markdown(br["verdict"], t.currency))
    if br["verdict_note"]:
        lines += ["", f"> {br['verdict_note']}"]
    if not br["counter_offer"]:
        lines.append("Pass the vendor's current offer to get a counter-offer and a verdict.")
    lines += ["", "---", benchmark_markdown(br["benchmark"], banner=False)]
    return "\n".join(lines)


def savings_markdown(rows: list[dict], is_sample: bool, currency: str = "IDR") -> str:
    lines = _banner(is_sample) + [
        "# Savings opportunities — where to negotiate first",
        "",
        "| # | SKU | Spend 12m | Paying | Best available | Opportunity | % of spend |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(rows, 1):
        flag = " (single-source)" if r["single_source"] else ""
        lines.append(
            f"| {i} | {r['sku_name']}{flag} | {money(r['spend_12m'], currency)} | {unit_money(r['weighted_price_12m'], currency)} | "
            f"{unit_money(r['best_available_price'], currency)} | **{money(r['total_opportunity'], currency)}** | "
            f"{pct(r['opportunity_pct_of_spend'], 1)} |"
        )
    total = sum(r["total_opportunity"] for r in rows)
    lines += ["", f"Total identified in this list: **{money(total, currency)}** a year.",
              "", "Best available = the lower of the best Siloam site's price and the best price in the equivalence group."]
    return "\n".join(lines)


def alerts_markdown(rows: list[dict], is_sample: bool, currency: str = "IDR") -> str:
    icon = {"high": "🔴", "medium": "🟠", "low": "⚪"}
    lines = _banner(is_sample) + [f"# Savings alerts ({len(rows)})", ""]
    for a in rows:
        impact = f" — {money(a['annual_impact'], currency)}" if a["annual_impact"] else ""
        lines.append(f"- {icon[a['severity']]} **{a['title']}**{impact}  \n  {a['detail']}")
    return "\n".join(lines)


def vendor_spend_markdown(rows: list[dict], is_sample: bool, currency: str = "IDR") -> str:
    lines = _banner(is_sample) + [
        "# Vendor spend — last 12 months",
        "",
        "| Vendor | Spend | Share | vs prior year | SKUs | Sites |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        growth = pct(r["growth"], 1) if r["growth"] is not None else "new"
        lines.append(f"| {r['vendor']} | {money(r['spend_12m'], currency)} | {pct(r['share_of_wallet'], 1)} | {growth} | "
                     f"{r['skus']} | {r['hospitals']} |")
    return "\n".join(lines)


def lookup_markdown(res: dict) -> str:
    c = res["currency"]
    lines = _banner(res["is_sample"]) + [
        f"# Price lookup — {res['matches']} SKU/vendor combination(s)",
        "",
        "| SKU | Vendor | Pack | Latest (per pack) | Latest per unit | Best per unit | Seen at |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for r in res["rows"]:
        lines.append(
            f"| {r['sku_name']} | {r['vendor']} | {r['quoted_unit']} | {money(r['latest_price_per_quoted_unit'], c)} "
            f"({r['latest_source']}, {r['latest_date']}) | {unit_money(r['latest_price_per_clinical_unit'], c)} | "
            f"{unit_money(r['best_price_per_clinical_unit'], c)} ({r['best_hospital']}, {r['best_date']}) | "
            f"{len(r['hospitals'])} site(s) |"
        )
    if res["truncated"]:
        lines += ["", "Results truncated — narrow the search or raise the limit."]
    return "\n".join(lines)
