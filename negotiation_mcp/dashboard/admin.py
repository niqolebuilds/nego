"""Admin-only routes: data uploads and versions, documents, engine settings, users, activity.

The auth middleware already rejects non-admins for every ``/api/admin`` path; each
handler still reads the acting user from ``request.state.user`` for the audit trail.
Documents are the one shared area: every signed-in user can list and download them,
only admins can upload or delete.
"""

from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from .. import appdb, datastore
from .. import settings as S
from ..engine import EngineError
from ..pricebook import cached_book
from .app import fail, ok

DOC_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
MAX_DOC_BYTES = 20 * 1024 * 1024


def _who(request: Request) -> str:
    return request.state.user["email"]


def _docs_dir() -> Path:
    d = S.workspace() / "documents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(name: str) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[^\w.\- ()]", "_", base).strip() or "file"
    return base[:150]


async def _form_file(request: Request, max_bytes: int):
    form = await request.form(max_files=1, max_fields=20)
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise EngineError("Choose a file to upload")
    content = await upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise EngineError(f"File is larger than {max_bytes // (1024 * 1024)} MB")
    if not content:
        raise EngineError("The file is empty")
    return form, upload.filename or "upload", content


# ---------------------------------------------------------------- data

async def upload_stage(request: Request) -> Response:
    try:
        form, filename, content = await _form_file(request, datastore.MAX_UPLOAD_BYTES)
        return ok(datastore.stage_upload(str(form.get("kind", "price_history")), filename, content, _who(request)))
    except EngineError as e:
        return fail(e)


async def upload_apply(request: Request) -> Response:
    try:
        body = await request.json()
        res = datastore.apply_upload(request.path_params["uid"], str(body.get("mode", "add")), _who(request),
                                     str(body.get("note", ""))[:200])
        cached_book()  # reload now so the next question sees the new data
        return ok(res)
    except (ValueError, EngineError) as e:
        return fail(e)


async def upload_discard(request: Request) -> Response:
    try:
        datastore.discard_upload(request.path_params["uid"], _who(request))
        return ok({"ok": True})
    except EngineError as e:
        return fail(e)


async def uploads(_: Request) -> Response:
    return ok({"uploads": appdb.list_uploads(), "kinds": {k: v[3] for k, v in datastore.KINDS.items()}})


async def versions(_: Request) -> Response:
    data = datastore.versions()
    try:
        data["status"] = cached_book().status()
    except EngineError as e:
        data["status"] = {"error": str(e)}
    return ok(data)


async def version_activate(request: Request) -> Response:
    try:
        res = datastore.activate_version(request.path_params["vid"], _who(request))
        cached_book()
        return ok(res)
    except EngineError as e:
        return fail(e)


async def template(request: Request) -> Response:
    try:
        kind = request.path_params["kind"]
        return Response(datastore.template(kind), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{kind}_template.csv"'})
    except EngineError as e:
        return fail(e, 404)


# ---------------------------------------------------------------- documents

async def document_upload(request: Request) -> Response:
    try:
        form, filename, content = await _form_file(request, MAX_DOC_BYTES)
        safe = _safe_filename(filename)
        ext = Path(safe).suffix.lower()
        if ext not in DOC_TYPES:
            raise EngineError(f"Allowed types: {', '.join(sorted(DOC_TYPES))}")
        if ext == ".pdf" and not content.startswith(b"%PDF"):
            raise EngineError("That file doesn't look like a PDF")
        doc_id = uuid.uuid4().hex
        stored = f"{doc_id}{ext}"
        (_docs_dir() / stored).write_bytes(content)

        def field(name: str) -> str | None:
            v = str(form.get(name, "") or "").strip()
            return v[:300] or None

        doc = appdb.add_document({
            "id": doc_id, "at": appdb.now(), "user_email": _who(request), "filename": safe, "stored_name": stored,
            "content_type": DOC_TYPES[ext], "size": len(content), "vendor": field("vendor"), "sku": field("sku"),
            "renewal_key": field("renewal_key"), "note": field("note"),
        })
        appdb.audit(_who(request), "document.upload", {"id": doc_id, "file": safe, "vendor": doc["vendor"], "sku": doc["sku"]})
        return ok(doc)
    except EngineError as e:
        return fail(e)


async def document_delete(request: Request) -> Response:
    doc = appdb.delete_document(request.path_params["doc_id"])
    if not doc:
        return fail(EngineError("No such document"), 404)
    (_docs_dir() / doc["stored_name"]).unlink(missing_ok=True)
    appdb.audit(_who(request), "document.delete", {"id": doc["id"], "file": doc["filename"]})
    return ok({"ok": True})


def document_response(doc_id: str) -> Response:
    doc = appdb.get_document(doc_id)
    path = _docs_dir() / doc["stored_name"] if doc else None
    if not doc or not path.exists():
        return fail(EngineError("No such document"), 404)
    media = doc["content_type"] or mimetypes.guess_type(doc["filename"])[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, filename=doc["filename"],
                        headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- settings, users, activity

async def settings_update(request: Request) -> Response:
    try:
        body = await request.json()
        changes = body.get("changes") or {}
        if not isinstance(changes, dict):
            raise EngineError("Send the changed settings as an object")
        new, diff = S.save(changes)
        if diff:
            appdb.audit(_who(request), "settings.update", {k: {"from": a, "to": b} for k, (a, b) in diff.items()})
        return ok({"settings": S.describe(), "changed": list(diff)})
    except (ValueError, EngineError) as e:
        return fail(e)


async def users(_: Request) -> Response:
    return ok({"users": appdb.list_users()})


async def user_create(request: Request) -> Response:
    try:
        body = await request.json()
        return ok(appdb.create_user(body.get("email", ""), body.get("name", ""), body.get("role", "viewer"), _who(request)))
    except (ValueError, EngineError) as e:
        return fail(e)


async def user_update(request: Request) -> Response:
    try:
        body = await request.json()
        uid = int(request.path_params["uid"])
        if uid == request.state.user["id"] and (body.get("role") == "viewer" or body.get("active") is False):
            raise EngineError("You can't remove your own admin access; ask another admin")
        return ok(appdb.update_user(uid, _who(request), role=body.get("role"), active=body.get("active"),
                                    name=body.get("name")))
    except (ValueError, EngineError) as e:
        return fail(e)


async def activity(_: Request) -> Response:
    return ok({"events": appdb.audit_log(200)})


def routes() -> list[Route]:
    return [
        Route("/api/admin/uploads", uploads),
        Route("/api/admin/uploads", upload_stage, methods=["POST"]),
        Route("/api/admin/uploads/{uid}/apply", upload_apply, methods=["POST"]),
        Route("/api/admin/uploads/{uid}/discard", upload_discard, methods=["POST"]),
        Route("/api/admin/versions", versions),
        Route("/api/admin/versions/{vid}/activate", version_activate, methods=["POST"]),
        Route("/api/admin/templates/{kind}", template),
        Route("/api/admin/documents", document_upload, methods=["POST"]),
        Route("/api/admin/documents/{doc_id}", document_delete, methods=["DELETE"]),
        Route("/api/admin/settings", settings_update, methods=["PUT"]),
        Route("/api/admin/users", users),
        Route("/api/admin/users", user_create, methods=["POST"]),
        Route("/api/admin/users/{uid}", user_update, methods=["PATCH"]),
        Route("/api/admin/activity", activity),
    ]
