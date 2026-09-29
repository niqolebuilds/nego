#!/usr/bin/env python3
"""Build data/warehouse.db from the price book, for Metabase / Superset.

    python scripts/build_warehouse.py [--db data/warehouse.db] [--breakage 0.10]

Reads NEGOTIATION_DATA_DIR (default: the synthetic sample). --breakage sets the
rebate breakage rate (D-18) used for rebate alerts; without it those alerts say
they are blocked rather than guessing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from negotiation_mcp import engine as E  # noqa: E402
from negotiation_mcp.pricebook import PriceBook  # noqa: E402
from negotiation_mcp.warehouse import build  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ROOT / "data" / "warehouse.db"))
    ap.add_argument("--data-dir", default=None, help="Overrides NEGOTIATION_DATA_DIR")
    ap.add_argument("--breakage", type=float, default=None, help="Rebate breakage rate (D-18), e.g. 0.10")
    args = ap.parse_args()

    book = PriceBook.load(args.data_dir)
    counts = build(book, args.db, E.Parameters(rebate_breakage_rate=args.breakage))
    print(f"Built {args.db} from {book.source_dir}{' (SAMPLE DATA)' if book.is_sample else ''}")
    for table, n in counts.items():
        print(f"  {table:<28} {n:>7,} rows")


if __name__ == "__main__":
    main()
