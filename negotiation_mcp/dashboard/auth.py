"""Sign-in, sessions and role enforcement for the web app.

Two parts, kept separate on purpose:

* **Who you are** comes from a *sign-in provider*. Today that is ``DevSignIn``: any
  registered, active email may sign in, with no password check. It is a stand-in so the
  roles, pages and audit trail can be built and used now. Replace it with SSO (Microsoft
  Entra ID or Google) or passwords by writing another provider with the same
  ``authenticate`` method; nothing else changes.
* **What you may do** is enforced here on the server for every request, whatever the
  provider: all ``/api`` routes need a session, ``/api/admin`` routes need the admin
  role, and every request that changes something must carry the ``X-Requested-With``
  header that the app's own pages send (a simple guard against cross-site requests).

Sessions are random IDs stored in ``app.db`` and sent as an HMAC-signed, HttpOnly,
SameSite=Strict cookie that expires after 12 hours.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .. import appdb
from ..settings import workspace

COOKIE = "nego_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "nego"
PUBLIC_API = {"/api/auth/signin", "/api/auth/me", "/api/auth/provider"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _secret() -> bytes:
    env = os.environ.get("NEGO_SECRET")
    if env:
        return env.encode()
    path = workspace() / "secret.key"
    if not path.exists():
        path.write_bytes(secrets.token_bytes(32))
        os.chmod(path, 0o600)
    return path.read_bytes()


def sign(sid: str) -> str:
    mac = hmac.new(_secret(), sid.encode(), hashlib.sha256).digest()
    return f"{sid}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def unsign(value: str | None) -> str | None:
    if not value or "." not in value:
        return None
    sid, _, mac = value.rpartition(".")
    expected = sign(sid).rpartition(".")[2]
    return sid if hmac.compare_digest(mac, expected) else None


class DevSignIn:
    """Development stand-in: registered, active emails sign in without a password."""

    name = "development"
    password_required = False
    notice = "Development sign-in: the password check isn't enabled yet. Only registered emails can sign in."

    def authenticate(self, email: str, password: str | None = None) -> dict | None:
        user = appdb.user_by_email(email)
        return user if user and user["active"] else None


PROVIDER = DevSignIn()


def public_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]}


def current_user(request: Request) -> dict | None:
    sid = unsign(request.cookies.get(COOKIE))
    return appdb.session_user(sid) if sid else None


def _deny(status: int, message: str) -> Response:
    return Response(json.dumps({"error": message}), status_code=status, media_type="application/json")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request.state.user = current_user(request) if path.startswith("/api/") else None
        if path.startswith("/api/"):
            if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                return _deny(403, "Request blocked: missing app header")
            if path not in PUBLIC_API:
                user = request.state.user
                if not user:
                    return _deny(401, "Please sign in")
                if path.startswith("/api/admin/") and user["role"] != "admin":
                    return _deny(403, "Admins only")
        response = await call_next(request)
        if path == "/" or path.endswith(".html") or path == "/analytics":
            response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


def set_session_cookie(response: Response, request: Request, sid: str) -> None:
    response.set_cookie(COOKIE, sign(sid), max_age=appdb.SESSION_HOURS * 3600, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")
