#!/usr/bin/env python3
"""Small source probe for the candidate harvesting pipeline.

This script exercises public data sources only. It never calls OpenAI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generate_cards as cards  # noqa: E402


def main() -> int:
    probes = [
        ("Wikipedia topic samples", cards.fetch_wikipedia_topic_samples),
        (
            "English Wikipedia random",
            lambda: cards.fetch_wikipedia_random_samples("en", 8, 4),
        ),
        (
            "Chinese Wikipedia random",
            lambda: cards.fetch_wikipedia_random_samples("zh", 5, 4),
        ),
        ("Wikimedia On This Day", cards.fetch_on_this_day),
        ("NASA APOD", cards.fetch_nasa_apod),
    ]

    results = []
    ok_sources = 0
    for name, probe in probes:
        try:
            items = probe()
            ok = bool(items)
            ok_sources += int(ok)
            results.append(
                {
                    "source": name,
                    "ok": ok,
                    "candidates": len(items),
                    "sample_ids": [item.source_id for item in items[:3]],
                }
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic CLI must report any failure.
            results.append(
                {
                    "source": name,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:260],
                }
            )

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if ok_sources >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
