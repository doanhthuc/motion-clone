#!/usr/bin/env python3
"""Gate: every PIPELINES stage has a Vast model-registry entry, and every id it names is real.

    python3 scripts/check_vast_models.py     # make check-vast-models
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.vast_models import check_drift


def main() -> int:
    errors = check_drift()
    if errors:
        print("✗ scripts/batchlib/vast_models.py has drifted from PIPELINES or the catalog:",
              file=sys.stderr)
        for e in errors:
            print(f"    - {e}", file=sys.stderr)
        return 1
    print("✓ vast model registry matches PIPELINES and the catalog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
