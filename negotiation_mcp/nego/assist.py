"""Optional Claude drafting for admins. The engine computes; Claude only writes.

Off unless an admin turns on "Draft messages with Claude" (Engine settings) and the
server has ``ANTHROPIC_API_KEY``. Three drafts, always shown to a person before anyone
sends them:

* **Counter-offer cover message** (Bahasa) to the principal: counts and the deadline, no
  Siloam-internal numbers (no benchmarks, PO volumes, margins or findings).
* **Meeting summary** (Bahasa): the admin's own Online Nego notes, tidied into agreements,
  open points and follow-ups.
* **Escalation note** (English): the engine's escalation numbers for the approver.

What is sent is the minimum each draft needs (aggregates and the admin's notes, never the
item price list), and every request is logged in the cycle's activity.
"""

from __future__ import annotations

import os

from .. import settings as S
from ..engine import EngineError
from . import store
from .template_io import tanggal

MODEL = "claude-opus-5-5"
KINDS = ("co_cover", "meeting_summary", "escalation_note")

SYSTEM = (
    "You draft short business messages for the procurement team of Siloam Hospitals, an Indonesian hospital group, "
    "during price negotiations with suppliers (principals). Use only the facts given; never invent prices, dates, "
    "percentages or commitments. If a fact is missing, leave a clearly marked placeholder like [tanggal]. "
    "Write plainly and politely. Return only the message text, with no preamble."
)


def status() -> dict:
    v = S.load()
    key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    try:
        import anthropic  # noqa: F401

        sdk = True
    except ImportError:
        sdk = False
    return {"enabled": bool(v["llm_assist"]) and key and sdk, "setting": bool(v["llm_assist"]), "api_key": key, "sdk": sdk}


def _facts(cid: int, kind: str) -> tuple[str, dict]:
    """The prompt for one draft and the exact facts sent (also logged)."""
    from .impact import enrich, summary
    from .negotiate import escalation

    c = store.require_cycle(cid)
    p = c["principal"]
    ppn = float(S.load()["ppn_rate"])
    items = [enrich(i, ppn) for i in store.items(cid)]
    k = summary(items)
    if kind == "co_cover":
        co = [i for i in items if i.get("co_disc") is not None]
        raised = sum(1 for i in co if i.get("rfq_disc") is not None and i["co_disc"] > i["rfq_disc"] + 1e-9)
        facts = {"principal": p["name"], "contact": p.get("contact_name"), "items_with_counter_offer": len(co),
                 "items_where_siloam_asks_more_discount": raised, "items_accepted_as_quoted": len(co) - raised,
                 "deadline": tanggal(c.get("step_due")) if c.get("step_due") else None,
                 "contract_period": f"{tanggal(c['contract_start'])} – {tanggal(c['contract_end'])}"}
        task = ("Write a WhatsApp/email message in Bahasa Indonesia from Tim Pengadaan Siloam Hospitals to the principal, "
                "telling them Siloam's counter offer is ready in their portal link (the link is added separately), how many "
                "items have a counter offer, that they can accept or propose their own discount per item with a reason, and "
                "the deadline. Under 120 words. Do not mention internal benchmarks, volumes or margins.")
    elif kind == "meeting_summary":
        notes = (c.get("meeting_notes") or "").strip()
        if not notes:
            raise EngineError("Write the meeting notes first (Negotiate → Online Nego)")
        facts = {"principal": p["name"], "meeting_at": c.get("meeting_at"), "notes": notes[:4000],
                 "items_agreed": sum(1 for i in items if i.get("on_disc") is not None)}
        task = ("Turn these Online Nego meeting notes into a tidy summary in Bahasa Indonesia with three headed lists: "
                "Kesepakatan (agreements), Belum disepakati (open points), Tindak lanjut (follow-ups, with owner and date "
                "when the notes say so). Keep every fact from the notes; add nothing.")
    elif kind == "escalation_note":
        esc = escalation(cid)
        if not esc["items_agreed"]:
            raise EngineError("No agreed prices yet")
        stages = {s["key"]: s for s in k["stages"]}
        facts = {"principal": p["name"], "contract_period": f"{c['contract_start']} to {c['contract_end']}",
                 "items_agreed": esc["items_agreed"], "items_above_mou": esc["increases"],
                 "annual_cost_impact_vs_mou_idr": round(esc["impact"] or 0), "escalation_limit_idr": round(esc["limit"]),
                 "rfq_impact_idr": round(stages["rfq"]["impact"] or 0) if stages["rfq"]["impact"] is not None else None,
                 "baseline_spend_idr": round(k["baseline_spend"]), "reasons": esc["reasons"]}
        task = ("Write a short internal escalation note in English for the procurement head asking for approval of the "
                "agreed prices. State the impact against the MOU and the limit, how the position moved from the RFQ, and "
                "what approval is needed. Use Indonesian number style for rupiah (Rp 1.234.567). Under 150 words.")
    else:
        raise EngineError("Unknown draft type")
    return task, facts


def draft(cid: int, kind: str, by: str) -> dict:
    st = status()
    if not st["enabled"]:
        missing = [m for m, ok in (("turn on 'Draft messages with Claude' in Engine settings", st["setting"]),
                                   ("set ANTHROPIC_API_KEY on the server", st["api_key"]),
                                   ("install the anthropic package", st["sdk"])) if not ok]
        raise EngineError("Claude drafting is off: " + "; ".join(missing))
    import json

    import anthropic

    task, facts = _facts(cid, kind)
    client = anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            output_config={"effort": "low"},  # short drafts; low effort keeps them quick and cheap
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": f"{task}\n\nFacts (JSON):\n{json.dumps(facts, ensure_ascii=False, indent=1)}"}],
        )
    except anthropic.RateLimitError:
        raise EngineError("Claude is busy (rate limit). Try again in a minute.") from None
    except anthropic.APIStatusError as e:
        raise EngineError(f"Claude returned an error ({e.status_code}).") from None
    except anthropic.APIConnectionError:
        raise EngineError("Can't reach Claude from the server (network).") from None
    if resp.stop_reason == "refusal":
        raise EngineError("Claude declined to draft this. Write it by hand.")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    store.log_event(cid, by, "assist.draft", {"kind": kind, "model": resp.model, "facts_sent": list(facts)})
    return {"kind": kind, "text": text, "model": resp.model, "facts": facts}
