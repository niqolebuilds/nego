"""The principal portal: ``/p/<token>`` opens it, ``/api/p/*`` serves it.

A principal never has a Siloam account. An admin creates a link for one negotiation;
opening it stores the link in an HttpOnly cookie (HMAC-signed, like the Siloam session)
and redirects to ``/p`` so the token leaves the address bar. Every ``/api/p`` call
resolves that cookie to exactly one open cycle; nothing here takes a cycle or principal
id from the request. Siloam's auth middleware lets ``/api/p`` through only for this
reason, and still requires the app header on every write.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse, Response
from starlette.routing import Route

from .. import appdb
from ..engine import EngineError
from ..nego import checks, portal, store
from . import auth
from .admin import _form_file
from .app import fail, ok

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "nego_principal"
MAX_BYTES = 25 * 1024 * 1024


def _access(request: Request) -> dict | None:
    token = auth.unsign(request.cookies.get(COOKIE))
    return portal.resolve(token) if token else None


def _who(acc: dict) -> str:
    return f"principal:{acc['cycle']['principal']['name']}"


def guarded(fn):
    async def run(request: Request) -> Response:
        acc = _access(request)
        if not acc:
            return fail(EngineError("Tautan tidak berlaku lagi. Minta tautan baru ke Siloam. / "
                                    "This link is no longer valid. Ask Siloam for a new one."), 401)
        store.touch_link(acc["link"]["id"])
        try:
            return await fn(request, acc)
        except EngineError as e:
            return fail(e, 404 if str(e).startswith("No such") else 400)
        except ValueError:
            return fail(EngineError("Invalid request"), 400)
    run.__name__ = fn.__name__
    return run


async def open_link(request: Request) -> Response:
    token = request.path_params["token"]
    acc = portal.resolve(token)
    if not acc:
        return RedirectResponse("/p?invalid=1", status_code=303)
    store.log_event(acc["cycle"]["id"], f"principal:{acc['cycle']['principal']['name']}", "link.open",
                    {"link": acc["link"]["id"]})
    resp = RedirectResponse("/p", status_code=303)
    resp.set_cookie(COOKIE, auth.sign(token), max_age=14 * 24 * 3600, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https", path="/")
    return resp


async def page(_: Request) -> Response:
    return FileResponse(STATIC / "principal.html")


@guarded
async def me(_: Request, acc: dict) -> Response:
    c = acc["cycle"]
    step = portal.open_step(c)
    p = c["principal"]
    return ok({
        "principal": {"name": p["name"], "distributor": p["distributor"]},
        "contract": {"start": c["contract_start"], "end": c["contract_end"], "binding": c["binding"]},
        "step": step, "current_step": c["current_step"], "step_no": portal.STEP_NUMBER.get(c["current_step"]),
        "submitted": c["submitted_steps"].get(c["current_step"]), "due": c["step_due"],
        "ppn": portal.ppn(), "thresholds": portal.thresholds(),
        "reasons": [{"key": k, "id": a, "en": b} for k, a, b in checks.REASONS],
        "link_expires": acc["link"]["expires_at"],
    })


@guarded
async def items(request: Request, acc: dict) -> Response:
    q = request.query_params
    c = acc["cycle"]
    step = portal.open_step(c) or c["current_step"]
    if step not in portal.PRINCIPAL_STEPS:
        return ok({"total": 0, "offset": 0, "items": [], "counts": {}})
    return ok(portal.items_page(c, step, q.get("filter", "all"), q.get("q", "")[:100],
                                max(0, int(q.get("offset", 0))), int(q.get("limit", 50))))


@guarded
async def item_save(request: Request, acc: dict) -> Response:
    body = await request.json()
    if not isinstance(body, dict):
        raise EngineError("Send a JSON object")
    changes = body.get("changes") or {}
    if not isinstance(changes, dict):
        raise EngineError("Send the changed fields")
    confirm = body.get("confirm")
    return ok(portal.save(acc["cycle"], int(request.path_params["iid"]), changes, _who(acc),
                          confirm=str(confirm) if confirm else None))


@guarded
async def fill(request: Request, acc: dict) -> Response:
    body = await request.json()
    ids = body.get("ids") if isinstance(body, dict) else None
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, int) for i in ids)):
        raise EngineError("ids must be a list of numbers")
    return ok({"filled": portal.fill_from_reference(acc["cycle"], _who(acc), ids)})


@guarded
async def summary(_: Request, acc: dict) -> Response:
    return ok(portal.summary(acc["cycle"]))


@guarded
async def submit(_: Request, acc: dict) -> Response:
    s = portal.submit(acc["cycle"], _who(acc))
    appdb.audit(_who(acc), "principal.submit", {"cycle": acc["cycle"]["id"], "step": s["step"]})
    return ok(s)


@guarded
async def template_get(_: Request, acc: dict) -> Response:
    name, data = portal.export(acc["cycle"])
    store.log_event(acc["cycle"]["id"], _who(acc), "principal.download")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@guarded
async def template_post(request: Request, acc: dict) -> Response:
    form, filename, content = await _form_file(request, MAX_BYTES)
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise EngineError("Unggah file Excel (.xlsx). / Upload the Excel file (.xlsx).")
    apply = str(form.get("apply", "")).lower() in ("1", "true", "yes")
    return ok(portal.import_excel(acc["cycle"], content, _who(acc), apply))


def routes() -> list[Route]:
    return [
        Route("/p", page),
        Route("/p/{token}", open_link),
        Route("/api/p/me", me),
        Route("/api/p/items", items),
        Route("/api/p/items/{iid:int}", item_save, methods=["PATCH"]),
        Route("/api/p/fill", fill, methods=["POST"]),
        Route("/api/p/summary", summary),
        Route("/api/p/submit", submit, methods=["POST"]),
        Route("/api/p/template.xlsx", template_get),
        Route("/api/p/template", template_post, methods=["POST"]),
    ]
