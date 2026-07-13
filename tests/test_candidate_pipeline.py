from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import backfill_card_tags
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

    def test_candidate_prefilter_rejects_low_value_pages(self) -> None:
        biography = cards.Candidate(
            source_id="wiki-random:en:bio",
            source_name="Wikipedia",
            source_url="https://example.test/bio",
            title="Jane Example",
            excerpt=("Jane Example was born in 1960. She is an American politician and former mayor. " * 4),
            category_hint="综合",
            source_rank=4,
        )
        list_page = cards.Candidate(
            source_id="wiki-topic:en:list",
            source_name="Wikipedia",
            source_url="https://example.test/list",
            title="List of optical phenomena",
            excerpt="A" * 220,
            category_hint="科学",
            source_rank=2,
        )
        mechanism = cards.Candidate(
            source_id="wiki-topic:en:good",
            source_name="Wikipedia",
            source_url="https://example.test/good",
            title="Convection",
            excerpt="Convection happens because warmer, less dense fluid rises while cooler fluid sinks." * 4,
            category_hint="科学",
            source_rank=2,
        )

        self.assertEqual(cards.candidate_rejection_reason(biography), "biography")
        self.assertEqual(cards.candidate_rejection_reason(list_page), "weak_title")
        self.assertIsNone(cards.candidate_rejection_reason(mechanism))

    def test_semantic_fields_normalize_topic_and_tags(self) -> None:
        topic, tags = cards.semantic_fields(" 天文 ", ["引力", "观测", "引力", "很长的标签名称会被截断"], "科学")

        self.assertEqual(topic, "天文")
        self.assertEqual(tags[:3], ["天文", "引力", "观测"])
        self.assertLessEqual(len(tags), 6)

    def test_backfill_card_tags_uses_source_semantics(self) -> None:
        card = {
            "title": "A ring made by gravity",
            "lead": "Light bends around a massive object.",
            "explanation": "Einstein ring and gravitational lensing.",
            "category": "科学",
            "source_name": "English Wikipedia: Einstein ring",
            "source_url": "https://en.wikipedia.org/wiki/Einstein_ring",
        }

        topic, tags = backfill_card_tags.stable_tags(card)

        self.assertEqual(topic, "天文")
        self.assertIn("引力", tags)
        self.assertIn("科学", tags)

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

    def test_select_pool_batch_prefers_balanced_explicit_topics(self) -> None:
        def record(source_id: str, category: str, rank: int = 2) -> dict:
            candidate = cards.Candidate(
                source_id=source_id,
                source_name="Wikipedia",
                source_url=f"https://example.test/{source_id}",
                title=f"Topic {source_id}",
                excerpt="This phenomenon happens because a mechanism creates a surprising effect. " * 4,
                category_hint=category,
                source_rank=rank,
            )
            return cards.candidate_to_record(candidate)

        pool = {
            "version": 1,
            "candidates": [
                *[record(f"wiki-random:en:{index}", "综合", 4) for index in range(12)],
                record("wiki-topic:en:science", "科学"),
                record("wiki-topic:en:bio", "生物"),
                record("wiki-topic:en:life", "生活"),
                record("wiki-topic:en:art", "艺术"),
            ],
        }

        selected, selected_ids, counts = cards.select_pool_batch(pool, [], {"seen": {}}, 7)

        selected_categories = {candidate.category_hint for candidate in selected}
        self.assertIn("科学", selected_categories)
        self.assertIn("生物", selected_categories)
        self.assertIn("生活", selected_categories)
        self.assertIn("艺术", selected_categories)
        self.assertGreaterEqual(counts["explicit_topic_selected"], 4)
        self.assertEqual(len(selected_ids), len(set(selected_ids)))

    def test_semantic_duplicate_detects_same_topic_overlap(self) -> None:
        existing = [
            {
                "title": "冰为什么能浮在水面",
                "lead": "冰比液态水更疏松。",
                "category": "科学",
                "topic": "自然现象",
                "tags": ["水", "密度", "日常科学"],
            }
        ]
        candidate = {
            "title": "冰会浮起来是因为密度更低",
            "lead": "冰的结构让它比水密度低。",
            "category": "科学",
            "topic": "自然现象",
            "tags": ["水", "密度", "日常科学"],
        }

        self.assertTrue(cards.semantically_similar_to_existing(candidate, existing))

    def test_quality_summary_reports_distribution_and_rates(self) -> None:
        summary = cards.quality_summary(
            existing_cards=[{"category": "科学"}, {"category": "历史"}],
            new_cards=[{"category": "艺术"}, {"category": "艺术"}],
            source_stats={"维基主题分类": {"processed": 4, "accepted": 2}},
            final_filter_counts={"evidence_mismatch": 1},
            processed_total=5,
            reviewed_total=4,
        )

        self.assertEqual(summary["category_distribution"]["艺术"], 2)
        self.assertEqual(summary["source_pass_rates"]["维基主题分类"]["pass_rate"], 50.0)
        self.assertEqual(summary["review_pass_rate"], 50.0)
        self.assertEqual(summary["evidence_mismatch"], 1)

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
                "HARVEST_REFILL_TRIGGER": cards.HARVEST_REFILL_TRIGGER,
                "HARVEST_MIN_ADDED": cards.HARVEST_MIN_ADDED,
                "HARVEST_DIAGNOSTICS": cards.HARVEST_DIAGNOSTICS,
            }
            try:
                cards.POOL_STATUS_PATH = tmp_path / "site" / "data" / "pool_status.json"
                cards.HARVEST_REPORT_PATH = tmp_path / "data" / "harvest_report.json"
                cards.CANDIDATE_POOL_PATH = tmp_path / "data" / "candidate_pool.json"
                cards.STATE_PATH = tmp_path / "data" / "generation_state.json"
                cards.HARVEST_TARGET_PENDING = 2
                cards.HARVEST_REFILL_TRIGGER = 1
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
                report = json.loads(cards.HARVEST_REPORT_PATH.read_text(encoding="utf-8"))
                self.assertEqual(report["pool_before"], 2)
                self.assertEqual(report["pool_after"], 2)
                self.assertEqual(report["target_pool_size"], 2)
                self.assertEqual(report["refill_trigger"], 1)
                self.assertFalse(report["refill_needed"])
            finally:
                for name, value in originals.items():
                    setattr(cards, name, value)

    def test_harvest_waits_when_pool_is_between_trigger_and_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            originals = {
                "POOL_STATUS_PATH": cards.POOL_STATUS_PATH,
                "HARVEST_REPORT_PATH": cards.HARVEST_REPORT_PATH,
                "CANDIDATE_POOL_PATH": cards.CANDIDATE_POOL_PATH,
                "STATE_PATH": cards.STATE_PATH,
                "HARVEST_TARGET_PENDING": cards.HARVEST_TARGET_PENDING,
                "HARVEST_REFILL_TRIGGER": cards.HARVEST_REFILL_TRIGGER,
                "HARVEST_MIN_ADDED": cards.HARVEST_MIN_ADDED,
                "HARVEST_DIAGNOSTICS": cards.HARVEST_DIAGNOSTICS,
            }
            try:
                cards.POOL_STATUS_PATH = tmp_path / "site" / "data" / "pool_status.json"
                cards.HARVEST_REPORT_PATH = tmp_path / "data" / "harvest_report.json"
                cards.CANDIDATE_POOL_PATH = tmp_path / "data" / "candidate_pool.json"
                cards.STATE_PATH = tmp_path / "data" / "generation_state.json"
                cards.HARVEST_TARGET_PENDING = 5
                cards.HARVEST_REFILL_TRIGGER = 3
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
                            "source_id": f"wiki-topic:en:{index}",
                            "source_name": "Wikipedia",
                            "source_url": f"https://example.test/{index}",
                            "title": f"Topic {index}",
                            "excerpt": "A" * 220,
                            "category_hint": "科学",
                            "source_rank": 1,
                        }
                        for index in range(3)
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
                self.assertEqual(status["last_harvest"]["target_pool_size"], 5)
                self.assertEqual(status["last_harvest"]["refill_trigger"], 3)
                self.assertFalse(status["last_harvest"]["refill_needed"])
                self.assertEqual(
                    status["last_harvest"]["message"],
                    "候选池库存尚可，等待继续消耗",
                )
            finally:
                for name, value in originals.items():
                    setattr(cards, name, value)


if __name__ == "__main__":
    unittest.main()
