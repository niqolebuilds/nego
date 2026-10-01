"""Offline understanding of free-text questions for the chat bar. No LLM, no network.

``parse(text, catalog)`` turns "best price for ceftri from medisindo" into a structured
intent plus the product, vendor, hospital and time window it mentions. It only routes:
every number in the answer still comes from the engine. When it can't tell what was
meant, it says so and offers close matches instead of guessing.

Matching is fuzzy (``difflib``) over words and short phrases, so typos, lower case,
brand-less names ("ceftriaxone") and partial names ("medis") all resolve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher, get_close_matches
from typing import Iterable

INTENTS = (
    "options", "negotiate", "details", "renewals", "my_renewals", "alerts",
    "savings", "spend", "lookup", "vendor", "help", "greeting", "unknown",
)

# Ordered: the first rule that matches wins, so specific phrasings come before general ones.
INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("help", ("help", "what can you do", "how do i use", "how does this work", "what do you do")),
    ("my_renewals", ("my renewal", "my contract", "assigned to me", "my task", "my pipeline", "my deals")),
    ("renewals", ("renew", "expir", "calendar", "ending soon", "contracts ending", "contract end", "due soon", "pipeline")),
    ("alerts", ("risk", "alert", "warning", "red flag", "creep", "problem")),
    ("savings", ("where can we save", "where to save", "saving", "save money", "overpay", "opportunit",
                 "where to negotiate", "what to negotiate", "biggest gap")),
    ("negotiate", ("negotiat", "plan", "counter", "script", "what should i say", "how do i get", "approach", "tactic")),
    ("details", ("detail", "evidence", "why", "proof", "compare", "breakdown", "show me the data", "chart")),
    ("spend", ("spend", "spent", "share of wallet", "biggest supplier", "top supplier", "how much do we buy")),
    ("lookup", ("what do we pay", "what are we paying", "how much do we pay", "current price", "price history",
                "last price", "paid for", "price of", "prices for", "list price")),
    ("options", ("best price", "target", "option", "cheapest", "lowest", "good price", "should we pay",
                 "quote", "deal", "price for", "offer")),
    ("greeting", ("hello", "hi", "hey", "good morning", "good afternoon", "selamat")),
)

STOPWORDS = {
    "the", "a", "an", "for", "from", "of", "to", "with", "and", "or", "in", "on", "at", "by", "me", "we", "our", "us",
    "is", "are", "what", "whats", "how", "much", "best", "price", "prices", "give", "show", "get", "can", "you",
    "please", "i", "do", "does", "latest", "current", "negotiate", "negotiation", "options", "option", "vendor",
    "supplier", "about", "tell", "need", "want", "should", "pay", "paying", "3", "three", "top", "next", "days",
    "day", "week", "weeks", "month", "months", "this", "that", "it", "per", "unit", "details", "detail", "plan",
    "with", "renewal", "renewals", "contract", "contracts", "site", "hospital", "rs", "siloam",
}


@dataclass
class Catalog:
    """What the parser can recognise, built from the price book."""

    skus: list[dict]  # {"sku", "sku_name", "equivalence_group", "vendors": [...]}
    vendors: list[str]
    hospitals: list[str]


@dataclass
class Parsed:
    intent: str
    sku: str | None = None
    sku_name: str | None = None
    group: str | None = None
    vendor: str | None = None
    hospital: str | None = None
    window: tuple[int, int] | None = None
    confidence: float = 0.0
    missing: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _norm(text: str) -> str:
    t = text.lower()
    t = re.sub(r"[^\w\s\-./%]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s/()\-.,]+", text.lower()) if w]


def _name_core(name: str) -> str:
    """'Ceftriaxone 1 g injection (Medisindo)' -> 'ceftriaxone 1 g injection'."""
    return _norm(re.sub(r"\([^)]*\)", " ", name))


def _phrases(words: list[str], max_len: int = 5) -> Iterable[str]:
    for n in range(min(max_len, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            yield " ".join(words[i:i + n])


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


GENERIC = {"injection", "reagent", "infusion", "exam", "luer", "lock", "ml", "mg", "g", "iv", "kit", "tests",
           "test", "box", "coronary", "absorbable", "nitrile", "drug", "eluting"}


def _best_word_score(term_words: list[str], text_words: list[str]) -> float:
    """How well a product's distinctive words appear in the text (typo-tolerant).

    The strongest distinctive word decides (so "gloves" finds "Nitrile exam gloves M"),
    and the other words refine it. Generic words ("injection", "reagent") only refine:
    on their own they would match every product of that kind.
    """
    key = [w for w in term_words if w not in STOPWORDS and not w.isdigit() and len(w) > 1]
    if not key:
        return 0.0
    text = [w for w in text_words if w not in STOPWORDS]
    if not text:
        return 0.0
    scores = {}
    for kw in key:
        best = 0.0
        for tw in text:
            if len(tw) >= 4 and kw.startswith(tw):
                best = max(best, 0.95)  # "ceftri" -> "ceftriaxone"
            elif len(kw) >= 4 and tw.startswith(kw):
                best = max(best, 0.95)  # "sutures" -> "suture"
            else:
                best = max(best, _ratio(kw, tw))
        scores[kw] = best
    distinctive = [v for k, v in scores.items() if k not in GENERIC] or [0.0]
    return 0.75 * max(distinctive) + 0.25 * (sum(scores.values()) / len(scores))


def match_vendor(text_words: list[str], vendors: list[str]) -> tuple[str | None, float]:
    best, score = None, 0.0
    for v in vendors:
        vw = _words(v)
        for i, w in enumerate(text_words):
            if w in STOPWORDS or len(w) < 3:
                continue
            s = max((_ratio(w, x) for x in vw), default=0.0)
            if len(w) >= 4 and any(x.startswith(w) for x in vw):
                s = max(s, 0.93)
            if i + 1 < len(text_words):
                s = max(s, _ratio(f"{w} {text_words[i + 1]}", v.lower()))
            if s > score:
                best, score = v, s
    return (best, score) if score >= 0.8 else (None, score)


def match_hospital(text_words: list[str], hospitals: list[str]) -> str | None:
    for h in hospitals:
        place = [w for w in _words(h) if w not in {"site", "rs", "siloam", "hospital"}]
        for w in text_words:
            if len(w) >= 4 and any(_ratio(w, p) >= 0.85 or p.startswith(w) for p in place):
                return h
    return None


def match_product(text_words: list[str], skus: list[dict], vendor: str | None) -> tuple[dict | None, float, list[dict]]:
    """Best SKU for the text. A vendor in the text breaks ties inside an equivalence group."""
    scored = []
    for s in skus:
        name_score = _best_word_score(_words(_name_core(s["sku_name"])), text_words)
        group_score = _best_word_score(_words(s["equivalence_group"]), text_words)
        code_score = 1.0 if s["sku"].lower() in text_words else 0.0
        score = max(name_score, group_score, code_score)
        if vendor and vendor in s.get("vendors", []):
            score += 0.05
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored or scored[0][0] < 0.72:
        return None, scored[0][0] if scored else 0.0, [s for _, s in scored[:3]]
    top_score, top = scored[0]
    # Same product family from several vendors and no vendor named: report the group.
    ties = [s for sc, s in scored if sc >= top_score - 0.051 and s["equivalence_group"] == top["equivalence_group"]]
    return top, top_score, ties


def parse_window(text: str) -> tuple[int, int] | None:
    t = text.lower()
    m = re.search(r"(\d{1,3})\s*(?:-|–|to)\s*(\d{1,3})\s*day", t)
    if m:
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        return lo, hi
    m = re.search(r"(?:next|within|in)\s+(\d{1,3})\s*day", t) or re.search(r"(\d{1,3})\s*days?", t)
    if m:
        return 0, int(m.group(1))
    m = re.search(r"(?:next|within|in)\s+(\d{1,2})\s*weeks?", t)
    if m:
        return 0, 7 * int(m.group(1))
    m = re.search(r"(?:next|within|in)\s+(\d{1,2})\s*months?", t)
    if m:
        return 0, 30 * int(m.group(1))
    if "this week" in t or "next week" in t:
        return 0, 7 if "this week" in t else 14
    if "this month" in t:
        return 0, 30
    if "next month" in t:
        return 0, 60
    if "this quarter" in t or "next quarter" in t:
        return 0, 90
    if "urgent" in t or "soon" in t:
        return 0, 30
    return None


def _intent(t: str) -> tuple[str, float]:
    for intent, keys in INTENT_RULES:
        for k in keys:
            if intent == "greeting":
                if re.fullmatch(rf"\W*{re.escape(k)}\W*(there|team)?\W*", t):
                    return intent, 0.9
                continue
            if k in t:
                return intent, 0.85
    return "unknown", 0.0


def parse(text: str, catalog: Catalog) -> Parsed:
    t = _norm(text or "")
    if not t:
        return Parsed("help", confidence=1.0)
    words = _words(t)
    intent, conf = _intent(t)
    vendor, vscore = match_vendor(words, catalog.vendors)
    product, pscore, candidates = match_product(words, catalog.skus, vendor)
    hospital = match_hospital(words, catalog.hospitals)
    window = parse_window(t)
    p = Parsed(intent=intent, vendor=vendor, hospital=hospital, window=window, confidence=conf)

    if product:
        p.sku, p.sku_name, p.group = product["sku"], product["sku_name"], product["equivalence_group"]
        if len(candidates) > 1 and not vendor:
            p.candidates = candidates
    elif pscore >= 0.55:
        p.suggestions = [c["sku_name"] for c in candidates]

    # Fill in the intent from what was mentioned when no keyword said it.
    if p.intent in ("unknown", "greeting") and (p.sku or p.vendor):
        p.intent = "options" if p.sku else "vendor"
        p.confidence = 0.7
    if p.intent == "unknown" and p.window:
        p.intent, p.confidence = "renewals", 0.7
    if p.intent == "spend" and p.vendor and not p.sku:
        p.intent = "vendor"

    needs_product = p.intent in ("options", "negotiate", "details")
    if needs_product:
        if not p.sku:
            p.missing.append("sku")
        if not p.vendor:
            p.missing.append("vendor")
        if p.sku and p.vendor:
            p.confidence = max(p.confidence, 0.9)
    if p.intent == "lookup" and not (p.sku or p.vendor):
        p.missing.append("sku")

    if p.intent == "unknown":
        vocab = sorted({w for s in catalog.skus for w in _words(_name_core(s["sku_name"])) if len(w) > 3}
                       | {w for v in catalog.vendors for w in _words(v) if len(w) > 3})
        close = {m for w in words if len(w) > 3 for m in get_close_matches(w, vocab, n=2, cutoff=0.7)}
        p.suggestions = p.suggestions or sorted(close)[:5]
    return p


def catalog_from_book(book) -> Catalog:
    supplied: dict[str, set] = {}
    for o in book.observations:
        supplied.setdefault(o.sku, set()).add(o.vendor)
    skus = [{"sku": s.sku, "sku_name": s.sku_name, "equivalence_group": s.equivalence_group,
             "vendors": sorted(supplied.get(s.sku, ()))} for s in book.skus.values()]
    return Catalog(skus=skus, vendors=sorted({o.vendor for o in book.observations}),
                   hospitals=sorted({o.hospital for o in book.observations}))
