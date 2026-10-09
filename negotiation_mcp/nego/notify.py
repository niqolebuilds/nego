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
EVENT_REMINDER = "principal.reminder"
EVENT_NUDGE = "principal.whatsapp_nudge"
EVENT_SUBMITTED = "siloam.step_submitted"
EVENT_MOU_ALERT = "siloam.mou_alert"
EVENT_TEST = "test"
TEMPLATE = "siloam_nego_step_open"
TEMPLATE_REMINDER = "siloam_nego_reminder"
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


def _contacts(p: dict) -> tuple[str | None, str | None, str]:
    email = (p.get("contact_email") or "").strip() or None
    wa = whatsapp_number(p.get("contact_phone"))
    return email, wa, ", ".join(x for x in (email, f"+{wa}" if wa else None) if x) or "—"


def _wa_delay() -> int:
    from .. import settings as S

    return int(S.load()["whatsapp_after_days"])


def _nudge_key(cid: int, step: str) -> str:
    return f"wa_nudge:{cid}:{step}"


def _with_whatsapp(c: dict, email: str | None, wa: str | None) -> bool:
    """WhatsApp rides along with the email only when it isn't being held back: no delay is set,
    there is no email address to try first, or the nudge for this step has already gone."""
    return bool(wa) and (not email or _wa_delay() == 0 or store.has_message(_nudge_key(c["id"], c["current_step"])))


def _principal_payload(c: dict, event: str, link: dict, url: str, extra: dict | None = None, whatsapp: bool = True) -> dict:
    p = c["principal"]
    step = c["current_step"]
    email, wa, _ = _contacts(p)
    token = link["path"].rsplit("/", 1)[1]
    step_id, step_en = STEP_TEXT[step]
    due = tanggal(c.get("step_due"))
    contract = f"{tanggal(c['contract_start'])} – {tanggal(c['contract_end'])}"
    subject, body = _email(p["name"], step_id, step_en, due, url, contract)
    payload = {
        "event": event,
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
    if not whatsapp:  # the flow sends WhatsApp only when recipient.whatsapp is filled
        payload["recipient"]["whatsapp"] = None
        payload["whatsapp"]["to"] = None
    if extra:
        payload.update(extra)
    return payload


def _can_send(cid: int, event: str, recipient: str, by: str, has_contact: bool, dedupe: str | None = None) -> dict | None:
    """None if sending can go ahead; otherwise records why not and returns that."""
    if not config()["enabled"]:
        store.queue_message(cid, event, recipient, {}, by, "skipped",
                            f"Automatic sending isn't set up ({', '.join(status()['missing'])})", dedupe=dedupe)
        return {"status": "skipped", "reason": "not_configured", "recipient": recipient}
    if not has_contact:
        store.queue_message(cid, event, recipient, {}, by, "skipped", "No contact email or WhatsApp number", dedupe=dedupe)
        return {"status": "skipped", "reason": "no_contact", "recipient": recipient}
    return None


def step_opened(cid: int, by: str) -> dict:
    """Create a fresh link for the principal's open step and queue it for email + WhatsApp."""
    c = store.require_cycle(cid)
    if c["current_step"] not in M.PRINCIPAL_STEP_FIELDS:
        raise EngineError("It's Siloam's turn at this step; there's nothing to send the principal")
    email, wa, recipient = _contacts(c["principal"])
    stop = _can_send(cid, EVENT_STEP_OPENED, recipient, by, bool(email or wa))
    if stop:
        return stop
    store.revoke_active_links(cid, by)  # one live link per principal: the one just sent
    link = portal.create_link(cid, by)
    payload = _principal_payload(c, EVENT_STEP_OPENED, link, config()["public_url"] + link["path"], whatsapp=_with_whatsapp(c, email, wa))
    mid = store.queue_message(cid, EVENT_STEP_OPENED, recipient, payload, by)
    wake()
    return {"status": "queued", "id": mid, "recipient": recipient}


def reminder(cid: int, kind: str, days_left: int, today: date, by: str = "system") -> dict:
    """A reminder for the open step, with the same link as before (a new one only if it expired)."""
    c = store.require_cycle(cid)
    email, wa, recipient = _contacts(c["principal"])
    key = f"reminder:{cid}:{c['current_step']}:{today.isoformat()}"
    if store.has_message(key):
        return {"status": "duplicate"}
    stop = _can_send(cid, EVENT_REMINDER, recipient, by, bool(email or wa), dedupe=key)
    if stop:
        return stop
    link = portal.current_link_path(cid)
    if link is None:
        store.revoke_active_links(cid, by)
        link = portal.create_link(cid, by)
    payload = _principal_payload(c, EVENT_REMINDER, link, config()["public_url"] + link["path"],
                                 {"reminder": {"kind": kind, "days_left": days_left}}, whatsapp=_with_whatsapp(c, email, wa))
    step_id = payload["step"]["label_id"]
    due = payload["step"]["due"] or "-"
    when = (f"{days_left} hari lagi" if days_left > 0 else "hari ini") if kind != "overdue" else f"terlambat {-days_left} hari"
    payload["email"]["subject"] = f"Pengingat: {step_id} – batas waktu {due} ({when})"
    payload["whatsapp"].update(template=TEMPLATE_REMINDER,
                               body_params=[c["principal"].get("contact_name") or c["principal"]["name"], step_id, due, when])
    mid = store.queue_message(cid, EVENT_REMINDER, recipient, payload, by, dedupe=key)
    if mid:
        wake()
    return {"status": "queued" if mid else "duplicate", "id": mid, "recipient": recipient}


def whatsapp_nudge(cid: int, by: str = "system") -> dict:
    """One WhatsApp message with the same link, for a principal who hasn't acted on the email.
    Once per step: the de-duplication key stops a second one, even after a restart."""
    c = store.require_cycle(cid)
    email, wa, recipient = _contacts(c["principal"])
    key = _nudge_key(cid, c["current_step"])
    if store.has_message(key):
        return {"status": "duplicate"}
    stop = _can_send(cid, EVENT_NUDGE, recipient, by, bool(wa), dedupe=key)
    if stop:
        return stop
    link = portal.current_link_path(cid)
    if link is None:
        store.revoke_active_links(cid, by)
        link = portal.create_link(cid, by)
    payload = _principal_payload(c, EVENT_NUDGE, link, config()["public_url"] + link["path"],
                                 {"nudge": {"after_days": _wa_delay()}})
    payload["recipient"]["email"] = None  # WhatsApp only: the email went already
    payload["email"] = None
    mid = store.queue_message(cid, EVENT_NUDGE, recipient, payload, by, dedupe=key)
    if mid:
        wake()
    return {"status": "queued" if mid else "duplicate", "id": mid, "recipient": recipient}


def _nudge_due(c: dict, today: date, after_days: int) -> bool:
    """The email for this step went out, and neither a delivery nor the link being opened happened since."""
    sent = store.last_sent(c["id"], EVENT_STEP_OPENED)
    if not sent or (sent["payload"].get("step") or {}).get("key") != c["current_step"]:
        return False
    last = datetime.fromisoformat(sent["sent_at"])
    for link in store.links(c["id"]):
        if link.get("last_used_at"):
            last = max(last, datetime.fromisoformat(link["last_used_at"]))
    return (today - last.date()).days >= after_days


def admins() -> list[str]:
    return [a.strip() for a in (os.environ.get("NEGO_NOTIFY_ADMINS") or "").split(",") if "@" in a]


def step_submitted(cid: int, step: str, summary: dict, by: str) -> dict:
    """Tell Siloam's team that a principal sent a step."""
    c = store.require_cycle(cid)
    to = admins()
    recipient = ", ".join(to) or "—"
    key = f"submitted:{cid}:{step}:{store.now()[:16]}"
    stop = _can_send(cid, EVENT_SUBMITTED, recipient, by, bool(to), dedupe=key)
    if stop:
        return stop
    p = c["principal"]
    url = f"{config()['public_url']}/#nego/{cid}"
    label = STEP_TEXT.get(step, (step, step))[1]
    facts = [f"Items: {summary.get('items', 0)}", f"Price increases (with reasons): {summary.get('increase_count', 0)}",
             f"Discontinued: {summary.get('discontinued', 0)}"]
    if step == "submission":
        facts = [f"Documents: {summary.get('documents', 0)}"]
    e = html.escape
    body = (f"<p><b>{e(p['name'])}</b> sent <b>{e(label)}</b>.</p><ul>" + "".join(f"<li>{e(f)}</li>" for f in facts) +
            f"</ul><p><a href=\"{e(url)}\">Open the negotiation</a> to review the findings and move to the next step.</p>")
    payload = {"event": EVENT_SUBMITTED, "sent_at": store.now(), "principal": {"name": p["name"]},
               "step": {"key": step, "label_en": label}, "recipients": to, "link": url, "summary": summary,
               "email": {"to": to, "subject": f"{p['name']} sent {label}", "html": body}}
    mid = store.queue_message(cid, EVENT_SUBMITTED, recipient, payload, by, dedupe=key)
    wake()
    return {"status": "queued", "id": mid, "recipient": recipient}


def mou_alert(today: date) -> dict:
    """Weekly list for Siloam: MOUs ending within the alert window with no negotiation open."""
    from . import service

    d = service.principals(today)
    due = [p for p in d["principals"] if p["mou_alert"]]
    key = f"mou_alert:{today.isoformat()}"
    to = admins()
    if not due or store.has_message(key):
        return {"status": "nothing" if not due else "duplicate"}
    stop = _can_send(None, EVENT_MOU_ALERT, ", ".join(to) or "—", "system", bool(to), dedupe=key)
    if stop:
        return stop
    e = html.escape
    rows = "".join(f"<li><b>{e(p['name'])}</b> – MOU ends {e(tanggal(p['mou_end']) or '')} ({p['mou_days_left']} days)</li>" for p in due)
    url = f"{config()['public_url']}/#nego"
    payload = {"event": EVENT_MOU_ALERT, "sent_at": store.now(), "recipients": to, "link": url,
               "principals": [{"name": p["name"], "mou_end": p["mou_end"], "days_left": p["mou_days_left"]} for p in due],
               "email": {"to": to, "subject": f"{len(due)} MOUs end within {d['alert_months']} months with no negotiation open",
                         "html": f"<p>Start these negotiations:</p><ul>{rows}</ul><p><a href=\"{e(url)}\">Open Negotiations</a></p>"}}
    mid = store.queue_message(None, EVENT_MOU_ALERT, ", ".join(to), payload, "system", dedupe=key)
    wake()
    return {"status": "queued", "id": mid, "count": len(due)}


def daily(today: date | None = None) -> dict:
    """Reminders before and after step deadlines, and the weekly MOU alert. Safe to run often:
    each message has a de-duplication key, so nothing goes out twice on the same day."""
    from .. import settings as S

    today = today or date.today()
    v = S.load()
    out = {"reminders": 0, "nudges": 0, "mou_alert": None}
    if not config()["enabled"]:
        return out
    after = int(v["whatsapp_after_days"])
    if after > 0:
        for cid in store.open_cycles():
            c = store.get_cycle(cid)
            if c["current_step"] in M.PRINCIPAL_STEP_FIELDS and c["current_step"] not in c["submitted_steps"] and _nudge_due(c, today, after):
                if whatsapp_nudge(cid).get("status") == "queued":
                    out["nudges"] += 1
    if v["reminders_on"]:
        marks = {int(v["reminder_first_days"]), int(v["reminder_second_days"])}
        for cid in store.open_cycles():
            c = store.get_cycle(cid)
            step = c["current_step"]
            if step not in M.PRINCIPAL_STEP_FIELDS or step in c["submitted_steps"] or not c.get("step_due"):
                continue
            left = (date.fromisoformat(c["step_due"]) - today).days
            kind = f"h-{left}" if left in marks else "overdue" if left < 0 else None
            if kind and reminder(cid, kind, left, today).get("status") == "queued":
                out["reminders"] += 1
    if today.weekday() == int(v["mou_alert_weekday"]):
        out["mou_alert"] = mou_alert(today).get("status")
    return out


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
                if os.environ.get("NEGO_SCHEDULER", "1") != "0":
                    daily()
                deliver_due()
            except Exception:  # noqa: BLE001 - keep the sender alive; errors are on each message
                pass

    threading.Thread(target=loop, name="nego-notify", daemon=True).start()
