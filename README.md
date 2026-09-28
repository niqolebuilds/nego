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
pip install -r requirements.txt
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
| `negotiation_data_readiness` | — | What is still missing, and who owns it. |

Every tool is read-only, idempotent and closed-world: no external API, no database,
no writes. Data arrives as parameters.

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

## Tests

```bash
python -m pytest tests/ -q      # 33 unit tests
python tests/smoke_mcp.py       # end-to-end over stdio
```

The unit tests recompute the scenario from `Procurement_Negotiation_Engine_v0.xlsx`,
whose figures were independently verified, so the kernel and the workbook cannot
silently diverge.

## Layout

```
negotiation_mcp/
  engine.py       pure calculation kernel — no MCP, no I/O, fully unit-testable
  formatting.py   shared markdown/JSON formatting
  server.py       FastMCP tools, Pydantic validation, annotations
tests/
  test_engine.py  33 tests including workbook parity
  smoke_mcp.py    end-to-end client over stdio
evaluation.xml    10 evaluation questions
```

The kernel is deliberately separable: a vendor building the full product can take
`engine.py` unchanged and wrap it in a REST API, a batch job, or a UI.

## Not in scope

Approval workflow routing, three-way matching, SLA tracking, ticketing. Those are 12
of the 34 catalogue gaps and they are platform work. This server advises; it does not
route, approve, or write to any system of record.
