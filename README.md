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
| `negotiation_principals` | Nego Standarisasi (6-month alert) | Principals, MOU end dates, where each negotiation is. |
| `negotiation_cycle_summary` | Cost impact in Excel | One principal negotiation: step, RFQ coverage, cost impact per step. |
| `negotiation_cycle_findings` | Hunting for largest/smallest anomalies | The anomaly queue, with the discount that holds the MOU price. |

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
whole rupiah) and large totals `Rp 5,36 Bn` (`K` thousand, `Mn` million / juta,
`Bn` billion / miliar, `Tn` trillion / triliun). Percentages use a decimal comma, `10,6%`. Amounts under Rp 100 keep two
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
NEGO_ADMIN_EMAIL=you@siloamhospitals.com python -m negotiation_mcp.dashboard --port 8080
```

It's a web app on 127.0.0.1 that makes no external requests, so it works on a hospital intranet.

**Signing in and roles**
- **Getting in:** *Get started* leads to **Sign in**. Only registered users get in, and admins
  invite them; there's no self sign-up. On first run the app creates one admin from
  `NEGO_ADMIN_EMAIL`, or `admin@example.com` if that isn't set.
- **Viewers** can see everything and update renewal progress.
- **Admins** can also:
  - upload price lists, records and documents
  - roll data back
  - tune the engine
  - manage users
  - read the activity log
- **The server enforces this on every request**, not just the page:
  - `/api` needs a session;
  - `/api/admin` needs the admin role;
  - writes need the app's own header.
- **Sessions** are random IDs in an HMAC-signed, HttpOnly, SameSite=Strict cookie that lasts
  12 hours. Set `NEGO_SECRET`, or one is generated in the workspace.
- **Passwords aren't checked yet.** Sign-in is a *development* stand-in that accepts any
  registered, active email, and the sign-in page says so. Real authentication plugs into
  `dashboard/auth.py` (replace `DevSignIn`, e.g. Microsoft Entra ID SSO). Roles, sessions
  and checks stay as they are.

**Assistant**
- **Two ways to ask:**
  - **type a question** in the bar, e.g. "best price for ceftri from medisindo",
    "renewals in the next 60 days", "where can we save", "how much do we spend with Sehat";
  - **use the buttons**: *Ask me anything*, *Renewal calendar*, *Risk alerts*, *Where to save*.
- **The answers:**
  - **three price options:** Stretch, Target and Fallback;
  - **Negotiate**, which you can copy as text or print;
  - **See details**, a sorted price bar chart with a target line;
  - vendor profiles, spend, price look-ups and savings.
- **How typed questions are read:** a built-in, offline, typo-tolerant parser (`nlu.py`)
  asks when something is missing ("Which vendor?"). It only routes; the engine computes every
  number.

**Renewals**
- A board with one column per stage (*Not started → Preparing → In negotiation → Offer
  received → Agreed / Escalated / Lost*). Drag a card, or use its stage menu.
- **Open a renewal to:**
  - set the owner, next step and due date
  - log the vendor's offer (it says where the offer lands and the next move)
  - record the agreed price
  - add notes
  - see its timeline and documents
- Agreed prices add up to **saving realised**.

**Admin**
- **Price data:** upload a CSV or Excel file, see a row-checked preview, then *add* or
  *replace*. Every apply is a new version with one-click rollback.
- **Documents:** PDF, Word, Excel, CSV or image files, linked to a vendor, product or renewal.
- **Engine settings:** margins, WACC, rebate breakage (D-18), the renewal window and alert
  thresholds. They're shared with the MCP server.
- **Users and Activity.**

Everything the app writes (users, sessions, progress, uploads, versions, documents,
settings) lives in one folder, `data/workspace/` (gitignored; `NEGO_HOME` moves it). Back up
that folder.

The full analytics views are at `/analytics` (signed in).

**Colours:**
- Cobalt Pulse `#0c21a4`: actions and your asks
- Blue Breeze `#b8c7f8`: the assistant and prices on record
- Spring Field `#d4d67d`: savings and highlights
- Soft Cloud `#fffafd`: the base
- Sunset Pop `#ff891f`: only a small dot for urgent dates and the walk-away

### Does it use an LLM?

No. The engine is deterministic arithmetic, so the same data always gives the same prices,
and the web app runs with no LLM at all. Claude comes in only as a *user* of the engine, when
you connect the MCP server to Claude Desktop.

The roadmap adds an LLM around the engine, never inside the price maths:
- a Claude fallback for questions the parser can't read (the admin switch exists; it isn't
  connected yet);
- drafting negotiation emails;
- reading prices from uploaded PDF contracts into a review queue that an admin approves.

### Metabase / Apache Superset

```bash
python scripts/build_warehouse.py --breakage 0.10     # -> data/warehouse.db (SQLite)
```

The `mart_*` tables are written by the same functions the tools call, so BI
dashboards and Claude always agree. `deploy/docker-compose.yml` runs the warehouse
build, the built-in dashboard and the official Metabase image together. Connection
steps for Metabase and Superset are in `deploy/SUPERSET.md`.

## Principal negotiations (Template_Nego)

Siloam negotiates **one principal at a time, with all of its items**. The app follows the
six sections of `Template_Nego.xlsx`, plus a Prepare step before them and a Submission step after:

| Step | Who | What happens |
|---|---|---|
| Prepare | Siloam | The item list is built from 12 months of POs (PowerBI export), the formulary (items not bought are added) and the current MOU, then sorted A–Z. |
| 1. Item Identification | Principal | Brand, catalogue no. (REF), Active/Discontinue, remarks. |
| 2. Current MOU | Reference | MOU Qty/PO unit, HNA, discount and unit price incl. PPN. |
| 3. RFQ | Principal | Qty/PO unit, HNA, discount. |
| 4. Counter Offer | Siloam | A CO discount per item. The engine proposes the discount that holds the MOU price. |
| 5. Feedback I | Principal | Accept, or a Feedback I discount. |
| 6. Online Nego | Siloam | The agreed discount. |
| Submission | Principal | Documents for the BAK. The app stops at document submission. |

Every unit price uses the template formula, `HNA / Qty × (1 − Disc) × (1 + PPN)`. Counter offer,
Feedback I and Online Nego price the RFQ HNA and Qty with their own discount, as columns P, R and T
do. PPN is an admin setting (11% by default).

**Engine checks (the anomaly queue).** Each finding has a severity, a plain message and, where
the fix is obvious, a one-click fix. An admin decides to fix it or keep it as is; keeping it needs a
reason, which is logged. Decisions survive rescans. The checks:
- **Typing slips:** a discount typed as 15 instead of 15%, a discount of 100% or more, HNA without
  a Qty, an HNA of 0.
- **Units:** the pack size changed between the MOU and the RFQ; the PO unit text (`BOX@50`,
  `BX 10 VIAL`, `1 BOX = 100 PCS`…) disagrees with the pack; a price off by a pack multiple
  (≈10×, 50×, 100×).
- **Rule 1, no increase if avoidable:** every increase over the MOU per piece, with the
  conversion discount that keeps the MOU net price.
- **Rule 2, always check the largest and smallest changes:** robust outliers (median/MAD) plus
  the top N each way.
- **List problems:** duplicate ERP codes, discontinued items with a price, and active items with
  no RFQ price once the RFQ is back.
- **PO price vs MOU:** Siloam's last PO price far from the MOU (invoice compliance or a unit
  mix-up).

**Cost impact** is (step price − MOU price) × pieces a year from the POs, per item and per step.
Margin impact needs Siloam's selling price per item and isn't computed yet.

**Template_Nego in and out.**
- **Export:** the real workbook, filled in, with the title block, legend, colours, drop-downs,
  highlight rules and formulas kept. It grows past 150 rows when needed.
- **Import:** reads a filled copy back by its headers. You see every change before applying it.
  Blank cells never erase a value, and a file for another principal is refused.
- **Last cycle's template as the current MOU:** it's read with the agreed discount (ON, else FB1,
  CO, RFQ) as the MOU discount.

**The principal's side (portal).** An admin clicks **Principal link** on a negotiation and sends
the link by WhatsApp or email. The principal needs no account. The link opens only that
principal's current step (Item Identification, RFQ or Feedback I), in Bahasa Indonesia with an
English switch. Entry is built to avoid mistakes:
- **Plain questions:** "HNA per BOX, sebelum PPN" and "Isi per BOX (pcs)" instead of the
  template headers.
- **Prefilled from the current MOU:** one click on **Sama dengan MOU** fills a SKU, and one
  click fills every blank SKU.
- **Inputs that can't be misread:** a discount field always reads as percent (15 means 15%), and
  HNA shows thousands separators as you type.
- **The price per piece incl. PPN appears instantly**, using the template formula, with the
  change against the MOU.
- **Checks run on the server on every save,** with the same code for the Excel upload:
  - **Errors block sending:** a discount of 100% or more, HNA of 0, Qty not a whole number,
    missing values.
  - **Increases are allowed but need a reason.** Siloam's findings show it.
  - **Unusual entries are confirmed once ("Ya, sudah benar"):** pack-size changes, prices off
    by a pack multiple, big drops, very high discounts.
- **Never loses work:** autosave per SKU, a progress bar, filters ("Belum diisi", "Perlu dicek",
  "Harga naik"), and a one-SKU-at-a-time view on phones.
- **Excel route:** a locked copy of Template_Nego where only the step's columns can be typed in,
  with hints and stricter drop-downs in Bahasa, a check column and a reason column. Rows with
  mistakes are listed and not saved.
- **Sending:** **Kirim ke Siloam** shows a summary and stays disabled until nothing is left to
  fix. The step then locks, Siloam sees "Principal sent ✓", and the findings are rescanned.

What a principal can reach is enforced on the server (`nego/portal.py`):
- one negotiation per link;
- only the open step's fields;
- responses that never include PO volumes or values, cost impact, Siloam's findings or other
  principals.

Links can be revoked, and only their SHA-256 hash is stored.

**After the RFQ: counter offer, benchmarks, Online Nego, submission (the Negotiate tab).**
- **Counter offer:** one click proposes a CO discount per item. It brings the price per piece
  down to the lowest credible reference: the MOU price (rule 1), Siloam's last PO price, or a
  confirmed market benchmark.
  - It never asks for less than the principal quoted.
  - It asks for at most a set number of extra points (25 by default).
  - Each item shows the reason.
- **Market benchmarks:** import an INAPROC e-Katalog, SIMO Inhealth or other price-list export
  (any headers, CSV or Excel).
  - Prices become price per piece incl. PPN.
  - Rows are matched to items by catalogue no., or by name and brand with size checks
    (22G ≠ 24G).
  - Strong matches are confirmed automatically, the rest wait for a person, and decisions are
    remembered by ERP code.
  - "Above market benchmark" joins Siloam's findings.
  - Principals never see benchmarks.

  The app imports files; it doesn't log in to those sites.
- **Online Nego:** record the meeting, then fill the agreed discount from Feedback I (else the
  counter offer) and edit what changed. If the agreed prices cost more than the MOU beyond the
  escalation limit (Engine settings), the tab says escalation is needed.
- **Submission package:** one Excel file with **Excel Confirmation**, **BAK Draft** (Nett or
  Disc layout by binding) and **Checks**.
  - It covers active items with an agreed price, A–Z, with no duplicate ERP codes.
  - It's marked DRAFT while anything is missing.
  - Siloam's own Confirmation and BAK templates can replace these layouts when they're provided.
- **Company documents:** at the last step the principal uploads NIB and NPWP (required), plus
  deeds, LoA and product registrations.
  - Files are **encrypted at rest** (Fernet; key from `NEGO_DOC_KEY`, or a workspace key file
    for pilots).
  - Only admins can download them, and every download is logged.

**Who can do what.** Everyone signed in can see the negotiations. Only admins can add principals,
open and prepare negotiations, edit items, decide findings, import, and move steps. **Sending the link automatically.** When an admin moves a negotiation to a principal step, the
app can send the principal a fresh link by email and WhatsApp.
- **How:** it posts one event to a **Power Automate** flow. The flow sends the Outlook email and
  a Meta-approved **WhatsApp Cloud API** template.
- **Reliability:** messages go through an outbox with retries. Admins see each one's status and
  can retry.
- **Setup:** set `NEGO_PUBLIC_URL` and `NEGO_NOTIFY_WEBHOOK_URL` on the server. The step-by-step
  guide, the JSON schema and the template text are in `deploy/POWER_AUTOMATE.md`.
- **Without it:** an admin copies the link from **Principal link**.
- **Deadline:** moving to a principal step sets a deadline (7 days by default).
- **Reminders:** they go out 3 days and 1 day before, then daily while late, until the principal
  sends. Each carries the same link.
- **Notices to Siloam:** when a principal sends a step, Siloam's team gets an email
  (`NEGO_NOTIFY_ADMINS`). Admins also get a weekly email listing MOUs that end within 6 months
  with no negotiation open.
- **Other WhatsApp providers:** an official partner of Meta (BSP) can replace Meta's API inside
  the flow. Unofficial WhatsApp gateways (QR-linked numbers) aren't suitable: numbers get banned
  and the links are confidential.

**Try it.** As an admin, open **Negotiations** and choose **Load sample principals**. That loads
6 fictional principals and opens 3 negotiations. One of them already has an RFQ filled the way
principals do, typical mistakes included. Sample files are in `data/nego_sample/`
(`python scripts/generate_nego_sample.py`).

The data is kept in its own SQLite file (`nego.db` in the workspace), separate from `app.db`, so
the confidential commercial data can be backed up and encrypted on its own.

## Tests

```bash
python scripts/generate_sample_data.py   # only needed if data/sample/ is missing
python -m pytest tests/ -q               # the full suite
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
  appdb.py          users, sessions, renewal progress, uploads, documents, audit (SQLite)
  datastore.py      admin uploads: validate, preview, versioned apply, rollback
  settings.py       admin-tunable engine settings, shared with the MCP server
  nlu.py            offline parser for the chat bar
  nego/             principal negotiation cycles: model (steps, template columns, formula), uom,
                    store (nego.db), prepare, template_io, anomalies, impact, service, demo,
                    checks (principal-side checks, ID/EN), portal (links, allowlists, send),
                    notify (outbox + webhook to Power Automate), negotiate (counter offer, Online Nego,
                    escalation, BAK/Confirmation package), benchmark (import + matching), vault (encrypted documents)
  templates/        Template_Nego.xlsx (the exact workbook used for export)
  dashboard/        Starlette app: auth.py, admin.py, nego_routes.py, portal_routes.py, app.py;
                    static/ shell, chat, nego, renewals, admin, principal (the portal)
scripts/
  generate_sample_data.py   deterministic synthetic price book
  generate_nego_sample.py   fictional principals, PO export, formulary and MOU tracker
  build_warehouse.py        builds data/warehouse.db
data/
  sample/           synthetic price book (marked SAMPLE_DATA)
  templates/        header-only CSVs describing the export
deploy/
  docker-compose.yml, SUPERSET.md, POWER_AUTOMATE.md
tests/
  test_engine.py        33 tests including workbook parity
  test_pricebook.py     loading, validation, UoM, index
  test_intelligence.py  targets beat every reference; counter-offers recompute through ENUC
  test_warehouse.py     warehouse parity with the kernel
  test_dashboard.py     API parity, JSON safety, read-only
  test_nego.py          UOM, template formula, anomaly rules, prepare, Template_Nego round trip, roles
  test_portal.py        principal checks, field/response allowlists, links, sending, locked Excel
  test_notify.py        automatic link sending through a stub webhook: payload, signature, retry, redaction
  test_negotiate.py     counter-offer rules, benchmark matching, Online Nego, escalation, package, encrypted documents
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
