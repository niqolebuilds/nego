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
from ..nego import assist, benchmark, demo, groups, negotiate, notify, portal, service, store, vault
from .admin import _form_file
from .app import _calendar, fail, ok

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


def _norm_vendor(name: str | None) -> str:
    """Company name without legal form, case or word order: "PT Alkes Prima" == "Prima Alkes"."""
    words = (name or "").lower().replace(".", " ").replace(",", " ").split()
    return " ".join(sorted(w for w in words if w not in ("pt", "cv", "tbk")))


@handler
async def mou_board(request: Request) -> Response:
    """Renewals: one card per principal MOU. Each card also lists the price-book SKUs whose
    vendor is that principal or its distributor, with the engine's targets."""
    q = request.query_params.get("max_days", "")
    board = service.mou_board(int(q) if q.strip() else None)
    try:
        skus = _calendar(0, 3650)["renewals"]
    except EngineError:
        skus = []
    by_vendor: dict[str, list[dict]] = {}
    for r in skus:
        by_vendor.setdefault(_norm_vendor(r["vendor"]), []).append(r)
    for p in board["mous"]:
        names = {_norm_vendor(p["name"]), _norm_vendor(p.get("distributor"))} - {""}
        rows = [r for n in names for r in by_vendor.get(n, [])]
        p["price_targets"] = {
            "skus": len({r["sku"] for r in rows}),
            "contract_value": sum(r["contract_value"] for r in rows),
            "saving_at_target": sum(max(r["saving_at_target"], 0) for r in rows),
            "items": [{k: r.get(k) for k in ("key", "sku", "sku_name", "vendor", "hospital", "contract_end", "days_left",
                                             "contract_price_per_clinical_unit", "opening_ask", "target_price",
                                             "fallback_price", "proposed_walk_away", "saving_at_target",
                                             "quoted_unit", "clinical_units_per_quoted_unit")}
                      for r in sorted(rows, key=lambda r: -r["saving_at_target"])[:50]],
        }
    k = board["kpis"]
    k["saving_at_target"] = sum(p["price_targets"]["saving_at_target"] for p in board["mous"] if p["stage"] != "renewed")
    return ok(board)


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
    c = service.set_step(_cid(request), str(body.get("step", "")), _who(request), str(body.get("note", "")),
                         send_link=bool(body.get("send_link", False)), due=body.get("due") or None)
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


@handler
async def link_send(request: Request) -> Response:
    cid = _cid(request)
    res = notify.step_opened(cid, _who(request))
    appdb.audit(_who(request), "principal.link.send", {"cycle": cid, "status": res["status"]})
    return ok(res)


@handler
async def message_list(request: Request) -> Response:
    cid = _cid(request)
    store.require_cycle(cid)
    return ok({"messages": store.messages(cid), "automation": notify.status()})


@handler
async def message_retry(request: Request) -> Response:
    return ok(notify.retry(_cid(request), int(request.path_params["mid"])))


@handler
async def notify_status(_: Request) -> Response:
    return ok(notify.status())


@handler
async def notify_test(request: Request) -> Response:
    res = notify.send_test(_who(request))
    appdb.audit(_who(request), "notify.test", res)
    return ok(res)


# ---------------------------------------------------------------- benchmarks

@handler
async def benchmark_sources(_: Request) -> Response:
    return ok({"sources": store.benchmark_sources()})


@handler
async def benchmark_upload(request: Request) -> Response:
    form, filename, content = await _form_file(request, MAX_BYTES)
    source = str(form.get("source") or "Benchmark").strip()[:60] or "Benchmark"
    incl = str(form.get("incl_ppn", "1")).lower() in ("1", "true", "yes", "on")
    res = benchmark.read_file(filename, content, source, incl, service.ppn(), _who(request))
    appdb.audit(_who(request), "benchmark.import", {"file": filename, "source": source, "rows": res["rows"]})
    return ok(res)


@handler
async def benchmark_match(request: Request) -> Response:
    cid = _cid(request)
    store.require_cycle(cid)
    res = benchmark.match_cycle(cid)
    res["scan"] = service.scan(cid)
    return ok(res)


@handler
async def cycle_groups(request: Request) -> Response:
    """The cycle's items compared across brands of the same generic name or group."""
    cid = _cid(request)
    store.require_cycle(cid)
    return ok(groups.analyse(store.items(cid), service.ppn(), service.thresholds()["group_spread"]))


@handler
async def benchmark_matches(request: Request) -> Response:
    cid = _cid(request)
    store.require_cycle(cid)
    status = request.query_params.get("status", "suggested")
    if status not in ("", "suggested", "confirmed", "rejected"):
        raise EngineError("Unknown status")
    return ok({"matches": store.matches(cid, status or None)[:500]})


@handler
async def benchmark_decide(request: Request) -> Response:
    cid = _cid(request)
    body = await _json(request)
    status = {"confirm": "confirmed", "reject": "rejected", "undo": "suggested"}.get(str(body.get("decision")))
    if not status:
        raise EngineError("Decision must be confirm, reject or undo")
    store.decide_match(cid, int(request.path_params["mid"]), status, _who(request))
    service.scan(cid)
    return ok({"ok": True})


# ---------------------------------------------------------------- counter offer, online nego, package

@handler
async def co_plan(request: Request) -> Response:
    plan = negotiate.plan_co(_cid(request), request.query_params.get("overwrite") == "1")
    plan["suggestions"] = plan["suggestions"][:300]
    return ok(plan)


@handler
async def co_apply(request: Request) -> Response:
    cid = _cid(request)
    body = await _json(request)
    res = negotiate.apply_co(cid, _who(request), bool(body.get("overwrite")))
    res["scan"] = service.scan(cid)
    return ok(res)


@handler
async def on_fill(request: Request) -> Response:
    cid = _cid(request)
    body = await _json(request)
    res = negotiate.fill_on(cid, _who(request), bool(body.get("overwrite")))
    res["scan"] = service.scan(cid)
    return ok(res)


@handler
async def package_checks(request: Request) -> Response:
    chk = negotiate.package_checks(_cid(request))
    chk["agreed"] = len(chk["agreed"])
    return ok(chk)


@handler
async def package_xlsx(request: Request) -> Response:
    cid = _cid(request)
    name, data, _ = negotiate.package_xlsx(cid)
    appdb.audit(_who(request), "package.export", {"cycle": cid})
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@handler
async def cycle_documents(request: Request) -> Response:
    cid = _cid(request)
    store.require_cycle(cid)
    labels = {k: b for k, a, b, r in vault.DOC_TYPES}
    docs = [{**{k: d[k] for k in ("id", "doc_type", "filename", "size", "uploaded_at", "uploaded_by", "sha256")},
             "label": labels.get(d["doc_type"], d["doc_type"])} for d in store.documents(cid)]
    return ok({"documents": docs, "required": sorted(vault.REQUIRED)})


@handler
async def cycle_document_get(request: Request) -> Response:
    cid = _cid(request)
    d = store.get_document(cid, int(request.path_params["did"]))
    if not d:
        raise EngineError("No such document")
    data = vault.load(cid, d["stored_name"])
    store.log_event(cid, _who(request), "document.view", {"id": d["id"], "file": d["filename"]})
    appdb.audit(_who(request), "document.view", {"cycle": cid, "id": d["id"]})
    return Response(data, media_type=d["content_type"] or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{d["filename"]}"', "Content-Security-Policy": "sandbox",
                             "Cache-Control": "no-store"})


@handler
async def assist_status(_: Request) -> Response:
    return ok(assist.status())


@handler
async def assist_draft(request: Request) -> Response:
    body = await _json(request)
    cid = _cid(request)
    res = assist.draft(cid, str(body.get("kind", "")), _who(request))
    appdb.audit(_who(request), "assist.draft", {"cycle": cid, "kind": res["kind"]})
    return ok(res)


def routes() -> list[Route]:
    return [
        Route("/api/nego/principals", principals),
        Route("/api/nego/mou-board", mou_board),
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
        Route("/api/admin/nego/cycles/{cid}/links/send", link_send, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/messages", message_list),
        Route("/api/admin/nego/cycles/{cid}/messages/{mid:int}/retry", message_retry, methods=["POST"]),
        Route("/api/admin/nego/notify", notify_status),
        Route("/api/admin/nego/assist", assist_status),
        Route("/api/admin/nego/cycles/{cid}/assist", assist_draft, methods=["POST"]),
        Route("/api/nego/benchmarks", benchmark_sources),
        Route("/api/admin/nego/benchmarks", benchmark_upload, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/benchmarks/match", benchmark_match, methods=["POST"]),
        Route("/api/nego/cycles/{cid}/benchmarks", benchmark_matches),
        Route("/api/nego/cycles/{cid}/groups", cycle_groups),
        Route("/api/admin/nego/cycles/{cid}/benchmarks/{mid:int}", benchmark_decide, methods=["POST"]),
        Route("/api/nego/cycles/{cid}/co-plan", co_plan),
        Route("/api/admin/nego/cycles/{cid}/co", co_apply, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/on-fill", on_fill, methods=["POST"]),
        Route("/api/nego/cycles/{cid}/package", package_checks),
        Route("/api/admin/nego/cycles/{cid}/package.xlsx", package_xlsx),
        Route("/api/admin/nego/cycles/{cid}/documents", cycle_documents),
        Route("/api/admin/nego/cycles/{cid}/documents/{did:int}", cycle_document_get),
        Route("/api/admin/nego/notify/test", notify_test, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/links", link_create, methods=["POST"]),
        Route("/api/admin/nego/cycles/{cid}/links/{lid:int}/revoke", link_revoke, methods=["POST"]),
    ]
