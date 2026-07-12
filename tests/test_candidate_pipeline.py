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

    def test_harvest_succeeds_when_pool_is_already_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            originals = {
                "POOL_STATUS_PATH": cards.POOL_STATUS_PATH,
                "HARVEST_REPORT_PATH": cards.HARVEST_REPORT_PATH,
                "CANDIDATE_POOL_PATH": cards.CANDIDATE_POOL_PATH,
                "STATE_PATH": cards.STATE_PATH,
                "HARVEST_TARGET_PENDING": cards.HARVEST_TARGET_PENDING,
                "HARVEST_MIN_ADDED": cards.HARVEST_MIN_ADDED,
                "HARVEST_DIAGNOSTICS": cards.HARVEST_DIAGNOSTICS,
            }
            try:
                cards.POOL_STATUS_PATH = tmp_path / "site" / "data" / "pool_status.json"
                cards.HARVEST_REPORT_PATH = tmp_path / "data" / "harvest_report.json"
                cards.CANDIDATE_POOL_PATH = tmp_path / "data" / "candidate_pool.json"
                cards.STATE_PATH = tmp_path / "data" / "generation_state.json"
                cards.HARVEST_TARGET_PENDING = 2
                cards.HARVEST_MIN_ADDED = 20
                cards.HARVEST_DIAGNOSTICS = {
                    "version": 1,
                    "generated_at": None,
                    "user_agent": cards.USER_AGENT,
                    "sources": {},
                    "requests": [],
                }
                pool = {
                    "version": 1,
                    "candidates": [
                        {
                            "source_id": "wiki-topic:en:1",
                            "source_name": "Wikipedia",
                            "source_url": "https://example.test/1",
                            "title": "Convection",
                            "excerpt": "A" * 220,
                            "category_hint": "科学",
                            "source_rank": 1,
                        },
                        {
                            "source_id": "wiki-topic:en:2",
                            "source_name": "Wikipedia",
                            "source_url": "https://example.test/2",
                            "title": "Reflection",
                            "excerpt": "B" * 220,
                            "category_hint": "科学",
                            "source_rank": 1,
                        },
                    ],
                }

                exit_code = cards.run_harvest(
                    cards.utc_now(),
                    {"version": 1, "cards": []},
                    pool,
                    {"version": 1, "seen": {}},
                )

                status = json.loads(cards.POOL_STATUS_PATH.read_text(encoding="utf-8"))
                self.assertEqual(exit_code, 0)
                self.assertTrue(status["last_harvest"]["success"])
                self.assertEqual(status["last_harvest"]["passes"], 0)
                self.assertEqual(status["last_harvest"]["added"], 0)
                self.assertEqual(
                    status["last_harvest"]["message"],
                    "候选池库存充足，无需补充",
                )
            finally:
                for name, value in originals.items():
                    setattr(cards, name, value)


if __name__ == "__main__":
    unittest.main()
