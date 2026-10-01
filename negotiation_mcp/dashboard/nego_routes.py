"""Principal negotiation routes.

Reading (``/api/nego/...``) is open to every signed-in Siloam user, like the rest of
the app. Changing anything (``/api/admin/nego/...``) needs an admin; the auth
middleware enforces that before a handler runs. The principal portal (one step at a
time, principal-only fields) is a separate set of routes in the next phase.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import appdb
from ..engine import EngineError
from ..nego import demo, portal, service, store
from .admin import _form_file
from .app import fail, ok

MAX_BYTES = 25 * 1024 * 1024


def _who(request: Request) -> str:
    return request.state.user["email"]


def _cid(request: Request) -> int:
    try:
        return int(request.path_params["cid"])
    except ValueError:
        raise EngineError("No such negotiation") from None


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise EngineError("Send JSON") from None
    if not isinstance(body, dict):
        raise EngineError("Send a JSON object")
    return body


def handler(fn):
    async def run(request: Request) -> Response:
        try:
            return await fn(request)
        except EngineError as e:
            return fail(e, 404 if str(e).startswith("No such") else 400)
        except ValueError:
            return fail(EngineError("Invalid request"), 400)
    run.__name__ = fn.__name__
    return run


# ---------------------------------------------------------------- read

@handler
async def principals(_: Request) -> Response:
    return ok(service.principals())


@handler
async def principal(request: Request) -> Response:
    p = store.get_principal(int(request.path_params["pid"]))
    if not p:
        raise EngineError("No such principal")
    return ok(p)


@handler
async def cycle(request: Request) -> Response:
    return ok(service.overview(_cid(request)))


@handler
async def cycle_items(request: Request) -> Response:
    q = request.query_params
    try:
        offset, limit = int(q.get("offset", 0)), int(q.get("limit", 50))
    except ValueError:
        raise EngineError("offset and limit must be numbers") from None
    return ok(service.item_page(_cid(request), q.get("q", "")[:100], q.get("filter", "all"), max(0, offset), limit,
                                q.get("sort", "sort")))


@handler
async def cycle_item(request: Request) -> Response:
    return ok(service.item_detail(_cid(request), int(request.path_params["iid"])))


@handler
async def cycle_anomalies(request: Request) -> Response:
    status = request.query_params.get("status", "open")
    if status not in ("", "open", "fixed", "kept", "cleared"):
        raise EngineError("Unknown status")
    return ok(service.anomaly_list(_cid(request), status))


@handler
async def cycle_export(request: Request) -> Response:
    cid = _cid(request)
    name, data = service.export_xlsx(cid)
    store.log_event(cid, _who(request), "template.export")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- admin

@handler
async def principal_create(request: Request) -> Response:
    p = store.create_principal(await _json(request), _who(request))
    appdb.audit(_who(request), "principal.create", {"id": p["id"], "name": p["name"]})
    return ok(p)


@handler
async def principal_update(request: Request) -> Response:
    pid = int(request.path_params["pid"])
    p = store.update_principal(pid, await _json(request), _who(request))
    appdb.audit(_who(request), "principal.update", {"id": pid})
    return ok(p)


@handler
async def principal_import(request: Request) -> Response:
    _, filename, content = await _form_file(request, MAX_BYTES)
    res = demo.import_principals(filename, content, _who(request))
    appdb.audit(_who(request), "principal.import", {"file": filename, "added": res["added"], "updated": res["updated"]})
    return ok(res)


@handler
async def sample_load(request: Request) -> Response:
    res = demo.seed(_who(request))
    appdb.audit(_who(request), "nego.sample", {"opened": [o["principal"] for o in res["opened"]]})
    return ok(res)


@handler
async def cycle_create(request: Request) -> Response:
    body = await _json(request)
    c = service.create_cycle(int(body.get("principal_id") or 0), body, _who(request))
    appdb.audit(_who(request), "cycle.create", {"id": c["id"], "principal": c["principal"]["name"]})
    return ok(c)


@handler
async def cycle_update(request: Request) -> Response:
    return ok(store.update_cycle(_cid(request), await _json(request), _who(request)))


@handler
async def cycle_prepare(request: Request) -> Response:
    cid = _cid(request)
    form = await request.form(max_files=3, max_fields=10)
    uploads = {}
    for kind in ("po", "formulary", "mou"):
        f = form.get(kind)
        if f is None or not hasattr(f, "read"):
            continue
        content = await f.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise EngineError(f"{f.filename} is larger than 25 MB")
        if content:
            uploads[kind] = (f.filename or kind, content)
    if not uploads:
        raise EngineError("Choose at least one file")
    res = service.prepare(cid, uploads, _who(request))
    appdb.audit(_who(request), "cycle.prepare", {"id": cid, "items": res["items"]})
    return ok(res)


@handler
async def cycle_import(request: Request) -> Response:
    cid = _cid(request)
    form, filename, content = await _form_file(request, MAX_BYTES)
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise EngineError("Upload the filled Template_Nego (.xlsx)")
    apply = str(form.get("apply", "")).lower() in ("1", "true", "yes")
    res = service.import_template(cid, content, _who(request), apply=apply)
    if apply:
        appdb.audit(_who(request), "template.import", {"id": cid, "file": filename, "items": res.get("applied_items")})
    return ok(res)


@handler
async def cycle_scan(request: Request) -> Response:
    cid = _cid(request)
    res = service.scan(cid)
    store.log_event(cid, _who(request), "scan", res)
    return ok({**res, "counts": store.anomaly_counts(cid)})


@handler
async def cycle_step(request: Request) -> Response:
    body = await _json(request)
    c = service.set_step(_cid(request), str(body.get("step", "")), _who(request), str(body.get("note", "")))
    return ok(c)


@handler
async def item_update(request: Request) -> Response:
    body = await _json(request)
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise EngineError("Send the changed fields")
    return ok(service.edit_item(_cid(request), int(request.path_params["iid"]), changes, _who(request)))


@handler
async def anomaly_decide(request: Request) -> Response:
    body = await _json(request)
    return ok(service.decide(_cid(request), int(request.path_params["aid"]), str(body.get("decision", "")),
                             str(body.get("reason", "")), _who(request)))


@handler
async def link_create(request: Request) -> Response:
    cid = _cid(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    link = portal.create_link(cid, _who(request), (body or {}).get("days"))
    appdb.audit(_who(request), "principal.link", {"cycle": cid, "link": link["id"]})
    return ok(link)


@handler
async def link_list(request: Request) -> Response:
    cid = _cid(request)
    store.require_cycle(cid)
    return ok({"links": store.links(cid)})


@handler
async def link_revoke(request: Request) -> Response:
    cid = _cid(request)
    store.revoke_link(cid, int(request.path_params["lid"]), _who(request))
    return ok({"links": store.links(cid)})


def routes() -> list[Route]:
    return [
        Route("/api/nego/principals", principals),
        Route("/api/nego/principals/{pid:int}", principal),
        Route("/api/nego/cycles/{cid}", cycle),
        Route("/api/nego/cycles/{cid}/items", cycle_items),
        Route("/api/nego/cycles/{cid}/items/{iid:int}", cycle_item),
        Route("/api/nego/cycles/{cid}/anomalies", cycle_anomalies),
        Route("/api/nego/cycles/{cid}/export.xlsx", cycle_export),
        Route("/api/admin/nego/principals", principal_create, methods=["POST"]),
        Route("/api/admin/nego/principals/import", principal_import, methods=["POST"]),
        Route("/api/admin/nego/principals/{pid:int}", principal_update, methods=["PATCH"]),
        Route("/api/admin/nego/sample", sample_load, methods=["POST"]),
        Route("/api/admin/nego/cycles", cycle_create, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}", cycle_update, methods=["PATCH"]),
        Route("/api/admin/nego/cycles/{cid}/prepare", cycle_prepare, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/import", cycle_import, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/scan", cycle_scan, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/step", cycle_step, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/items/{iid:int}", item_update, methods=["PATCH"]),
        Route("/api/admin/nego/cycles/{cid}/anomalies/{aid:int}", anomaly_decide, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/links", link_list),
        Route("/api/admin/nego/cycles/{cid}/links", link_create, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/links/{lid:int}/revoke", link_revoke, methods=["POST"]),
    ]
