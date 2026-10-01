"""Send the principal their link automatically, through an automation tool (Power Automate).

The app doesn't talk to Outlook or WhatsApp itself. It posts one JSON event per message to a
webhook — a Power Automate "When an HTTP request is received" flow — which sends the email
from Siloam's mailbox and the WhatsApp template message through Meta's Cloud API. Wording
lives in the flow and in the Meta-approved template, so it can change without code. See
``deploy/POWER_AUTOMATE.md``.

Configuration is in environment variables, never in ``settings.json`` (everyone signed in
can read that):

* ``NEGO_PUBLIC_URL``          address principals open, e.g. https://nego.siloamhospitals.com
* ``NEGO_NOTIFY_WEBHOOK_URL``  the flow's trigger URL (it carries its own signature: a secret)
* ``NEGO_NOTIFY_SECRET``       optional; HMAC-SHA256 of the body in ``X-Nego-Signature``

Messages go through an outbox in ``nego.db``: queued, sent by a background sender with
retries, and visible to admins. The link token stays in the stored payload only until
the message is delivered.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from ..engine import EngineError
from . import model as M
from . import portal, store

EVENT_STEP_OPENED = "principal.step_opened"
EVENT_TEST = "test"
TEMPLATE = "siloam_nego_step_open"
BACKOFF_MIN = (1, 5, 15, 60, 240)
STEP_TEXT = {
    "identification": ("Konfirmasi data item", "Confirm item details"),
    "rfq": ("Isi harga penawaran (RFQ)", "Quote your prices (RFQ)"),
    "feedback1": ("Tanggapi counter offer Siloam", "Respond to Siloam's counter offer"),
    "submission": ("Kirim dokumen perusahaan", "Send company documents"),
}
BULAN = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November",
         "Desember")


def config() -> dict:
    url = (os.environ.get("NEGO_NOTIFY_WEBHOOK_URL") or "").strip()
    public = (os.environ.get("NEGO_PUBLIC_URL") or "").strip().rstrip("/")
    return {"webhook": url, "public_url": public, "secret": os.environ.get("NEGO_NOTIFY_SECRET") or "",
            "enabled": bool(url and public)}


def status() -> dict:
    c = config()
    host = re.sub(r"^https?://([^/]+).*$", r"\1", c["webhook"]) if c["webhook"] else None
    return {"enabled": c["enabled"], "webhook_host": host, "public_url": c["public_url"] or None,
            "signed": bool(c["secret"]),
            "missing": [n for n, v in (("NEGO_NOTIFY_WEBHOOK_URL", c["webhook"]), ("NEGO_PUBLIC_URL", c["public_url"])) if not v]}


def whatsapp_number(raw: str | None) -> str | None:
    """Indonesian numbers to the international digits WhatsApp wants: 0812… / +62 812… -> 62812…"""
    d = re.sub(r"\D", "", raw or "")
    if not d:
        return None
    if d.startswith("0"):
        d = "62" + d[1:]
    elif d.startswith("8"):
        d = "62" + d
    return d if 9 <= len(d) <= 15 else None


def tanggal(iso: str | None) -> str | None:
    if not iso:
        return None
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {BULAN[d.month - 1]} {d.year}"


def _email(principal: str, step_id: str, step_en: str, due: str | None, link: str, contract: str) -> tuple[str, str]:
    e = html.escape
    subject = f"Siloam Hospitals – {step_id} ({principal})"
    due_id = f"<p>Batas waktu: <b>{e(due)}</b></p>" if due else ""
    body = f"""<p>Yth. Bapak/Ibu {e(principal)},</p>
<p>Siloam Hospitals mengundang Anda untuk <b>{e(step_id)}</b> untuk kontrak {e(contract)}.</p>
{due_id}
<p><a href="{e(link)}" style="display:inline-block;padding:10px 18px;background:#0c21a4;color:#fff;border-radius:8px;text-decoration:none;font-weight:bold">Buka halaman pengisian</a></p>
<p>Atau salin tautan ini: {e(link)}</p>
<p>Tautan ini khusus untuk perusahaan Anda. Mohon tidak diteruskan ke pihak lain. Data tersimpan otomatis; Anda juga bisa mengunduh dan mengunggah file Excel dari halaman tersebut.</p>
<p>Terima kasih,<br>Tim Pengadaan Siloam Hospitals</p>
<hr>
<p style="color:#666">English: Siloam Hospitals invites you to <b>{e(step_en)}</b>. Open the link above; it is for your company only, please don't forward it.</p>"""
    return subject, body


def _wait_seconds(attempt: int) -> int:
    return 60 * BACKOFF_MIN[min(attempt, len(BACKOFF_MIN) - 1)]


def step_opened(cid: int, by: str) -> dict:
    """Create a fresh link for the principal's open step and queue it for email + WhatsApp."""
    c = store.require_cycle(cid)
    step = c["current_step"]
    if step not in M.PRINCIPAL_STEP_FIELDS:
        raise EngineError("It's Siloam's turn at this step; there's nothing to send the principal")
    p = c["principal"]
    cfg = config()
    email = (p.get("contact_email") or "").strip() or None
    wa = whatsapp_number(p.get("contact_phone"))
    recipient = ", ".join(x for x in (email, f"+{wa}" if wa else None) if x) or "—"
    if not cfg["enabled"]:
        store.queue_message(cid, EVENT_STEP_OPENED, recipient, {}, by, "skipped",
                            f"Automatic sending isn't set up ({', '.join(status()['missing'])})")
        return {"status": "skipped", "reason": "not_configured", "recipient": recipient}
    if not email and not wa:
        store.queue_message(cid, EVENT_STEP_OPENED, recipient, {}, by, "skipped",
                            "The principal has no contact email or WhatsApp number")
        return {"status": "skipped", "reason": "no_contact", "recipient": recipient}
    store.revoke_active_links(cid, by)  # one live link per principal: the one just sent
    link = portal.create_link(cid, by)
    token = link["path"].rsplit("/", 1)[1]
    url = cfg["public_url"] + link["path"]
    step_id, step_en = STEP_TEXT[step]
    due = tanggal(c.get("step_due"))
    contract = f"{tanggal(c['contract_start'])} – {tanggal(c['contract_end'])}"
    subject, body = _email(p["name"], step_id, step_en, due, url, contract)
    payload = {
        "event": EVENT_STEP_OPENED,
        "sent_at": store.now(),
        "principal": {"name": p["name"], "distributor": p.get("distributor")},
        "recipient": {"name": p.get("contact_name"), "email": email, "whatsapp": wa},
        "step": {"key": step, "number": portal.STEP_NUMBER[step], "label_id": step_id, "label_en": step_en, "due": due},
        "contract": {"start": c["contract_start"], "end": c["contract_end"], "binding": c["binding"]},
        "link": url, "link_token": token, "link_expires": link["expires_at"],
        "email": {"subject": subject, "html": body},
        "whatsapp": {"to": wa, "template": TEMPLATE, "language": "id",
                     "body_params": [p.get("contact_name") or p["name"], step_id, due or "-"],
                     "button_param": token},
    }
    mid = store.queue_message(cid, EVENT_STEP_OPENED, recipient, payload, by)
    wake()
    return {"status": "queued", "id": mid, "recipient": recipient}


def send_test(by: str) -> dict:
    cfg = config()
    if not cfg["webhook"]:
        raise EngineError("Set NEGO_NOTIFY_WEBHOOK_URL on the server first")
    payload = {"event": EVENT_TEST, "sent_at": store.now(), "by": by,
               "note": "Test from Negotiation Intelligence. The flow should answer 200 and send nothing."}
    ok, err = post(payload)
    return {"ok": ok, "error": err}


def post(payload: dict) -> tuple[bool, str | None]:
    cfg = config()
    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "nego-notify/1"}
    if cfg["secret"]:
        headers["X-Nego-Signature"] = "sha256=" + hmac.new(cfg["secret"].encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(cfg["webhook"], data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - admin-configured URL
            return 200 <= r.status < 300, None if 200 <= r.status < 300 else f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, str(getattr(e, "reason", e))[:300]


def _redact(payload: dict) -> dict:
    out = json.loads(json.dumps(payload))
    if "link_token" in out:
        out["link_token"] = "(sent)"
        out["link"] = re.sub(r"/p/[^/]+$", "/p/(sent)", out.get("link", ""))
        if out.get("whatsapp"):
            out["whatsapp"]["button_param"] = "(sent)"
        if out.get("email"):
            out["email"]["html"] = "(sent)"
    return out


def deliver_due() -> dict:
    """Send everything that's due. Called by the background sender and by tests."""
    sent = failed = 0
    if not config()["webhook"]:
        return {"sent": 0, "failed": 0}
    for m in store.claim_due_messages():
        ok, err = post(m["payload"])
        nxt = (datetime.now(timezone.utc) + timedelta(seconds=_wait_seconds(m["attempts"]))).isoformat(timespec="seconds")
        store.message_result(m["id"], ok, err, nxt, _redact(m["payload"]) if ok else None)
        if m["cycle_id"]:
            store.log_event(m["cycle_id"], "system", "message.sent" if ok else "message.failed",
                            {"id": m["id"], "error": err, "attempt": m["attempts"] + 1})
        sent += ok
        failed += not ok
    return {"sent": sent, "failed": failed}


def retry(cid: int, mid: int) -> dict:
    m = store.get_message(mid)
    if not m or m["cycle_id"] != cid:
        raise EngineError("No such message")
    if m["status"] == "sent":
        raise EngineError("Already sent. Use Send link now to send a fresh link")
    if m["status"] == "skipped" or not m["payload"]:
        raise EngineError("This message was never queued; fix the reason and use Send link now")
    store.requeue_message(mid)
    wake()
    return {"ok": True}


# ---------------------------------------------------------------- background sender

_wake = threading.Event()
_started = False
_lock = threading.Lock()


def wake() -> None:
    _wake.set()


def start_sender(interval: int = 60) -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    store.reset_sending()

    def loop() -> None:
        while True:
            _wake.wait(interval)
            _wake.clear()
            try:
                deliver_due()
            except Exception:  # noqa: BLE001 - keep the sender alive; errors are on each message
                pass

    threading.Thread(target=loop, name="nego-notify", daemon=True).start()
