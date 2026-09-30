"""Engine settings that admins tune, shared by the web app and the MCP server.

Stored as JSON in the workspace (``settings.json``). Every value has a default, a
range and a plain-language label, so the admin form, the validation and the
documentation all come from one table. Unset values fall back to the defaults, and
the rebate breakage rate (D-18) deliberately defaults to *unset*: the engine keeps
refusing to guess it until someone who owns the number enters it.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import engine as E
from .engine import EngineError

REPO_ROOT = Path(__file__).resolve().parent.parent


def workspace() -> Path:
    """Folder holding everything the app writes: settings, app.db, data versions, documents."""
    d = Path(os.environ.get("NEGO_HOME") or REPO_ROOT / "data" / "workspace")
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    group: str
    default: Any
    kind: str  # "fraction" | "number" | "int" | "bool"
    lo: float | None = None
    hi: float | None = None
    help: str = ""
    optional: bool = False


SETTINGS: tuple[Setting, ...] = (
    Setting("beat_margin", "How far the target sits below the best price on record", "Targets", 0.01, "fraction", 0, 0.5,
            "0.01 = 1% below the lowest reference."),
    Setting("anchor_margin", "How far the opening ask sits below the target", "Targets", 0.05, "fraction", 0, 0.5,
            "0.05 = open 5% below target."),
    Setting("lookback_months", "How far back a vendor's own best price counts", "Targets", 24, "int", 3, 120),
    Setting("wacc", "Cost of capital (WACC)", "Money", 0.12, "fraction", 0, 0.99,
            "Values payment terms and rebate lag. Confirm with Treasury (D-17)."),
    Setting("contract_years", "Contract term used to spread capex", "Money", 3.0, "number", 0.5, 20),
    Setting("baseline_payment_days", "Group standard payment terms (days)", "Money", 30, "int", 0, 365),
    Setting("rebate_breakage_rate", "Rebate breakage: share of earned rebate never collected", "Rebates", None, "fraction", 0, 1,
            "D-18, owner Finance / AP. Leave empty until measured; rebate figures stay blocked until then.", optional=True),
    Setting("rebate_collection_lag_months", "Months between earning and receiving rebate cash", "Rebates", 6.0, "number", 0, 60),
    Setting("tax_efficiency_off_invoice", "Value kept from an off-invoice rebate vs an invoice discount", "Rebates", 0.88, "fraction", 0.01, 1,
            "PLACEHOLDER until Tax confirms (D-19)."),
    Setting("renewal_min_days", "Renewal calendar starts (days from today)", "Renewals & alerts", 30, "int", 0, 365),
    Setting("renewal_max_days", "Renewal calendar ends (days from today)", "Renewals & alerts", 90, "int", 1, 730),
    Setting("creep_threshold", "Price rise above inflation that raises an alert", "Renewals & alerts", 0.03, "fraction", 0, 1),
    Setting("variance_threshold", "Hospital premium over the group's best that raises an alert", "Renewals & alerts", 0.05, "fraction", 0, 1),
    Setting("llm_fallback", "Send questions the built-in parser can't understand to Claude", "Assistant", False, "bool",
            help="Needs ANTHROPIC_API_KEY on the server. Question text and engine results would be sent to Anthropic."),
)
BY_KEY = {s.key: s for s in SETTINGS}
_LOCK = threading.Lock()


def _path() -> Path:
    return workspace() / "settings.json"


def defaults() -> dict[str, Any]:
    return {s.key: s.default for s in SETTINGS}


def load() -> dict[str, Any]:
    """Current settings: stored values over defaults. Unknown keys in the file are ignored."""
    values = defaults()
    p = _path()
    if p.exists():
        try:
            stored = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        values.update({k: v for k, v in stored.items() if k in BY_KEY})
    return values


def coerce(key: str, value: Any) -> Any:
    """Validate one value against its definition. Raises EngineError with a plain message."""
    s = BY_KEY.get(key)
    if s is None:
        raise EngineError(f"Unknown setting '{key}'")
    if value is None or value == "":
        if s.optional:
            return None
        raise EngineError(f"'{s.label}' can't be empty")
    if s.kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "1", "yes", "on"):
            return True
        if str(value).lower() in ("false", "0", "no", "off"):
            return False
        raise EngineError(f"'{s.label}' must be on or off")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise EngineError(f"'{s.label}' must be a number") from None
    if s.kind == "int":
        if v != int(v):
            raise EngineError(f"'{s.label}' must be a whole number")
        v = int(v)
    if (s.lo is not None and v < s.lo) or (s.hi is not None and v > s.hi):
        raise EngineError(f"'{s.label}' must be between {s.lo} and {s.hi}")
    return v


def save(changes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, tuple[Any, Any]]]:
    """Validate and store changes. Returns (new settings, {key: (old, new)}) for the audit log."""
    with _LOCK:
        current = load()
        clean = {k: coerce(k, v) for k, v in changes.items()}
        new = {**current, **clean}
        if new["renewal_min_days"] > new["renewal_max_days"]:
            raise EngineError("The renewal calendar must start before it ends")
        diff = {k: (current[k], v) for k, v in clean.items() if current[k] != v}
        p = _path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: new[k] for k in BY_KEY if new[k] != BY_KEY[k].default}, indent=2), encoding="utf-8")
        tmp.replace(p)
        return new, diff


def parameters(values: dict[str, Any] | None = None) -> E.Parameters:
    """Engine parameters built from the current settings."""
    v = values or load()
    return E.Parameters(
        wacc=v["wacc"],
        baseline_payment_days=int(v["baseline_payment_days"]),
        contract_years=v["contract_years"],
        rebate_breakage_rate=v["rebate_breakage_rate"],
        rebate_collection_lag_months=v["rebate_collection_lag_months"],
        tax_efficiency_off_invoice=v["tax_efficiency_off_invoice"],
    )


def describe() -> list[dict]:
    """The settings table for the admin form."""
    values = load()
    return [
        {"key": s.key, "label": s.label, "group": s.group, "kind": s.kind, "min": s.lo, "max": s.hi,
         "help": s.help, "optional": s.optional, "default": s.default, "value": values[s.key]}
        for s in SETTINGS
    ]
