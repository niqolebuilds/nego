"""End-to-end check: start the server over stdio, list tools, call several."""
import asyncio, json, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main() -> int:
    sp = StdioServerParameters(command=sys.executable, args=["-m", "negotiation_mcp.server"], env=None)
    async with stdio_client(sp) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            tools = (await s.list_tools()).tools
            print(f"TOOLS ({len(tools)}):")
            for t in tools:
                req = t.inputSchema.get("required", []) if t.inputSchema else []
                print(f"  - {t.name}  (required: {req})")

            res = (await s.list_resources()).resources
            print(f"\nRESOURCES ({len(res)}): {[str(x.uri) for x in res]}")

            offer = {
                "vendor": "Vendor A", "sku_group": "Immunoassay reagent - Test X",
                "quoted_annual_quantity": 1200, "list_price_per_quoted_unit": 3900000,
                "clinical_units_per_quoted_unit": 50, "quoted_unit_name": "box-of-50",
                "on_invoice_discount": 0.10, "payment_terms_days": 45, "wastage_rate": 0.01,
                "instrument_capex_paid": 2400000000, "annual_service_cost": 180000000,
                "annual_logistics_cost": 25000000,
                "rebate_tiers": [
                    {"threshold_units": 50000, "rate": 0.010, "probability": 0.96},
                    {"threshold_units": 58000, "rate": 0.020, "probability": 0.62},
                    {"threshold_units": 66000, "rate": 0.030, "probability": 0.18},
                    {"threshold_units": 75000, "rate": 0.040, "probability": 0.03},
                ],
                "rebate_structure": "retro",
            }
            params = {"wacc": 0.12, "rebate_breakage_rate": 0.10}

            print("\n--- compute_enuc (json, quoted in boxes of 50) ---")
            out = await s.call_tool("negotiation_compute_enuc", {"params": {
                "offer": offer, "parameters": params, "response_format": "json"}})
            d = json.loads(out.content[0].text)
            print(f"  ENUC Ledger A = {d['enuc_ledger_a']:,.2f}  (workbook: 86,534.55)")
            assert abs(d["enuc_ledger_a"] - 86534.55) < 0.05, "diverged from workbook"

            print("\n--- verdict without reservation (must refuse) ---")
            out = await s.call_tool("negotiation_verdict", {"params": {
                "current_enuc": 86534, "target_enuc": 76000, "annual_units": 59400}})
            txt = out.content[0].text
            print("  ", txt[:150])
            assert "D-21" in txt, "should name the missing data element"

            print("\n--- verdict with reservation ---")
            out = await s.call_tool("negotiation_verdict", {"params": {
                "current_enuc": 86534, "target_enuc": 76000, "reservation_enuc": 88000,
                "batna_enuc": 81000, "annual_units": 59400, "response_format": "json"}})
            d = json.loads(out.content[0].text)
            print(f"   verdict={d['verdict']}  annual value of gap={d['annual_value_of_closing_gap']:,.0f}")

            print("\n--- price increase, net binding ---")
            out = await s.call_tool("negotiation_convert_price_increase", {"params": {
                "binding_type": "net", "old_list_price": 100000, "new_list_price": 110000,
                "old_discount": 0.20, "annual_quantity": 60000, "response_format": "json"}})
            d = json.loads(out.content[0].text)
            print(f"   discount must rise to {d['required_or_resulting_discount']:.2%}, "
                  f"net held at {d['new_net_price']:,.0f}")

            print("\n--- data readiness ---")
            out = await s.call_tool("negotiation_data_readiness", {"params": {"response_format": "json"}})
            print("  ", json.loads(out.content[0].text)["count"], "blocking elements the engine touches")

            print("\n--- resource read ---")
            rr = await s.read_resource("negotiation://parameters/default")
            print("  ", rr.contents[0].text[:90].replace("\n", " "), "...")

            print("\n--- price intelligence: brief, no offer ---")
            out = await s.call_tool("negotiation_brief", {"params": {
                "sku": "IVC22-PRI", "vendor": "Prima", "response_format": "json"}})
            d = json.loads(out.content[0].text)
            t = d["targets"]
            print("  ", d["headline"][:150], "...")
            assert d["is_sample"], "sample data must be flagged"
            assert all(t["target_price"] < r["price"] for r in t["references"]), "target must beat every reference"
            assert "PROPOSED" in t["walk_away_status"]

            print("\n--- price intelligence: brief with offer and sign-off ---")
            out = await s.call_tool("negotiation_brief", {"params": {
                "sku": "IVC22-PRI", "vendor": "Prima", "reservation_approved_by": "Heldra",
                "parameters": {"rebate_breakage_rate": 0.10}, "response_format": "json",
                "offer": {"vendor": "Prima Alkes", "sku_group": "IV cannula 22G",
                          "quoted_annual_quantity": 1050, "list_price_per_quoted_unit": 700000,
                          "clinical_units_per_quoted_unit": 50, "on_invoice_discount": 0.10}}})
            d = json.loads(out.content[0].text)
            print(f"   verdict={d['verdict']['verdict']}  packages={[p['name'] for p in d['counter_offer']['packages']]}")
            assert all(p["meets_target"] for p in d["counter_offer"]["packages"])

            print("\n--- savings, alerts, lookup (markdown) ---")
            for tool, args in (("negotiation_savings_opportunities", {"top_n": 3}),
                               ("negotiation_alerts", {"limit": 3}),
                               ("negotiation_price_lookup", {"search": "ceftriaxone"})):
                txt = (await s.call_tool(tool, {"params": args})).content[0].text
                assert not txt.startswith("Error"), txt
                assert "SAMPLE DATA" in txt
                print(f"   {tool}: {txt.splitlines()[2]}")

    print("\nSMOKE TEST PASSED")
    return 0

sys.exit(asyncio.run(main()))
