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
