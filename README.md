# Plan: Price-intelligence negotiation engine on top of `negotiation_mcp`

## Context

Your MCP (`negotiation_mcp`, from the uploaded zip) already turns any vendor offer into one comparable number (ENUC, Ledger A/B), ranks offers, prices trade-offs and gives a verdict. It **cannot yet answer "what price should we get?"**. It has no memory of what Siloam has paid, what competing vendors quoted, or what the cheapest Siloam hospital pays, so it can't set a target that beats all of that.

Goal: extend the MCP so Claude can answer questions like *"what's the best price we can get for SKU X from vendor Y?"*. The answer is a target that beats Siloam's own history, competing vendors and the best internal (hospital-to-hospital) price. It comes with evidence, an opening ask, a proposed walk-away and the concessions to trade.

Decisions you made:
- **Competitors** means competing vendors on equivalent SKUs, plus Siloam's own hospitals (internal price variance).
- **Targets:** the engine recommends a target, an opening ask and a walk-away. The walk-away stays **PROPOSED until a named person signs off**, which keeps your D-21 "refuse to guess" rule.
- **Interface:** new tools on the existing MCP.
- **Data:** you'll export real data later. For now I build a pluggable CSV data layer and a synthetic sample dataset. Every answer based on the sample data is clearly labelled.

The repo `/home/user/nego` is empty. Step 0 imports your zip as the baseline so the diff shows only the new work.

## Step 0: Baseline commit
- Extract the zip's `negotiation_mcp/` contents into the repo root (`negotiation_mcp/` package, `tests/`, `README.md`, `pyproject.toml`, `requirements.txt`, `evaluation.xml`).
- Commit as "Import negotiation_mcp v0.1.0" on `claude/jolly-faraday-8ohsv6`.
- Run `pytest` to confirm the 33 tests pass before changing anything.

## Step 1: Data layer (`negotiation_mcp/pricebook.py`, new)
Pure loading and normalisation, with no MCP code in it.
- `PriceObservation` dataclass: date, hospital, vendor, sku, sku_name, equivalence_group, quoted_unit, clinical_units_per_quoted_unit, quantity, list_price, discount, net_price, source (`po` | `contract` | `quote`) and optional payment_terms_days.
- `net_price_per_clinical_unit` is computed through the existing `UomConversion` in `engine.py`, so pack-size tricks can't distort comparisons.
- `PriceBook`: loads CSVs from `NEGOTIATION_DATA_DIR` (default `data/sample/`). Files:
  - `price_history.csv` (required)
  - `sku_master.csv` (optional; equivalence groups for competing vendors, single-source flag D-23)
  - `price_index.csv` (optional monthly index, used to bring old prices to today's money; if it's missing, results are flagged "not inflation-adjusted")
- Query helpers: filter by sku / name substring / equivalence group / vendor / hospital / date range.
- Validation fails loudly and names the row: a missing UoM factor raises D-13, the same as the engine's `_require_blocking` pattern.
- `data/templates/*.csv`: header-only templates so you know exactly what to export.
- `scripts/generate_sample_data.py`: deterministic (seeded) synthetic data covering about 5 hospitals, 4 vendors and 30 SKUs in 8 equivalence groups, over 36 months. It deliberately plants internal price variance, a pack-size trap and a cheaper competing vendor. Output goes to `data/sample/`.

## Step 2: Intelligence kernel (`negotiation_mcp/intelligence.py`, new, pure)
The same style as `engine.py`: frozen dataclasses, `EngineError`, deterministic.
- `benchmark(book, sku, as_of)`:
  - best-ever and best-trailing-12-month net unit price, each with who, where and when
  - p25 and median prices
  - per-vendor best price and per-hospital price
  - competing-vendor best price within the equivalence group
  - **internal price variance** = Σ volume × (hospital price − best internal price)
- `recommend_targets(book, sku, vendor, annual_volume, as_of, anchor_margin=0.05)`:
  - **Target** = the lowest defensible reference, i.e. the minimum of: best internal price (trailing 24 months), best competing-vendor price, and this vendor's own best historical price. Old prices are index-adjusted.
  - **Opening ask** = target × (1 − anchor_margin).
  - **Proposed walk-away** = the current incumbent price, or the trailing median if there is no incumbent. Status is `PROPOSED — requires sign-off (D-21)`.
  - Each number carries an evidence list (the source rows).
  - Volume leverage: the group's consolidated annual volume compared with each historical deal's volume is reported as a talking point, not silently priced in.
- `beat_check(offer, book, params)` checks whether an offer's net invoice price per clinical unit beats every historical, competitor and internal reference. It returns `BEATS_ALL`, or the list of references it fails to beat and the gap to each. If the history holds full terms, it also compares at ENUC level via `engine.compute_enuc`, and says clearly which basis each comparison uses.
- `counter_offer(offer, target_enuc, params)`:
  - Uses `engine.compute_enuc` with bisection to solve, one lever at a time, for what reaches the target: extra on-invoice discount, payment-terms days, free-goods ratio, or converting rebate points into on-invoice discount (via `engine.trade_ratios`).
  - It also returns 2–3 mixed packages, ordered cheapest-for-the-vendor first. Your README's rule "trade rebate for on-invoice first" becomes the first package.
- `savings_opportunities(book, top_n)` ranks SKUs by internal price variance plus the gap to the best competitor, times volume. This answers "where do we negotiate first".

## Step 3: MCP tools (`negotiation_mcp/server.py`, extended)
These follow the existing pattern: a `StrictModel` Pydantic input, `ResponseFormat` markdown/JSON through `_respond`, `_error`, read-only annotations, and helpers in `formatting.py`. The PriceBook is loaded once and cached.

| New tool | Answers |
|---|---|
| `negotiation_price_lookup` | "What have we paid or been quoted for X, from whom, where?" |
| `negotiation_benchmark` | Best, median, per-vendor, per-hospital and internal variance |
| `negotiation_recommend_targets` | Target, opening ask and proposed walk-away, with evidence |
| `negotiation_beat_check` | "Is this quote better than everything we know?" |
| `negotiation_counter_offer` | Which levers, and how much of each, reach the target |
| `negotiation_brief` | **One-call answer.** SKU + vendor (+ optional current offer) → benchmark, targets, beat-check, counter packages and verdict. This is the tool Claude uses for "best price for X from Y". |
| `negotiation_savings_opportunities` | The top SKUs to renegotiate across the group |

Changes to existing tools:
- `negotiation_verdict` accepts `reservation_approved_by`. A PROPOSED walk-away from `recommend_targets` can be used only when a name is given; otherwise the D-21 refusal stays.
- `negotiation_data_readiness` also reports the PriceBook's status: files loaded, row counts, date coverage, missing UoM factors, and whether the data is SAMPLE or real.
- Every price-intelligence response that uses `data/sample/` starts with a "SAMPLE DATA — not real prices" banner.

## Step 4: Tests, evaluation, docs
- `tests/test_pricebook.py`: CSV load, UoM normalisation (a box of 50 and a single price the same), row-level validation errors, index adjustment.
- `tests/test_intelligence.py`:
  - the target is never above any reference
  - the opening ask is below the target
  - the walk-away is labelled PROPOSED
  - beat_check detects each failure type
  - each counter-offer lever, fed back through `compute_enuc`, actually reaches the target (round-trip)
  - savings are ranked correctly
  - equivalence-group competitors are included
- Extend `tests/smoke_mcp.py` to call `negotiation_brief` end to end over stdio.
- Add about 5 questions to `evaluation.xml` against the sample dataset.
- README: new "Price intelligence" section covering the data schema, the `NEGOTIATION_DATA_DIR` setting, the sign-off rule, and how to swap in your real export.

## Step 5: Features modelled on Metabase / Superset and Ramp
You asked to "copy" these. I'll reproduce their **features**, not their code or branding. Ramp is proprietary. Metabase is AGPL, so vendoring it would force AGPL on this repo. Superset is Apache-licensed, but it is far too large to fork usefully.

**5a. BI-ready warehouse (the Metabase/Superset part), `negotiation_mcp/warehouse.py`**
- `scripts/build_warehouse.py` loads the CSVs into **SQLite** at `data/warehouse.db`. SQLite is in the Python standard library, so there's no new dependency.
- Tables: `price_history`, `sku_master`, `price_index`.
- Analytical views: `v_net_unit_price` (UoM-normalised), `v_internal_price_variance`, `v_vendor_vs_best`, `v_savings_opportunities`, `v_price_trend_monthly`.
- The views are computed by the same Python kernel and written back as tables, so there's one source of truth and dashboards can never disagree with the MCP.
- `deploy/docker-compose.yml` runs the **real Metabase** (the official image, run as-is) pointed at `warehouse.db`, plus `deploy/SUPERSET.md` with the connection string `sqlite:////data/warehouse.db`. Procurement staff then get the full Metabase/Superset experience (saved questions, drill-down, filters, scheduled email reports) without us rebuilding it.

**5b. Built-in dashboard (Metabase-style, zero install), `negotiation_mcp/dashboard/`**
- It's served with Starlette and uvicorn, which `mcp` already depends on: `negotiation-mcp dashboard --port 8080`. It's a read-only, single-page HTML app using vanilla JS and Chart.js.
- Pages:
  - **Overview**: KPI tiles (spend, savings identified, % of spend above best price) and the top 10 opportunities.
  - **SKU explorer**: price-trend line chart by vendor and by hospital, a benchmark band (best / p25 / median), and filters for hospital, vendor, equivalence group and date.
  - **Vendor scorecard**: each vendor's price against the best competitor, per SKU.
  - **Negotiation brief**: the `negotiation_brief` output rendered as a printable page.
- JSON endpoints under `/api/*` reuse `intelligence.py`, the same functions the MCP tools call.

**5c. Ramp-style price intelligence and alerts, in `intelligence.py`**
- **"You're paying X% more than the best price"** callout on every SKU and vendor (Ramp's price-benchmark insight), using internal and competitor references.
- **Savings alerts**, `negotiation_alerts` tool plus the dashboard banner:
  - price creep (a vendor raised the net price more than the index)
  - a hospital paying above the group's best price
  - a new quote above history
  - a rebate tier at risk (reuses `engine.rebate_realization`)
- **Renewal calendar**: contracts expiring within N days, with the negotiation brief pre-built. `price_history` gains an optional `contract_end` column for this.
- **Vendor spend view**: spend by vendor, share of wallet and trend, the basis for consolidation leverage.

## Critical files
- New: `negotiation_mcp/warehouse.py`, `negotiation_mcp/dashboard/` (app.py and static/), `scripts/build_warehouse.py`, `deploy/docker-compose.yml`, `deploy/SUPERSET.md`, `tests/test_warehouse.py`, `tests/test_dashboard.py`
- New: `negotiation_mcp/pricebook.py`, `negotiation_mcp/intelligence.py`, `scripts/generate_sample_data.py`, `data/templates/`, `data/sample/`, `tests/test_pricebook.py`, `tests/test_intelligence.py`
- Modified: `negotiation_mcp/server.py`, `negotiation_mcp/formatting.py`, `tests/smoke_mcp.py`, `README.md`, `evaluation.xml`
- Reused unchanged: `engine.py` (`compute_enuc`, `trade_ratios`, `decide`, `UomConversion`, `EngineError`, `_require_blocking`)

## Verification
1. `pip install -r requirements.txt pytest`, then `python scripts/generate_sample_data.py`.
2. `python -m pytest tests/ -q`: the original 33 tests plus the new ones all pass.
3. `python tests/smoke_mcp.py`: over stdio, lists the tools and calls `negotiation_brief` on a sample SKU. Check the result: it has the SAMPLE banner, the target is ≤ every reference, the counter packages reach the target when recomputed, and the walk-away is PROPOSED.
4. Point `NEGOTIATION_DATA_DIR` at a copy of the templates with a few hand-entered rows, and confirm that loading works and that validation errors name the row and field.
5. `python scripts/build_warehouse.py`, then open the SQLite file and check that each view's row totals match the kernel's output (a parity test in `tests/test_warehouse.py`).
6. Start the dashboard, load every page with Playwright (Chromium is preinstalled), take screenshots, and check that the `/api/*` JSON matches the MCP output.
7. Docker isn't available here, so the Metabase compose file can't be run in this session. I'll validate it statically and document the steps.
8. Commit and push to `claude/jolly-faraday-8ohsv6` (no PR unless you ask).
