#!/usr/bin/env python3
"""Run the checkout's generic product TestKit from any working directory.

The TestKit intentionally lives under ``tests/`` while it is being developed
with ZaoFu.  This thin launcher makes the same implementation callable against
an external product without relying on the caller's current directory being
the ZaoFu checkout.
"""

from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    root_text = str(repository_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from tests.product_testkit.__main__ import main as product_testkit_main

    return product_testkit_main()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

