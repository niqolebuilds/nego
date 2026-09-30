"""Price history data layer: load, validate and normalise what Siloam has paid.

No MCP and no network. It reads a directory of CSV exports and turns every row
into a price per *clinical unit*, using the same ``UomConversion`` as the ENUC
kernel, so a box of 50 and a single can never be compared as if they were alike.

Files in the data directory
---------------------------
``price_history.csv`` (required)
    One row per purchase order line, contract price or vendor quote.
``sku_master.csv`` (optional)
    SKU name, clinical equivalence group and the single-source flag (D-23).
``price_index.csv`` (optional)
    Monthly price index used to bring old prices into today's money. Without it
    every comparison is flagged as not inflation-adjusted.
``rebate_programs.csv`` (optional)
    Signed rebate tiers, so realization can be tracked from purchase history.

A file named ``SAMPLE_DATA`` in the directory marks the data as synthetic, and
every answer built on it says so.

The directory is chosen by ``active_data_dir()``: the ``NEGOTIATION_DATA_DIR``
environment variable if set, else the data version an admin activated in the app,
else the bundled synthetic sample.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from .engine import BLOCKING_DATA_ELEMENTS, EngineError, UomConversion
from .numfmt import pct, rp

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
SAMPLE_MARKER = "SAMPLE_DATA"
SAMPLE_BANNER = (
    "SAMPLE DATA — synthetic prices for demonstration only. Point NEGOTIATION_DATA_DIR "
    "at a real export before relying on any number here."
)

SOURCES = ("po", "contract", "quote")

PRICE_HISTORY_COLUMNS = [
    "date",
    "hospital",
    "vendor",
    "sku",
    "sku_name",
    "equivalence_group",
    "quoted_unit",
    "clinical_units_per_quoted_unit",
    "quantity",
    "list_price",
    "discount",
    "net_price",
    "source",
    "payment_terms_days",
    "contract_end",
]
PRICE_HISTORY_REQUIRED = [
    "date",
    "hospital",
    "vendor",
    "sku",
    "quoted_unit",
    "clinical_units_per_quoted_unit",
    "quantity",
    "source",
]
SKU_MASTER_COLUMNS = ["sku", "sku_name", "equivalence_group", "clinical_unit", "single_source"]
PRICE_INDEX_COLUMNS = ["month", "index"]
REBATE_PROGRAM_COLUMNS = [
    "vendor",
    "sku",
    "period_start",
    "months_in_period",
    "tier_target_units",
    "tier_rate",
    "collected_to_date",
]


# --------------------------------------------------------------------------
# Month arithmetic (stdlib only)
# --------------------------------------------------------------------------


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def add_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(d.day, 28))


def months_between(a: date, b: date) -> int:
    """Whole calendar months from ``a`` to ``b``."""
    return (b.year - a.year) * 12 + (b.month - a.month)


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceObservation:
    """One price Siloam paid, contracted or was quoted, in the vendor's quoted unit."""

    date: date
    hospital: str
    vendor: str
    sku: str
    sku_name: str
    equivalence_group: str
    uom: UomConversion
    quantity: float
    list_price: float
    discount: float
    net_price: float
    source: str
    payment_terms_days: int | None = None
    contract_end: date | None = None

    @property
    def clinical_units(self) -> float:
        return self.uom.to_clinical_units(self.quantity)

    @property
    def net_price_per_clinical_unit(self) -> float:
        return self.uom.price_per_clinical_unit(self.net_price)

    @property
    def spend(self) -> float:
        return self.quantity * self.net_price


@dataclass(frozen=True)
class SkuInfo:
    sku: str
    sku_name: str
    equivalence_group: str
    clinical_unit: str = "unit"
    single_source: bool | None = None


@dataclass(frozen=True)
class RebateProgram:
    vendor: str
    sku: str
    period_start: date
    months_in_period: int
    tier_target_units: float
    tier_rate: float
    collected_to_date: float = 0.0


# --------------------------------------------------------------------------
# Parsing helpers — every error names the file, the row and the field
# --------------------------------------------------------------------------


def _where(file: str, row: int) -> str:
    return f"{file} row {row}"


def _parse_date(value: str, file: str, row: int, name: str) -> date:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        raise EngineError(
            f"{_where(file, row)}: '{name}' must be an ISO date (YYYY-MM-DD), got '{value}'"
        ) from None


def _parse_float(value: str, file: str, row: int, name: str) -> float:
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        raise EngineError(f"{_where(file, row)}: '{name}' must be a number, got '{value}'") from None


def _parse_bool(value: str) -> bool | None:
    v = value.strip().lower()
    if v in ("", "unknown", "null", "none"):
        return None
    return v in ("1", "true", "yes", "y")


def _read_csv(path: Path, required: Sequence[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in required if c not in headers]
        if missing:
            raise EngineError(f"{path.name}: missing required column(s) {missing}")
        return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]


def _parse_observation(r: dict[str, str], file: str, row: int) -> PriceObservation:
    for col in PRICE_HISTORY_REQUIRED:
        if col == "clinical_units_per_quoted_unit":
            continue
        if not r.get(col):
            raise EngineError(f"{_where(file, row)}: '{col}' is empty")

    cu_raw = r.get("clinical_units_per_quoted_unit", "")
    if not cu_raw:
        raise EngineError(
            f"{_where(file, row)}: 'clinical_units_per_quoted_unit' is empty for SKU "
            f"'{r['sku']}' quoted per '{r['quoted_unit']}'. This is a known blocking data "
            f"element: {BLOCKING_DATA_ELEMENTS['uom_conversion']}. The engine will not "
            f"assume 1, because a box priced as a single is the most plausible wrong number "
            f"in procurement."
        )
    cu = _parse_float(cu_raw, file, row, "clinical_units_per_quoted_unit")
    try:
        uom = UomConversion(r["quoted_unit"], cu)
    except EngineError as e:
        raise EngineError(f"{_where(file, row)}: {e}") from None

    source = r["source"].lower()
    if source not in SOURCES:
        raise EngineError(f"{_where(file, row)}: 'source' must be one of {SOURCES}, got '{source}'")

    quantity = _parse_float(r["quantity"], file, row, "quantity")
    if quantity < 0:
        raise EngineError(f"{_where(file, row)}: 'quantity' cannot be negative")

    list_price = _parse_float(r["list_price"], file, row, "list_price") if r.get("list_price") else None
    discount = _parse_float(r["discount"], file, row, "discount") if r.get("discount") else 0.0
    if not 0 <= discount < 1:
        raise EngineError(
            f"{_where(file, row)}: 'discount' must be a fraction between 0 and 1, got {discount}"
        )
    net_price = _parse_float(r["net_price"], file, row, "net_price") if r.get("net_price") else None

    if net_price is None and list_price is None:
        raise EngineError(f"{_where(file, row)}: give net_price, or list_price and discount")
    if net_price is None:
        net_price = list_price * (1.0 - discount)  # type: ignore[operator]
    if list_price is None:
        list_price = net_price / (1.0 - discount)
    if net_price < 0 or list_price < 0:
        raise EngineError(f"{_where(file, row)}: prices cannot be negative")
    if list_price and abs(list_price * (1.0 - discount) - net_price) > 0.01 * max(net_price, 1.0):
        raise EngineError(
            f"{_where(file, row)}: net_price {rp(net_price, decimals=2)} is inconsistent with list_price "
            f"{rp(list_price, decimals=2)} less discount {pct(discount)}. Fix the export or leave one blank."
        )

    terms = r.get("payment_terms_days", "")
    contract_end = r.get("contract_end", "")
    return PriceObservation(
        date=_parse_date(r["date"], file, row, "date"),
        hospital=r["hospital"],
        vendor=r["vendor"],
        sku=r["sku"],
        sku_name=r.get("sku_name") or r["sku"],
        equivalence_group=r.get("equivalence_group") or r["sku"],
        uom=uom,
        quantity=quantity,
        list_price=list_price,
        discount=discount,
        net_price=net_price,
        source=source,
        payment_terms_days=int(_parse_float(terms, file, row, "payment_terms_days")) if terms else None,
        contract_end=_parse_date(contract_end, file, row, "contract_end") if contract_end else None,
    )


# --------------------------------------------------------------------------
# The price book
# --------------------------------------------------------------------------


@dataclass
class PriceBook:
    observations: list[PriceObservation]
    skus: dict[str, SkuInfo] = field(default_factory=dict)
    price_index: dict[str, float] = field(default_factory=dict)
    rebate_programs: list[RebateProgram] = field(default_factory=list)
    source_dir: str = "(in memory)"
    is_sample: bool = False
    currency: str = "IDR"

    def __post_init__(self) -> None:
        if not self.observations:
            raise EngineError("The price book is empty: price_history has no rows")
        # SKUs seen in history but missing from the master are registered from history.
        for o in self.observations:
            if o.sku not in self.skus:
                self.skus[o.sku] = SkuInfo(o.sku, o.sku_name, o.equivalence_group)

    # ---- loading --------------------------------------------------------

    @classmethod
    def load(cls, directory: str | os.PathLike | None = None) -> "PriceBook":
        d = Path(directory or os.environ.get("NEGOTIATION_DATA_DIR") or DEFAULT_DATA_DIR)
        history = d / "price_history.csv"
        if not history.exists():
            raise EngineError(
                f"No price_history.csv in {d}. Set NEGOTIATION_DATA_DIR to the folder holding "
                "the export (templates are in data/templates/), or run "
                "scripts/generate_sample_data.py to build the synthetic sample."
            )

        rows = _read_csv(history, PRICE_HISTORY_REQUIRED)
        observations = [_parse_observation(r, history.name, i) for i, r in enumerate(rows, start=2)]

        skus: dict[str, SkuInfo] = {}
        master = d / "sku_master.csv"
        if master.exists():
            for i, r in enumerate(_read_csv(master, ["sku"]), start=2):
                if not r.get("sku"):
                    raise EngineError(f"{_where(master.name, i)}: 'sku' is empty")
                skus[r["sku"]] = SkuInfo(
                    sku=r["sku"],
                    sku_name=r.get("sku_name") or r["sku"],
                    equivalence_group=r.get("equivalence_group") or r["sku"],
                    clinical_unit=r.get("clinical_unit") or "unit",
                    single_source=_parse_bool(r.get("single_source", "")),
                )

        index: dict[str, float] = {}
        idx = d / "price_index.csv"
        if idx.exists():
            for i, r in enumerate(_read_csv(idx, PRICE_INDEX_COLUMNS), start=2):
                value = _parse_float(r["index"], idx.name, i, "index")
                if value <= 0:
                    raise EngineError(f"{_where(idx.name, i)}: 'index' must be positive")
                index[r["month"][:7]] = value

        programs: list[RebateProgram] = []
        rp = d / "rebate_programs.csv"
        if rp.exists():
            for i, r in enumerate(_read_csv(rp, REBATE_PROGRAM_COLUMNS), start=2):
                programs.append(
                    RebateProgram(
                        vendor=r["vendor"],
                        sku=r["sku"],
                        period_start=_parse_date(r["period_start"], rp.name, i, "period_start"),
                        months_in_period=int(_parse_float(r["months_in_period"], rp.name, i, "months_in_period")),
                        tier_target_units=_parse_float(r["tier_target_units"], rp.name, i, "tier_target_units"),
                        tier_rate=_parse_float(r["tier_rate"], rp.name, i, "tier_rate"),
                        collected_to_date=_parse_float(r["collected_to_date"] or "0", rp.name, i, "collected_to_date"),
                    )
                )

        return cls(
            observations=observations,
            skus=skus,
            price_index=index,
            rebate_programs=programs,
            source_dir=str(d),
            is_sample=(d / SAMPLE_MARKER).exists(),
        )

    # ---- dates and inflation -------------------------------------------

    @property
    def latest_date(self) -> date:
        return max(o.date for o in self.observations)

    @property
    def earliest_date(self) -> date:
        return min(o.date for o in self.observations)

    def resolve_as_of(self, as_of: date | None) -> date:
        """Default to the latest date in the data, so answers are stable for a given export."""
        return as_of or self.latest_date

    def index_factor(self, when: date, as_of: date) -> float | None:
        """Multiplier bringing a price from ``when`` into ``as_of`` money, or None if unknown."""
        if not self.price_index:
            return None
        a, b = self.price_index.get(month_key(when)), self.price_index.get(month_key(as_of))
        if a is None or b is None:
            return None
        return b / a

    def adjusted_unit_price(self, o: PriceObservation, as_of: date) -> tuple[float, bool]:
        """Net price per clinical unit in ``as_of`` money, and whether it was adjusted."""
        factor = self.index_factor(o.date, as_of)
        if factor is None:
            return o.net_price_per_clinical_unit, False
        return o.net_price_per_clinical_unit * factor, True

    # ---- lookup ---------------------------------------------------------

    def resolve_sku(self, query: str, vendor: str | None = None) -> SkuInfo:
        """Find one SKU by exact code, or by a unique case-insensitive name fragment.

        When a fragment matches several SKUs and a vendor is given, the SKUs that
        vendor has supplied or quoted break the tie.
        """
        q = query.strip()
        if q in self.skus:
            return self.skus[q]
        lowered = q.lower()
        exact = [s for s in self.skus.values() if s.sku.lower() == lowered or s.sku_name.lower() == lowered]
        if len(exact) == 1:
            return exact[0]
        matches = [s for s in self.skus.values() if lowered in s.sku_name.lower() or lowered in s.sku.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 and vendor:
            v = vendor.strip().lower()
            sold = {o.sku for o in self.observations if v in o.vendor.lower()}
            narrowed = [s for s in matches if s.sku in sold]
            if len(narrowed) == 1:
                return narrowed[0]
        if not matches:
            raise EngineError(
                f"No SKU matches '{query}'. Use negotiation_price_lookup with a shorter "
                "search term to see what exists."
            )
        listing = ", ".join(f"{s.sku} ({s.sku_name})" for s in sorted(matches, key=lambda s: s.sku)[:10])
        raise EngineError(f"'{query}' matches {len(matches)} SKUs — be more specific: {listing}")

    def resolve_vendor(self, query: str) -> str:
        vendors = sorted({o.vendor for o in self.observations})
        lowered = query.strip().lower()
        exact = [v for v in vendors if v.lower() == lowered]
        if exact:
            return exact[0]
        matches = [v for v in vendors if lowered in v.lower()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            # A vendor with no history is legitimate: a new entrant quoting for the first time.
            return query.strip()
        raise EngineError(f"'{query}' matches several vendors — be more specific: {matches}")

    def group_skus(self, equivalence_group: str) -> list[SkuInfo]:
        return [s for s in self.skus.values() if s.equivalence_group == equivalence_group]

    def query(
        self,
        *,
        skus: Iterable[str] | None = None,
        text: str | None = None,
        equivalence_group: str | None = None,
        vendor: str | None = None,
        hospital: str | None = None,
        since: date | None = None,
        until: date | None = None,
        sources: Iterable[str] | None = None,
    ) -> list[PriceObservation]:
        sku_set = set(skus) if skus is not None else None
        source_set = set(sources) if sources is not None else None
        t = text.lower() if text else None
        v = vendor.lower() if vendor else None
        h = hospital.lower() if hospital else None
        out = []
        for o in self.observations:
            if sku_set is not None and o.sku not in sku_set:
                continue
            if t and t not in o.sku_name.lower() and t not in o.sku.lower():
                continue
            if equivalence_group and self.skus[o.sku].equivalence_group != equivalence_group:
                continue
            if v and v not in o.vendor.lower():
                continue
            if h and h not in o.hospital.lower():
                continue
            if since and o.date < since:
                continue
            if until and o.date > until:
                continue
            if source_set is not None and o.source not in source_set:
                continue
            out.append(o)
        return out

    # ---- status ---------------------------------------------------------

    def status(self) -> dict:
        by_source: dict[str, int] = {}
        for o in self.observations:
            by_source[o.source] = by_source.get(o.source, 0) + 1
        return {
            "data_dir": self.source_dir,
            "is_sample": self.is_sample,
            "rows": len(self.observations),
            "rows_by_source": by_source,
            "skus": len(self.skus),
            "equivalence_groups": len({s.equivalence_group for s in self.skus.values()}),
            "vendors": len({o.vendor for o in self.observations}),
            "hospitals": len({o.hospital for o in self.observations}),
            "date_from": self.earliest_date.isoformat(),
            "date_to": self.latest_date.isoformat(),
            "inflation_index_loaded": bool(self.price_index),
            "rebate_programs": len(self.rebate_programs),
            "single_source_flag_known": sum(1 for s in self.skus.values() if s.single_source is not None),
        }


_CACHE: dict = {}
ACTIVE_POINTER = "ACTIVE"


def active_data_dir() -> Path:
    """Which price data the app uses, in order of precedence.

    1. ``NEGOTIATION_DATA_DIR``, when set: an explicit override always wins.
    2. The version an admin activated in the workspace (``versions/<id>``).
    3. The bundled synthetic sample.
    """
    if os.environ.get("NEGOTIATION_DATA_DIR"):
        return Path(os.environ["NEGOTIATION_DATA_DIR"])
    from .settings import workspace

    pointer = workspace() / ACTIVE_POINTER
    if pointer.exists():
        version = pointer.read_text(encoding="utf-8").strip()
        candidate = workspace() / "versions" / version
        if version and (candidate / "price_history.csv").exists():
            return candidate
    return DEFAULT_DATA_DIR


def cached_book() -> PriceBook:
    """Load the price book once, and again only when the active data changes."""
    directory = active_data_dir()
    history = directory / "price_history.csv"
    stamp = (str(directory), history.stat().st_mtime if history.exists() else None)
    if _CACHE.get("stamp") != stamp:
        _CACHE["book"] = PriceBook.load(directory)
        _CACHE["stamp"] = stamp
    return _CACHE["book"]


def write_templates(directory: str | os.PathLike) -> None:
    """Write header-only CSV templates describing exactly what to export."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    for name, cols in (
        ("price_history.csv", PRICE_HISTORY_COLUMNS),
        ("sku_master.csv", SKU_MASTER_COLUMNS),
        ("price_index.csv", PRICE_INDEX_COLUMNS),
        ("rebate_programs.csv", REBATE_PROGRAM_COLUMNS),
    ):
        with (d / name).open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(cols)
