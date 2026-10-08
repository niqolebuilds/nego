"""Sign-in, sessions and role enforcement for the web app.

Two parts, kept separate on purpose:

* **Who you are** comes from a *sign-in provider*. The default is ``PasswordSignIn``:
  a registered, active email plus its password (scrypt-hashed in ``app.db``). Nobody
  chooses a password for someone else: an admin copies a one-time setup link from
  Admin → Users and the person sets their own. Repeated failures from one address are
  slowed down. ``NEGO_AUTH=dev`` switches to ``DevSignIn`` (email only, no password) for
  tests and local demos. SSO (Microsoft Entra ID) can be added later as another provider
  with the same ``authenticate`` method; nothing else changes.
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
import re
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .. import appdb
from ..settings import workspace

COOKIE = "nego_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "nego"
PUBLIC_API = {"/api/auth/signin", "/api/auth/me", "/api/auth/provider", "/api/auth/setup"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


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


class PasswordSignIn:
    """Registered, active email plus the password the person set from their setup link."""

    name = "password"
    password_required = True
    notice = "First time here? Use the set-password link your admin sent you."

    def authenticate(self, email: str, password: str | None = None) -> dict | None:
        user = appdb.user_by_email(email)
        good = appdb.verify_password(password or "", user["password_hash"] if user else None)
        return user if good and user["active"] else None


PROVIDER = DevSignIn() if os.environ.get("NEGO_AUTH", "").lower() == "dev" else PasswordSignIn()

# Failed sign-ins per client address: after FAIL_LIMIT in FAIL_WINDOW seconds, refuse for a while.
FAIL_LIMIT, FAIL_WINDOW = 8, 15 * 60
_fails: dict[str, list[float]] = {}


def signin_blocked(ip: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
    _fails[ip] = recent
    return len(recent) >= FAIL_LIMIT


def signin_failed(ip: str) -> None:
    if len(_fails) > 10_000:  # keep memory bounded
        _fails.clear()
    _fails.setdefault(ip, []).append(time.monotonic())


def public_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]}


def current_user(request: Request) -> dict | None:
    sid = unsign(request.cookies.get(COOKIE))
    return appdb.session_user(sid) if sid else None


# What a negotiator may do under /api/admin: run a negotiation (prices, counter offers, anomalies,
# benchmarks, principal links and messages, the package), but not change master data: principals,
# opening or preparing a negotiation, uploads and price data, users, settings, notification setup.
NEGOTIATOR_PATHS = re.compile(
    r"^/api/admin/nego/(?:notify|assist)$"
    r"|^/api/admin/nego/cycles/[^/]+/(?:items/\d+|scan|step|anomalies/\d+|links|links/send|links/\d+/revoke|messages|messages/\d+/retry"
    r"|assist|benchmarks/match|benchmarks/\d+|co|on-fill|package\.xlsx|documents|documents/\d+)$")


def negotiator_may(path: str) -> bool:
    return bool(NEGOTIATOR_PATHS.match(path))


def _deny(status: int, message: str) -> Response:
    return Response(json.dumps({"error": message}), status_code=status, media_type="application/json")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request.state.user = current_user(request) if path.startswith("/api/") and not path.startswith("/api/p/") else None
        if path.startswith("/api/"):
            if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                return _deny(403, "Request blocked: missing app header")
            # /api/p/* is the principal portal: it has its own access check (a principal link,
            # see portal_routes.py) and never sees a Siloam session.
            if path not in PUBLIC_API and not path.startswith("/api/p/"):
                user = request.state.user
                if not user:
                    return _deny(401, "Please sign in")
                if path.startswith("/api/admin/") and user["role"] != "admin" and not (user["role"] == "negotiator" and negotiator_may(path)):
                    return _deny(403, "Admins only")
        response = await call_next(request)
        if path == "/" or path.endswith(".html") or path == "/analytics" or path == "/p" or path.startswith("/p/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if not path.startswith("/api/"):
            # Pages load only their own scripts; inline style attributes are used by the charts.
            response.headers.setdefault("Content-Security-Policy", CSP)
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


def set_session_cookie(response: Response, request: Request, sid: str) -> None:
    response.set_cookie(COOKIE, sign(sid), max_age=appdb.SESSION_HOURS * 3600, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")
