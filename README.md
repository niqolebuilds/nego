# negotiation_mcp

The decision layer for Siloam healthcare procurement negotiation, exposed as an MCP server.

## Why this exists

The Blueprint AI process catalogue documents 91 workflow steps across 12 procurement
processes. Principal Contract Negotiation step 18 branches on *"if thresholds are
breached, simulate alternative options"*, and step 20 on *"is there a deadlock with
significant financial impact requiring escalation?"* — while the same document's own
gap list says:

> "Absence of standardized threshold limits for automatic escalation versus continued
> negotiation loops."

The process branches on a threshold that has never been defined. Consumables Seasonal
Price Increase has the same hole at step 8. **This server is that missing decision
layer.**

It also closes a loop nothing else closes: no documented process tracks rebate
realization, so measured breakage has never fed back into the next cycle's rebate model.

## What it answers

*"What's the best price we can get for X from vendor Y?"* The answer is a target that
beats every price Siloam has paid, every Siloam site's price and every competing
vendor's price on an equivalent product. It comes with the evidence, an opening ask, a
proposed walk-away and the concessions to trade. See [Price intelligence](#price-intelligence).

## What it does

Collapses any vendor offer — price, on-invoice discount, rebate tiers, bonus stock,
wastage, payment terms, instrument capex or free placement, service, logistics — into
one comparable number:

**ENUC — Effective Net Unit Cost, in currency per usable clinical unit.**

Two ledgers, always reported separately:

| | Includes sponsorship | Used for |
|---|---|---|
| **Ledger A** | No | Ranking and award. The only basis. |
| **Ledger B** | Yes | Monitoring and disclosure. Never for award. |

Sponsorship is real value transferred and the engine quantifies it — but a vendor
quoting above market while sponsoring heavily is buying the price gap, and separating
the ledgers is what makes that visible.

## Install

```bash
pip install -r requirements.txt        # mcp 1.x: FastMCP was renamed in mcp 2.x
python -m negotiation_mcp.server        # stdio transport
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "negotiation": {
      "command": "python",
      "args": ["-m", "negotiation_mcp.server"],
      "cwd": "/absolute/path/to/negotiation_mcp"
    }
  }
}
```

## Tools

| Tool | Replaces / fills | Notes |
|---|---|---|
| `negotiation_compute_enuc` | PCN step 12 (Cost and Margin Impact Modeling) | The core. One offer to one number. |
| `negotiation_compare_offers` | PCN step 8, VendorSourcing step 2 | Ranks, flags sponsorship rank-flips. |
| `negotiation_rebate_expected_value` | — | Headline rate to expected cash. |
| `negotiation_trade_ratios` | Feeds PCN step 9 | What 1% of price buys elsewhere. |
| `negotiation_verdict` | **PCN steps 18 and 20, SPI step 8** | The empty gate. |
| `negotiation_convert_price_increase` | SPI steps 5 and 6 | Net vs discount binding. |
| `negotiation_rebate_realization` | *no process exists today* | Accrual to cash, breakage. |
| `negotiation_data_readiness` | — | What is still missing, who owns it, and the price book's status. |
| `negotiation_brief` | **"Best price for X from Y?"** | One call: benchmark, targets, beat-check, counter-offer, verdict. |
| `negotiation_recommend_targets` | Feeds PCN step 9 | Target, opening ask, PROPOSED walk-away, with evidence. |
| `negotiation_beat_check` | — | Does this quote beat history, sites and competitors? |
| `negotiation_counter_offer` | PCN step 18 | How much of each lever closes the gap, alone and as packages. |
| `negotiation_benchmark` | PCN step 8 | Best ever, best today, by vendor, by site, equivalents. |
| `negotiation_price_lookup` | — | Any price, any vendor, any site. |
| `negotiation_savings_opportunities` | — | Where to negotiate first. |
| `negotiation_alerts` | — | Price creep, overpaying sites, expensive quotes, renewals, rebates at risk. |
| `negotiation_vendor_spend` | — | Share of wallet and growth. |

Every tool is read-only, idempotent and closed-world: no external API and no writes.
The calculation tools take their data as parameters. The price-intelligence tools
read the local price book (CSV exports) and never write to it.

## Design decision: the engine refuses to guess

Nine data elements in the requirements register are blocking. Rather than substituting
defaults, tools that need one of them fail with the register ID and the owner:

```
Error: Missing required input 'reservation_enuc'. This is a known blocking data
element: D-21 — target / reservation / BATNA (owner: you + Heldra). The engine will
not substitute a default, because a plausible-looking wrong number here is worse
than no number.
```

This is deliberate. A reservation price invented by the tool would produce a
confident ACCEPT or WALK with nothing behind it.

The elements the engine touches directly:

| Key | Register | Owner |
|---|---|---|
| `escalation_thresholds` | D-20 | Andreas Tanjaya |
| `reservation_enuc` | D-21 | You + Heldra |
| `rebate_breakage_rate` | D-18 | Finance / AP |
| `uom_conversion` | D-13 | IT / Master Data |
| `single_source_flag` | D-23 | Pharmacy |

## Things worth knowing

**Money is shown the Indonesian way.** Amounts read `Rp 9.905` (dot for thousands,
whole rupiah) and large totals `Rp 5,36 M` (`rb` ribu, `jt` juta, `M` miliar, `T`
triliun). Percentages use a decimal comma, `10,6%`. Amounts under Rp 100 keep two
decimals so cheap per-unit prices stay comparable. This is display only: JSON output,
CSV input and the warehouse carry raw numbers. Pass another `currency` and the output
falls back to the international style.

**Unit of measure is the denominator, and it is load-bearing.** Vendors quote in boxes
of 50, boxes of 100 and singles, deliberately. `clinical_units_per_quoted_unit`
normalises them. A test asserts that quoting the same deal in boxes of 50 versus
singles gives an identical ENUC — get this field wrong and every number is wrong in a
way that still looks plausible.

**Bonus stock is a denominator effect, not a price cut.** And it is only worth
something if the units are usable before expiry, so wastage applies to bonus and paid
units alike.

**A "free" instrument is avoided capex.** It reduces cost, amortised over the contract
term — which is exactly how you price a reagent-rental deal against an outright
purchase, and how you find the consumable premium that is paying for it.

**One rebate point is worth less than one price point.** Rebate is probability-weighted,
lagged, and less tax-efficient. `negotiation_trade_ratios` computes the exchange rate;
trading rebate points for on-invoice price is usually cheaper for the vendor *and*
better for you, which makes it the first trade to look for.

**Single-source changes the meaning of WALK.** If a SKU has no clinically acceptable
alternative there is no credible walk-away. Pass `single_source: true` and the verdict
rationale says so — treat a WALK as an escalation, not an instruction.

## Known caveat

The net-binding and discount-binding definitions in
`negotiation_convert_price_increase` are **inferred from the Seasonal Price Increase
process document (steps 4–6), not from a contract.** Data element D-08 is still open.
Every response carries that caveat in the output. Confirm with the category owner
before anyone relies on it.

## Price intelligence

### Where the target comes from

For one SKU and one vendor, the engine builds an evidence ladder:

| Rung | Meaning |
|---|---|
| Best ever | The lowest price Siloam has ever paid or been quoted for the SKU, in today's money |
| Best Siloam site | The lowest 12-month price any Siloam hospital pays (internal price variance) |
| Vendor's own best | What this vendor has already accepted, in the last 24 months |
| Best competitor | The best other vendor's price on a clinically equivalent SKU, in the last 12 months |

- **Target** = `beat_margin` (default 1%) below the lowest rung. A deal at target beats every historical price, every site and every competitor.
- **Opening ask** = `anchor_margin` (default 5%) below the target.
- **Walk-away** = what Siloam pays this vendor today. It is labelled **PROPOSED — requires sign-off (D-21)**. The engine gives no ACCEPT / PUSH / WALK verdict until `reservation_approved_by` names someone other than the negotiator. This keeps the "refuse to guess" rule.

Every price is normalised to a **clinical unit** through the same `UomConversion` as
ENUC. A box of 50 and a single can never be compared as if they were alike. The
history records invoice prices, so benchmarks sit on the net-invoice-price basis. When
you pass the vendor's full offer, `negotiation_brief` translates the target into ENUC
on that offer's own terms, and the counter-offer is solved through `compute_enuc`.

### Loading your data

The price book is a folder of CSV exports. Point `NEGOTIATION_DATA_DIR` at it:

| File | Required | Content |
|---|---|---|
| `price_history.csv` | yes | One row per PO line, contract price or quote: date, hospital, vendor, sku, pack size (`clinical_units_per_quoted_unit`, D-13), quantity, list/discount/net price, source (`po`/`contract`/`quote`), terms, contract end |
| `sku_master.csv` | no | Name, **equivalence group** (what counts as a competing product), single-source flag (D-23) |
| `price_index.csv` | no | Monthly index, so old prices compare in today's money |
| `rebate_programs.csv` | no | Signed rebate tiers, tracked against purchase history |

Header-only templates are in `data/templates/`. Validation errors name the file, the
row and the field. A missing pack size is refused with D-13 rather than assumed to be 1.

Until you export real data, the engine runs on a **synthetic sample** in
`data/sample/`, generated by `python scripts/generate_sample_data.py`. Every answer
built on it starts with a `SAMPLE DATA` banner.

### The app

```bash
python -m negotiation_mcp.dashboard --port 8080 --breakage 0.10
```

A read-only web app on 127.0.0.1 that makes no external requests, so it works on a
hospital intranet. It shows one thing at a time on purpose:

1. **Welcome**: how to use it, then **Get started**.
2. **Conversation**: the assistant asks one question: *ask me anything, or see the
   Renewal Calendar & Risk Alerts?*
   - **Ask me anything**: a two-field form, **Target clinical SKU** and **Vendor**
     (pick from the list or type). It answers with the latest price and **three price
     options**: Stretch (open here), Target (beats every price on record) and Fallback
     (matches the best price on record). Then:
     - **Negotiate** gives a four-step plan: the opening ask and a line to say, what
       to trade if they push back (rebuilt from the vendor's current deal), your
       leverage, and the walk-away. Name an approver to get accept / push / walk.
     - **See details** explains the evidence in one sentence, then shows a sorted
       bar chart. Every price on record, today's price, your asks and the walk-away
       get one bar each, cheapest first and starting at zero, with a dashed line at
       the target, so you can see that everything on record costs more. Below it are a
       table view and what each hospital pays.
   - **Renewal Calendar & Risk Alerts**: contracts ending in 30–90 days on a month
     calendar. Each renewal is one row that expands to its targets, volume leverage
     and risks, with the high-severity risk alerts below.

The full analytics views (overview, SKU explorer, vendor scorecard, alerts) are still
at `/analytics`, one link away rather than on the first screen.

Colours: Cobalt Pulse `#0c21a4` for actions and your asks, Blue Breeze `#b8c7f8` for the
assistant and prices on record, Spring Field `#d4d67d` for savings and highlights, Soft Cloud `#fffafd`
as the base, and Sunset Pop `#ff891f` only as a small dot for urgent dates and the
walk-away.

### Metabase / Apache Superset

```bash
python scripts/build_warehouse.py --breakage 0.10     # -> data/warehouse.db (SQLite)
```

The `mart_*` tables are written by the same functions the tools call, so BI
dashboards and Claude always agree. `deploy/docker-compose.yml` runs the warehouse
build, the built-in dashboard and the official Metabase image together. Connection
steps for Metabase and Superset are in `deploy/SUPERSET.md`.

## Tests

```bash
python scripts/generate_sample_data.py   # only needed if data/sample/ is missing
python -m pytest tests/ -q               # 85 tests
python tests/smoke_mcp.py                # end-to-end over stdio
```

The unit tests recompute the scenario from `Procurement_Negotiation_Engine_v0.xlsx`,
whose figures were independently verified, so the kernel and the workbook cannot
silently diverge.

## Layout

```
negotiation_mcp/
  engine.py         pure calculation kernel — no MCP, no I/O, fully unit-testable
  pricebook.py      CSV loading, validation, UoM and inflation normalisation
  intelligence.py   benchmarks, targets, beat-check, counter-offer, alerts — pure
  warehouse.py      SQLite marts for Metabase / Superset
  formatting.py     shared markdown/JSON formatting
  server.py         FastMCP tools, Pydantic validation, annotations
  dashboard/        read-only Starlette app: two-page assistant (chat.js) + /analytics
scripts/
  generate_sample_data.py   deterministic synthetic price book
  build_warehouse.py        builds data/warehouse.db
data/
  sample/           synthetic price book (marked SAMPLE_DATA)
  templates/        header-only CSVs describing the export
deploy/
  docker-compose.yml, SUPERSET.md
tests/
  test_engine.py        33 tests including workbook parity
  test_pricebook.py     loading, validation, UoM, index
  test_intelligence.py  targets beat every reference; counter-offers recompute through ENUC
  test_warehouse.py     warehouse parity with the kernel
  test_dashboard.py     API parity, JSON safety, read-only
  smoke_mcp.py          end-to-end client over stdio
evaluation.xml      16 evaluation questions
```

The kernel is deliberately separable: a vendor building the full product can take
`engine.py` unchanged and wrap it in a REST API, a batch job, or a UI.

## Not in scope

Approval workflow routing, three-way matching, SLA tracking, ticketing. External
market price feeds: the engine benchmarks against Siloam's own history and the
vendors that quote to Siloam, not against other hospital groups. Those are 12
of the 34 catalogue gaps and they are platform work. This server advises; it does not
route, approve, or write to any system of record.
