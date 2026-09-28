"""Starlette app serving the dashboard and its JSON API.

Every endpoint calls the same ``intelligence`` functions as the MCP tools, so the
dashboard and Claude always quote the same numbers. Read-only: there is no write
endpoint, and it binds to 127.0.0.1 unless told otherwise.

    python -m negotiation_mcp.dashboard [--host 127.0.0.1] [--port 8080] [--breakage 0.10]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import engine as E
from .. import intelligence as I
from ..formatting import to_json
from ..pricebook import SAMPLE_BANNER
from ..pricebook import cached_book as get_book

STATIC = Path(__file__).resolve().parent / "static"
STATE: dict[str, Any] = {"breakage": None}


def _clean(o: Any) -> Any:
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def ok(payload: Any) -> Response:
    data = _clean(json.loads(to_json(payload)))
    return Response(json.dumps(data, allow_nan=False, ensure_ascii=False), media_type="application/json")


def fail(e: Exception, status: int = 400) -> Response:
    return Response(json.dumps({"error": str(e)}), status_code=status, media_type="application/json")


def params() -> E.Parameters:
    return E.Parameters(rebate_breakage_rate=STATE["breakage"])


async def index(_: Request) -> Response:
    return FileResponse(STATIC / "index.html")


async def api_status(_: Request) -> Response:
    try:
        book = get_book()
        return ok({**book.status(), "banner": SAMPLE_BANNER if book.is_sample else None, "currency": book.currency})
    except Exception as e:  # noqa: BLE001
        return fail(e, 500)


async def api_overview(_: Request) -> Response:
    try:
        book = get_book()
        opps = I.savings_opportunities(book, top_n=0)
        vendors = I.vendor_spend(book)
        alerts = I.alerts(book, params=params())
        spend = sum(v["spend_12m"] for v in vendors)
        identified = sum(r["total_opportunity"] for r in opps)
        internal = sum(r["internal_price_variance"] for r in opps)
        return ok(
            {
                "kpis": {
                    "spend_12m": spend,
                    "savings_identified": identified,
                    "savings_pct_of_spend": identified / spend if spend else 0.0,
                    "internal_price_variance": internal,
                    "alerts_high": sum(1 for a in alerts if a["severity"] == "high"),
                    "alerts_total": len(alerts),
                },
                "opportunities": opps[:10],
                "alerts": alerts[:8],
                "vendors": vendors,
                "as_of": book.latest_date.isoformat(),
                "currency": book.currency,
            }
        )
    except Exception as e:  # noqa: BLE001
        return fail(e, 500)


async def api_skus(_: Request) -> Response:
    book = get_book()
    rows = [
        {"sku": s.sku, "sku_name": s.sku_name, "equivalence_group": s.equivalence_group,
         "vendors": sorted({o.vendor for o in book.observations if o.sku == s.sku}),
         "single_source": s.single_source}
        for s in sorted(book.skus.values(), key=lambda s: (s.equivalence_group, s.sku_name))
    ]
    return ok(rows)


async def api_sku(request: Request) -> Response:
    try:
        book = get_book()
        sku = request.path_params["sku"]
        by = request.query_params.get("by", "vendor")
        return ok({"benchmark": I.benchmark(book, sku), "trend": I.price_trend(book, sku, by=by)})
    except E.EngineError as e:
        return fail(e, 404)


async def api_vendors(_: Request) -> Response:
    from ..warehouse import vendor_price_rows

    book = get_book()
    return ok({"spend": I.vendor_spend(book), "prices": vendor_price_rows(book, book.latest_date)})


async def api_alerts(_: Request) -> Response:
    return ok(I.alerts(get_book(), params=params()))


async def api_brief(request: Request) -> Response:
    q = request.query_params
    try:
        if not q.get("sku") or not q.get("vendor"):
            raise E.EngineError("sku and vendor are required")
        res = I.negotiation_brief(get_book(), q["sku"], q["vendor"], params(),
                                  reservation_approved_by=q.get("approved_by") or None)
        return ok(res)
    except E.EngineError as e:
        return fail(e)


def create_app(breakage: float | None = None) -> Starlette:
    STATE["breakage"] = breakage
    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/status", api_status),
            Route("/api/overview", api_overview),
            Route("/api/skus", api_skus),
            Route("/api/sku/{sku}", api_sku),
            Route("/api/vendors", api_vendors),
            Route("/api/alerts", api_alerts),
            Route("/api/brief", api_brief),
            Mount("/static", StaticFiles(directory=STATIC), name="static"),
        ]
    )


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Negotiation price-intelligence dashboard (read-only)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--breakage", type=float, default=None, help="Rebate breakage rate (D-18) for rebate alerts")
    args = ap.parse_args()
    uvicorn.run(create_app(args.breakage), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
