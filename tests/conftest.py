"""Shared fixtures: a tiny hand-built price book whose answers can be checked by hand."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

import pytest

# Every test run gets its own workspace (settings, app.db, versions, documents), so tests
# never read or write a real one. Set before any app module computes a path.
os.environ["NEGO_HOME"] = tempfile.mkdtemp(prefix="nego-test-")
os.environ.pop("NEGOTIATION_DATA_DIR", None)
os.environ["NEGO_SCHEDULER"] = "0"  # tests call notify.daily() themselves

from negotiation_mcp.pricebook import PRICE_HISTORY_COLUMNS, PriceBook

ROOT = Path(__file__).resolve().parent.parent

# Two sites buy one SKU from one vendor; a competitor sells an equivalent SKU cheaper.
#   IVC-A (Alpha, box of 50):  Site North pays 500,000/box = 10,000/unit, Site South 550,000 = 11,000/unit
#   IVC-B (Beta, each):        quoted 9,500/unit, bought at 9,800/unit
ROWS = [
    # date, hospital, vendor, sku, name, group, unit, cu, qty, list, discount, net, source, terms, end
    ("2025-09-15", "Site North", "Alpha", "IVC-A", "Cannula (Alpha)", "Cannula 22G", "box of 50", 50, 100, "", 0.0, 500000, "po", 30, ""),
    ("2025-09-15", "Site South", "Alpha", "IVC-A", "Cannula (Alpha)", "Cannula 22G", "box of 50", 50, 50, "", 0.0, 550000, "po", 30, ""),
    ("2026-03-15", "Site North", "Alpha", "IVC-A", "Cannula (Alpha)", "Cannula 22G", "box of 50", 50, 100, 555555.56, 0.10, 500000, "po", 30, ""),
    ("2026-03-15", "Site South", "Alpha", "IVC-A", "Cannula (Alpha)", "Cannula 22G", "box of 50", 50, 50, "", 0.0, 550000, "po", 30, ""),
    ("2026-01-01", "Site South", "Alpha", "IVC-A", "Cannula (Alpha)", "Cannula 22G", "box of 50", 50, 600, "", 0.0, 550000, "contract", 30, "2026-12-31"),
    ("2026-03-15", "Site North", "Beta", "IVC-B", "Cannula (Beta)", "Cannula 22G", "each", 1, 1000, "", 0.0, 9800, "po", 30, ""),
    ("2026-02-15", "Site South", "Beta", "IVC-B", "Cannula (Beta)", "Cannula 22G", "each", 1, 60000, "", 0.0, 9500, "quote", 30, ""),
    ("2024-03-15", "Site North", "Alpha", "IVC-A", "Cannula (Alpha)", "Cannula 22G", "box of 50", 50, 100, "", 0.0, 480000, "po", 30, ""),
]


def write_book(directory: Path, rows=ROWS, index: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "price_history.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(PRICE_HISTORY_COLUMNS)
        w.writerows(rows)
    if index:
        with (directory / "price_index.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["month", "index"])
            w.writerow(["2024-03", 100])
            w.writerow(["2026-03", 110])
    return directory


@pytest.fixture
def tiny_book(tmp_path) -> PriceBook:
    return PriceBook.load(write_book(tmp_path / "tiny"))


@pytest.fixture(scope="session")
def sample_book() -> PriceBook:
    return PriceBook.load(ROOT / "data" / "sample")
