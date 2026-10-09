"""Admin routes for item master data: list, label, bulk label, import and export.

All of ``/api/admin`` is admin-only (the auth middleware), and a negotiator is not let in:
labelling items is part of compiling master data, not of running a negotiation.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import appdb
from ..engine import EngineError
from ..nego import master
from .admin import _form_file
from .app import ok
from .nego_routes import _json, _who, handler

MAX_BYTES = 15 * 1024 * 1024


@handler
async def overview(_: Request) -> Response:
    return ok({"sets": master.overview(), "counts": master.counts()})


@handler
async def items(request: Request) -> Response:
    q = request.query_params
    return ok(master.list_items(q.get("filter", "all"), q.get("q", "")[:100], int(q.get("offset", 0)), int(q.get("limit", 50))))


@handler
async def item_update(request: Request) -> Response:
    body = await _json(request)
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise EngineError("Send the fields to change")
    res = master.update(request.path_params["erp"], changes, _who(request))
    if res["changed"]:
        appdb.audit(_who(request), "master.item", {"erp_code": res["item"]["erp_code"], **res["changed"]})
    return ok(res)


@handler
async def item_bulk(request: Request) -> Response:
    body = await _json(request)
    codes = body.get("erp_codes")
    if not isinstance(codes, list) or not all(isinstance(c, (str, int)) for c in codes):
        raise EngineError("Send the item codes as a list")
    tags_add = body.get("tags_add")
    res = master.bulk_update([str(c) for c in codes], _who(request), group_key=body.get("group_key"),
                             generic_name=body.get("generic_name"), tags_add=",".join(tags_add) if isinstance(tags_add, list) else tags_add,
                             status=body.get("status"))
    appdb.audit(_who(request), "master.bulk", {"selected": res["selected"], "updated": res["updated"],
                                               **{k: body[k] for k in ("group_key", "generic_name", "tags_add", "status") if body.get(k)}})
    return ok(res)


@handler
async def item_import(request: Request) -> Response:
    form, filename, content = await _form_file(request, MAX_BYTES)
    apply = str(form.get("apply", "0")) == "1"
    res = master.import_file(filename, content, apply, _who(request))
    if apply:
        appdb.audit(_who(request), "master.import", {"file": filename, "new": res["new"], "changed": res["changed"]})
    return ok(res)


@handler
async def item_export(_: Request) -> Response:
    return Response(master.export_xlsx(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="item_master.xlsx"'})


def routes() -> list[Route]:
    return [
        Route("/api/admin/master/overview", overview),
        Route("/api/admin/master/items", items),
        Route("/api/admin/master/items.xlsx", item_export),
        Route("/api/admin/master/items/bulk", item_bulk, methods=["POST"]),
        Route("/api/admin/master/items/import", item_import, methods=["POST"]),
        Route("/api/admin/master/items/{erp:path}", item_update, methods=["PATCH"]),
    ]
