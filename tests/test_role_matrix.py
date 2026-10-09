"""Who can reach what: one table for every role, the executable form of docs/ROLES.md.

Rows are probes (a request); columns are the roles. ``True`` means the request gets past the
access check (it may still answer 400/404 for an empty body or an unknown id), ``False``
means it is refused with 401 or 403. Vendors are principals with a link and no account.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from negotiation_mcp import appdb
from negotiation_mcp.dashboard.app import create_app
from negotiation_mcp.nego import master, store

H = {"X-Requested-With": "nego"}
ROLES = ("anonymous", "vendor", "viewer", "negotiator", "admin")

# (method, path with {cid}, body) -> allowed for [anonymous, vendor, viewer, negotiator, admin]
PROBES = [
    # staff reading
    ("GET", "/api/catalog", None, (0, 0, 1, 1, 1)),
    ("GET", "/api/nego/principals", None, (0, 0, 1, 1, 1)),
    ("GET", "/api/nego/cycles/{cid}", None, (0, 0, 1, 1, 1)),
    ("GET", "/api/nego/cycles/{cid}/groups", None, (0, 0, 1, 1, 1)),
    ("GET", "/api/nego/cycles/{cid}/benchmarks", None, (0, 0, 1, 1, 1)),
    ("GET", "/api/renewals", None, (0, 0, 1, 1, 1)),
    ("PATCH", "/api/renewals/progress", {}, (0, 0, 1, 1, 1)),
    # running a negotiation
    ("GET", "/api/admin/nego/cycles/{cid}/links", None, (0, 0, 0, 1, 1)),
    ("GET", "/api/admin/nego/cycles/{cid}/messages", None, (0, 0, 0, 1, 1)),
    ("GET", "/api/admin/nego/cycles/{cid}/documents", None, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/scan", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/co", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/anomalies/{n}", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/benchmarks/match", {}, (0, 0, 0, 1, 1)),
    ("GET", "/api/admin/nego/cycles/{cid}/package.xlsx", None, (0, 0, 0, 1, 1)),
    ("PATCH", "/api/admin/nego/cycles/{cid}/items/{n}", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/step", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/links", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/links/send", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/links/{n}/revoke", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/messages/{n}/retry", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/assist", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/benchmarks/{n}", {}, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/on-fill", {}, (0, 0, 0, 1, 1)),
    ("GET", "/api/admin/nego/cycles/{cid}/documents/{n}", None, (0, 0, 0, 1, 1)),
    ("GET", "/api/admin/nego/notify", None, (0, 0, 0, 1, 1)),
    ("GET", "/api/admin/nego/assist", None, (0, 0, 0, 1, 1)),
    # master data (read), settings, people
    ("POST", "/api/admin/nego/principals", {}, (0, 0, 0, 0, 1)),
    ("POST", "/api/admin/nego/cycles", {}, (0, 0, 0, 0, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/prepare", {}, (0, 0, 0, 0, 1)),
    ("POST", "/api/admin/nego/cycles/{cid}/import", {}, (0, 0, 0, 0, 1)),
    ("PATCH", "/api/admin/nego/cycles/{cid}", {}, (0, 0, 0, 0, 1)),
    ("GET", "/api/admin/master/overview", None, (0, 0, 0, 1, 1)),  # master data: negotiators read it...
    ("GET", "/api/admin/master/items", None, (0, 0, 0, 1, 1)),
    ("GET", "/api/admin/master/items.xlsx", None, (0, 0, 0, 1, 1)),
    ("POST", "/api/admin/master/items/bulk", {}, (0, 0, 0, 0, 1)),  # ...and only admins change it
    ("POST", "/api/admin/master/items/import", {}, (0, 0, 0, 0, 1)),
    ("PATCH", "/api/admin/master/items/{n}", {}, (0, 0, 0, 0, 1)),
    ("GET", "/api/admin/uploads", None, (0, 0, 0, 0, 1)),
    ("GET", "/api/admin/versions", None, (0, 0, 0, 0, 1)),
    ("PUT", "/api/admin/settings", {}, (0, 0, 0, 0, 1)),
    ("GET", "/api/admin/users", None, (0, 0, 0, 0, 1)),
    ("POST", "/api/admin/users", {}, (0, 0, 0, 0, 1)),
    ("GET", "/api/admin/activity", None, (0, 0, 0, 0, 1)),
    ("POST", "/api/admin/nego/notify/test", {}, (0, 0, 0, 0, 1)),
    # the vendor's own portal: only a principal link opens it
    ("GET", "/api/p/me", None, (0, 1, 0, 0, 0)),
    ("GET", "/api/p/items", None, (0, 1, 0, 0, 0)),
    ("GET", "/api/p/summary", None, (0, 1, 0, 0, 0)),
    ("GET", "/api/p/template.xlsx", None, (0, 1, 0, 0, 0)),
]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import os

    home = tmp_path_factory.mktemp("matrix")
    os.environ["NEGO_HOME"] = str(home)
    store.init()
    appdb.init()
    appdb.create_user("vera@example.com", "Vera", "viewer", "t")
    appdb.create_user("nina@example.com", "Nina", "negotiator", "t")
    p = store.create_principal({"name": "PT Matrix"}, "a@x")
    c = store.create_cycle(p["id"], {"contract_start": "2027-01-01", "contract_end": "2029-12-31"}, "a@x")
    store.add_items(c["id"], [{"erp_code": "M1", "item_name": "Item", "item_status": "Active", "mou_qty": 10, "mou_hna": 100_000, "mou_disc": 0.1}], "a@x")
    store.set_step(c["id"], "rfq", "a@x")
    master.update("M1", {"generic_name": "G"}, "a@x")
    app = create_app()
    clients = {"anonymous": TestClient(app)}
    for role, email in (("viewer", "vera@example.com"), ("negotiator", "nina@example.com"), ("admin", "admin@example.com")):
        cl = TestClient(app)
        assert cl.post("/api/auth/signin", json={"email": email}, headers=H).status_code == 200
        clients[role] = cl
    clients["vendor"] = vendor_client(clients["admin"], c["id"])
    return clients, c["id"]


def vendor_client(admin: TestClient, cid: int) -> TestClient:
    """A vendor holding a fresh link. Issuing a link revokes the earlier ones, so each probe gets its own."""
    link = admin.post(f"/api/admin/nego/cycles/{cid}/links", json={"days": 2}, headers=H).json()
    vendor = TestClient(admin.app)
    r = vendor.get(link["path"], follow_redirects=False)
    vendor.cookies.set("nego_principal", r.cookies.get("nego_principal"))
    return vendor


def reaches(client: TestClient, method: str, path: str, body) -> bool:
    kwargs = {"headers": H}
    if body is not None:
        kwargs["json"] = body
    return client.request(method, path, **kwargs).status_code not in (401, 403)


@pytest.mark.parametrize("method,path,body,expected", PROBES, ids=[f"{m} {p}" for m, p, _, _ in PROBES])
def test_each_role_reaches_exactly_what_it_should(world, method, path, body, expected):
    clients, cid = world
    clients["vendor"] = vendor_client(clients["admin"], cid)
    for role, allowed in zip(ROLES, expected):
        got = reaches(clients[role], method, path.replace("{cid}", str(cid)).replace("{n}", "1"), body)
        assert got == bool(allowed), f"{role}: {method} {path} {'reached' if got else 'was refused'}, expected {'reach' if allowed else 'refusal'}"


def test_every_admin_route_is_in_the_table_or_admin_only(world):
    """A route added under /api/admin without a probe must still be refused to non-admins."""
    clients, cid = world
    import re

    from negotiation_mcp.dashboard import auth

    app = clients["admin"].app
    probed = {re.sub(r"\{[^}]+\}", "{}", p) for _, p, _, _ in PROBES}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/admin/") or re.sub(r"\{[^}]+\}", "{}", path) in probed:
            continue
        concrete = re.sub(r"\{[^}:]+(:\w+)?\}", "1", path)
        assert not auth.negotiator_may(concrete), f"{path} is open to negotiators but is not in the role table"
        assert not reaches(clients["viewer"], "GET", concrete, None), path


BASE_KEYS = {"id", "erp_code", "item_name", "brand", "catalog_no", "item_status", "remarks", "po_unit_text", "mou_qty", "mou_hna",
             "mou_disc", "rfq_qty", "rfq_hna", "rfq_disc", "price_reason", "principal_confirmed", "mou_unit_price", "rfq_unit_price",
             "pack_name", "piece_name", "issues", "outstanding", "state"}
FEEDBACK_KEYS = {"co_disc", "fb1_disc", "co_unit_price", "fb1_unit_price"}


@pytest.mark.parametrize("step,extra", [("identification", set()), ("rfq", set()), ("feedback1", FEEDBACK_KEYS)])
def test_vendor_sees_only_the_whitelisted_fields_at_each_step(world, step, extra):
    """Siloam's own numbers (PO history, annual volume, impact, anomalies, benchmarks, groups, the
    online-nego discount) never reach a vendor, whatever step it is on. The counter offer and
    Feedback I discounts appear only at the Feedback I step, where the vendor responds to them."""
    clients, cid = world
    store.set_step(cid, step, "a@x")
    vendor = vendor_client(clients["admin"], cid)
    items = vendor.get("/api/p/items").json()["items"]
    assert items, step
    for it in items:
        assert set(it) == BASE_KEYS | extra, (step, set(it) ^ (BASE_KEYS | extra))
    me = vendor.get("/api/p/me").json()
    assert set(me) == {"principal", "contract", "step", "current_step", "step_no", "submitted", "due", "ppn", "thresholds", "reasons",
                       "link_expires", "help"}
    assert set(me["principal"]) == {"name", "distributor"}  # no contact person, phone, email or notes
    store.set_step(cid, "rfq", "a@x")
