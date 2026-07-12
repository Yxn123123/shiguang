from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generate_cards as cards


class CandidatePipelineTests(unittest.TestCase):
    def test_met_catalogue_records_are_low_information(self) -> None:
        candidate = cards.Candidate(
            source_id="met:123",
            source_name="The Met",
            source_url="https://example.test/met/123",
            title="Small Bowl",
            excerpt=(
                "作品：Small Bowl\n用途或对象：Bowl\n年代：1200 BCE\n"
                "材质：Ceramic\n尺寸：2 x 2 cm"
            ),
            category_hint="艺术",
            source_rank=2,
        )

        self.assertTrue(cards.candidate_is_low_information(candidate))

    def test_merge_candidates_deduplicates_and_filters(self) -> None:
        pool = {"version": 1, "candidates": []}
        existing_cards = [
            {
                "source_id": "wiki-topic:en:old",
                "source_url": "https://example.test/old",
            }
        ]
        good = cards.Candidate(
            source_id="wiki-topic:en:1",
            source_name="Wikipedia",
            source_url="https://example.test/new",
            title="Convection",
            excerpt="A" * 220,
            category_hint="科学",
            source_rank=1,
        )
        duplicate = cards.Candidate(
            source_id="wiki-topic:en:old",
            source_name="Wikipedia",
            source_url="https://example.test/old",
            title="Old",
            excerpt="B" * 220,
            category_hint="科学",
            source_rank=1,
        )
        weak = cards.Candidate(
            source_id="met:999",
            source_name="The Met",
            source_url="https://example.test/met/999",
            title="Whistle",
            excerpt="C" * 220,
            category_hint="艺术",
            source_rank=2,
        )

        added, counts = cards.merge_candidates_into_pool(
            pool,
            [good, duplicate, weak, good],
            existing_cards,
            {"seen": {}},
        )

        self.assertEqual(added, 1)
        self.assertEqual(len(pool["candidates"]), 1)
        self.assertEqual(pool["candidates"][0]["source_id"], "wiki-topic:en:1")
        self.assertEqual(counts["already_published"], 1)
        self.assertEqual(counts["already_in_pool"], 1)
        self.assertEqual(counts["low_information"], 1)

    def test_save_harvest_status_preserves_last_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            original_path = cards.POOL_STATUS_PATH
            try:
                cards.POOL_STATUS_PATH = tmp_path / "site" / "data" / "pool_status.json"
                cards.POOL_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
                cards.POOL_STATUS_PATH.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "approved_cards": 29,
                            "pending_candidates": 5,
                            "last_run": {"added": 2},
                            "recent_runs": [{"added": 2}],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                cards.save_harvest_status(
                    cards_count=29,
                    pending_count=25,
                    stats={"added": 20, "source_counts": {"NASA APOD": 3}},
                )

                payload = json.loads(cards.POOL_STATUS_PATH.read_text(encoding="utf-8"))
                self.assertEqual(payload["last_run"]["added"], 2)
                self.assertEqual(payload["last_harvest"]["added"], 20)
                self.assertEqual(payload["pending_candidates"], 25)
            finally:
                cards.POOL_STATUS_PATH = original_path


if __name__ == "__main__":
    unittest.main()
