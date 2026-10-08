"""Starlette app serving the negotiation app and its JSON API.

Every endpoint calls the same ``intelligence`` functions as the MCP tools, so the app
and Claude always quote the same numbers. All ``/api`` routes need a signed-in user;
``/api/admin`` routes need an admin (see ``auth.py``). Engine settings come from the
admin-managed settings file. It binds to 127.0.0.1 unless told otherwise.

    python -m negotiation_mcp.dashboard [--host 127.0.0.1] [--port 8080]
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from starlette.middleware import Middleware

from .. import appdb, nlu
from .. import engine as E
from .. import intelligence as I
from .. import settings as S
from ..formatting import to_json
from ..pricebook import SAMPLE_BANNER
from ..pricebook import cached_book as get_book
from . import auth

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


def cfg() -> dict:
    """Admin-managed engine settings. A --breakage flag on the command line fills D-18 if unset."""
    values = S.load()
    if values["rebate_breakage_rate"] is None and STATE["breakage"] is not None:
        values["rebate_breakage_rate"] = STATE["breakage"]
    return values


def params() -> E.Parameters:
    return S.parameters(cfg())


def alert_kwargs(v: dict | None = None) -> dict:
    v = v or cfg()
    return {"creep_threshold": v["creep_threshold"], "variance_threshold": v["variance_threshold"]}


async def index(_: Request) -> Response:
    return FileResponse(STATIC / "index.html")


async def analytics(_: Request) -> Response:
    return FileResponse(STATIC / "analytics.html")


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
        alerts = I.alerts(book, params=params(), **alert_kwargs())
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
    return ok(I.alerts(get_book(), params=params(), **alert_kwargs()))


async def api_brief(request: Request) -> Response:
    q = request.query_params
    try:
        if not q.get("sku") or not q.get("vendor"):
            raise E.EngineError("sku and vendor are required")
        v = cfg()
        res = I.negotiation_brief(get_book(), q["sku"], q["vendor"], params(), beat_margin=v["beat_margin"],
                                  anchor_margin=v["anchor_margin"], reservation_approved_by=q.get("approved_by") or None)
        return ok(res)
    except E.EngineError as e:
        return fail(e)


def _brief(q, with_offer: bool) -> dict:
    if not q.get("sku") or not q.get("vendor"):
        raise E.EngineError("Choose a target clinical SKU and a vendor")
    book = get_book()
    offer = I.offer_from_history(book, q["sku"], q["vendor"]) if with_offer else None
    v = cfg()
    res = I.negotiation_brief(book, q["sku"], q["vendor"], S.parameters(v), offer=offer,
                              beat_margin=v["beat_margin"], anchor_margin=v["anchor_margin"],
                              reservation_approved_by=q.get("approved_by") or None)
    res["offer_from_history"] = offer is not None
    if offer is not None:
        res["current_offer"] = {
            "quoted_unit": offer.uom.quoted_unit,
            "clinical_units_per_quoted_unit": offer.uom.clinical_units_per_quoted_unit,
            "list_price_per_quoted_unit": offer.list_price_per_quoted_unit,
            "on_invoice_discount": offer.on_invoice_discount,
            "payment_terms_days": offer.payment_terms_days,
            "invoice_price_per_clinical_unit": offer.invoice_price_per_clinical_unit,
            "annual_clinical_units": offer.clinical_units_paid,
        }
    return res


async def api_catalog(_: Request) -> Response:
    """SKUs and vendors for the two form fields, with who supplies what."""
    book = get_book()
    supplied: dict[str, set] = {}
    for o in book.observations:
        supplied.setdefault(o.sku, set()).add(o.vendor)
    skus = []
    for s in sorted(book.skus.values(), key=lambda s: (s.equivalence_group, s.sku_name)):
        group_vendors = sorted({v for g in book.group_skus(s.equivalence_group) for v in supplied.get(g.sku, ())})
        skus.append({"sku": s.sku, "sku_name": s.sku_name, "equivalence_group": s.equivalence_group,
                     "vendors": sorted(supplied.get(s.sku, ())), "group_vendors": group_vendors,
                     "single_source": s.single_source})
    v = cfg()
    return ok({"skus": skus, "vendors": sorted({o.vendor for o in book.observations}),
               "hospitals": sorted({o.hospital for o in book.observations}),
               "renewal_window": [v["renewal_min_days"], v["renewal_max_days"]],
               "as_of": book.latest_date.isoformat(), "is_sample": book.is_sample,
               "banner": SAMPLE_BANNER if book.is_sample else None, "currency": book.currency})


async def api_options(request: Request) -> Response:
    """Three price options for one SKU and vendor, with the evidence behind them."""
    try:
        return ok(_brief(request.query_params, with_offer=False))
    except E.EngineError as e:
        return fail(e)


async def api_negotiate(request: Request) -> Response:
    """Negotiation plan: the vendor's current deal rebuilt from history, and what to trade."""
    try:
        return ok(_brief(request.query_params, with_offer=True))
    except E.EngineError as e:
        return fail(e)


def _calendar(lo: int, hi: int) -> dict:
    v = cfg()
    cal = I.renewal_calendar(get_book(), min_days=lo, max_days=hi, params=S.parameters(v),
                             beat_margin=v["beat_margin"], anchor_margin=v["anchor_margin"], alert_kwargs=alert_kwargs(v))
    progress = appdb.all_progress()
    for r in cal["renewals"]:
        p = progress.get(r["key"]) or {"stage": "not_started"}
        r["progress"] = p
        if p.get("latest_offer"):
            r["offer_check"] = I.assess_offer(p["latest_offer"], r["target_price"], r["fallback_price"],
                                              r["proposed_walk_away"], r["opening_ask"])
        if p.get("stage") == "agreed" and p.get("agreed_price"):
            r["realised_saving"] = (r["contract_price_per_clinical_unit"] - p["agreed_price"]) * r["site_annual_units"]
    return cal


async def api_renewals(request: Request) -> Response:
    q = request.query_params
    try:
        v = cfg()
        lo = int(q.get("min_days", v["renewal_min_days"]))
        hi = int(q.get("max_days", v["renewal_max_days"]))
        return ok(_calendar(lo, hi))
    except (ValueError, E.EngineError) as e:
        return fail(e)


def _pipeline_kpis(renewals: list[dict]) -> dict:
    agreed = [r for r in renewals if r["progress"].get("stage") == "agreed"]
    open_ = [r for r in renewals if r["progress"].get("stage") not in ("agreed", "lost")]
    return {
        "open": len(open_),
        "pipeline_value": sum(r["contract_value"] for r in open_),
        "saving_at_target": sum(r["saving_at_target"] for r in open_),
        "realised_saving": sum(r.get("realised_saving", 0.0) or 0.0 for r in agreed),
        "agreed": len(agreed),
        "by_stage": {k: sum(1 for r in renewals if r["progress"].get("stage", "not_started") == k)
                     for k in appdb.STAGE_KEYS},
    }


async def api_pipeline(request: Request) -> Response:
    """Board view: every contract ending in the window, with its stage and progress."""
    q = request.query_params
    try:
        lo, hi = int(q.get("min_days", 0)), int(q.get("max_days", 180))
        cal = _calendar(lo, hi)
        mine = q.get("mine") == "1"
        if mine:
            me = request.state.user["email"]
            cal["renewals"] = [r for r in cal["renewals"] if (r["progress"].get("owner") or "").lower() == me]
        cal["kpis"] = _pipeline_kpis(cal["renewals"])
        cal["stages"] = [{"key": k, "label": label} for k, label in appdb.STAGES]
        return ok(cal)
    except (ValueError, E.EngineError) as e:
        return fail(e)


async def api_progress(request: Request) -> Response:
    """Update one renewal's stage, owner, next step, notes or prices. Any signed-in user."""
    try:
        body = await request.json()
        key = body.pop("key", None)
        cal = _calendar(0, 3650)
        renewal = next((r for r in cal["renewals"] if r["key"] == key), None)
        if renewal is None:
            raise E.EngineError("That renewal isn't in the current data")
        progress, events = appdb.update_progress(key, body, request.state.user["email"])
        out = {"progress": progress, "events": events}
        if progress.get("latest_offer"):
            out["offer_check"] = I.assess_offer(progress["latest_offer"], renewal["target_price"], renewal["fallback_price"],
                                                renewal["proposed_walk_away"], renewal["opening_ask"])
        return ok(out)
    except (ValueError, E.EngineError) as e:
        return fail(e)


async def api_events(request: Request) -> Response:
    return ok(appdb.events(request.query_params.get("key", "")))


async def api_home(request: Request) -> Response:
    """Greeting strip: my renewals due soon, high alerts, savings realised."""
    me = request.state.user["email"]
    cal = _calendar(0, 3650)
    mine_soon = [r for r in cal["renewals"] if (r["progress"].get("owner") or "").lower() == me and r["days_left"] <= 14]
    due_soon = [r for r in cal["renewals"] if r["days_left"] <= 30 and r["progress"].get("stage") not in ("agreed", "lost")]
    high = [a for a in I.alerts(get_book(), params=params(), **alert_kwargs()) if a["severity"] == "high"]
    return ok({
        "user": auth.public_user(request.state.user),
        "my_due_14d": len(mine_soon),
        "due_30d_open": len(due_soon),
        "high_alerts": len(high),
        "realised_saving": sum(r.get("realised_saving", 0.0) or 0.0 for r in cal["renewals"]),
    })


async def api_vendor(request: Request) -> Response:
    try:
        return ok(I.vendor_profile(get_book(), request.path_params["name"], params=params()))
    except E.EngineError as e:
        return fail(e, 404)


async def api_lookup(request: Request) -> Response:
    q = request.query_params
    try:
        return ok(I.lookup(get_book(), text=q.get("text") or None, vendor=q.get("vendor") or None,
                           hospital=q.get("hospital") or None, limit=30))
    except E.EngineError as e:
        return fail(e, 404)


async def api_savings(_: Request) -> Response:
    book = get_book()
    return ok({"rows": I.savings_opportunities(book, top_n=8), "currency": book.currency})


async def api_chat(request: Request) -> Response:
    """Free text in, a structured intent out. The page then calls the matching endpoint."""
    try:
        body = await request.json()
        text = str(body.get("text", ""))[:500]
        parsed = nlu.parse(text, nlu.catalog_from_book(get_book())).as_dict()
        v = cfg()
        parsed["llm_fallback"] = {
            "enabled": bool(v["llm_fallback"]),
            "available": False,
            "note": "Claude fallback is on the roadmap; answers come from the built-in parser.",
        }
        return ok(parsed)
    except (ValueError, E.EngineError) as e:
        return fail(e)


async def api_settings(_: Request) -> Response:
    """Everyone can see how the engine is configured; only admins can change it."""
    return ok({"settings": S.describe()})


async def api_documents(request: Request) -> Response:
    q = request.query_params
    return ok(appdb.list_documents(q.get("vendor") or None, q.get("sku") or None, q.get("renewal") or None,
                                   q.get("text") or None))


async def api_document_download(request: Request) -> Response:
    from .admin import document_response

    return document_response(request.path_params["doc_id"])


# ---------------------------------------------------------------- sign-in

def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def signin(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    ip = _client(request)
    if auth.signin_blocked(ip):
        return fail(E.EngineError("Too many attempts. Wait 15 minutes and try again."), 429)
    user = auth.PROVIDER.authenticate(str(body.get("email", "")), str(body.get("password") or ""))
    if not user:
        auth.signin_failed(ip)
        appdb.audit(str(body.get("email", ""))[:200] or "unknown", "signin.failed")
        msg = ("Wrong email or password. If you haven't set a password yet, use the link your admin sent you."
               if auth.PROVIDER.password_required else
               "That email isn't registered, or the account is disabled. Ask an admin to invite you.")
        return fail(E.EngineError(msg), 401)
    sid = appdb.create_session(user["id"])
    appdb.audit(user["email"], "signin")
    resp = ok({"user": auth.public_user(user)})
    auth.set_session_cookie(resp, request, sid)
    return resp


async def setup_password(request: Request) -> Response:
    """GET: who a setup link is for. POST: set the password from the link, then sign in."""
    if request.method == "GET":
        user = appdb.user_by_setup_token(request.query_params.get("token", ""))
        if not user:
            return fail(E.EngineError("This link has expired or was already used. Ask an admin for a new one."), 404)
        return ok({"email": user["email"], "name": user["name"], "min_length": appdb.MIN_PASSWORD})
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    ip = _client(request)
    if auth.signin_blocked(ip):
        return fail(E.EngineError("Too many attempts. Wait 15 minutes and try again."), 429)
    user = appdb.user_by_setup_token(str(body.get("token", "")))
    if not user:
        auth.signin_failed(ip)
        return fail(E.EngineError("This link has expired or was already used. Ask an admin for a new one."), 404)
    try:
        appdb.set_password(user["id"], str(body.get("password") or ""), user["email"])
    except E.EngineError as e:
        return fail(e)
    sid = appdb.create_session(user["id"])
    appdb.audit(user["email"], "signin")
    resp = ok({"user": auth.public_user(user)})
    auth.set_session_cookie(resp, request, sid)
    return resp


async def signout(request: Request) -> Response:
    sid = auth.unsign(request.cookies.get(auth.COOKIE))
    if sid:
        appdb.delete_session(sid)
    resp = ok({"ok": True})
    auth.clear_session_cookie(resp)
    return resp


async def me(request: Request) -> Response:
    user = request.state.user
    return ok({"user": auth.public_user(user) if user else None,
               "provider": {"name": auth.PROVIDER.name, "password_required": auth.PROVIDER.password_required,
                            "notice": auth.PROVIDER.notice}})


def create_app(breakage: float | None = None) -> Starlette:
    from ..nego import store as nego_store
    from . import admin, master_routes, nego_routes, portal_routes

    STATE["breakage"] = breakage
    first_token = appdb.init()
    if first_token:
        base = (os.environ.get("NEGO_PUBLIC_URL") or "http://localhost:8080").rstrip("/")
        print(f"\n  First admin created. Set the password here (valid {appdb.SETUP_LINK_DAYS} days):\n"
              f"  {base}/#setpw/{first_token}\n", flush=True)
    nego_store.init()
    from ..nego import notify

    notify.start_sender()
    return Starlette(
        middleware=[Middleware(auth.AuthMiddleware)],
        routes=[
            Route("/", index),
            Route("/analytics", analytics),
            Route("/api/auth/signin", signin, methods=["POST"]),
            Route("/api/auth/signout", signout, methods=["POST"]),
            Route("/api/auth/setup", setup_password, methods=["GET", "POST"]),
            Route("/api/auth/me", me),
            Route("/api/home", api_home),
            Route("/api/chat", api_chat, methods=["POST"]),
            Route("/api/catalog", api_catalog),
            Route("/api/options", api_options),
            Route("/api/negotiate", api_negotiate),
            Route("/api/renewals", api_renewals),
            Route("/api/pipeline", api_pipeline),
            Route("/api/renewals/progress", api_progress, methods=["PATCH"]),
            Route("/api/renewals/events", api_events),
            Route("/api/vendor/{name}", api_vendor),
            Route("/api/lookup", api_lookup),
            Route("/api/savings", api_savings),
            Route("/api/settings", api_settings),
            Route("/api/documents", api_documents),
            Route("/api/documents/{doc_id}/download", api_document_download),
            Route("/api/status", api_status),
            Route("/api/overview", api_overview),
            Route("/api/skus", api_skus),
            Route("/api/sku/{sku}", api_sku),
            Route("/api/vendors", api_vendors),
            Route("/api/alerts", api_alerts),
            Route("/api/brief", api_brief),
            *admin.routes(),
            *nego_routes.routes(),
            *master_routes.routes(),
            *portal_routes.routes(),
            Mount("/static", StaticFiles(directory=STATIC), name="static"),
        ],
    )


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Negotiation intelligence web app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--breakage", type=float, default=None,
                    help="Rebate breakage rate (D-18) if not set in the admin settings")
    args = ap.parse_args()
    uvicorn.run(create_app(args.breakage), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
