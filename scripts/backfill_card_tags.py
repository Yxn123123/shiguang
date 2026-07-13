#!/usr/bin/env python3
"""Backfill optional topic and tags fields for existing knowledge cards."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "site" / "data" / "cards.json"

CATEGORY_TAGS = {
    "科学": ["科学", "自然现象"],
    "科技": ["科技", "工程"],
    "历史": ["历史", "历史事件"],
    "生物": ["生物", "生命机制"],
    "艺术": ["艺术", "设计"],
    "生活": ["生活", "日常知识"],
    "综合": ["综合", "跨学科"],
}

RULES = [
    (("einstein", "gravit", "lens", "venus", "moon", "astronom", "planet", "universe"), "天文", ["天文", "引力", "宇宙", "观测"]),
    (("weather", "butterfly", "chaos", "convection", "ice", "glass", "acoustic", "sound"), "自然现象", ["自然现象", "物理机制", "日常科学"]),
    (("chatoyancy", "ultramarine", "lapis", "pigment", "pencil", "graphite"), "材料", ["材料", "物性", "工艺"]),
    (("molography", "biomolecular", "molecule", "qr", "error correction"), "工程", ["工程", "检测技术", "信息技术"]),
    (("seismology", "earthquake", "eiffel", "airplane", "window", "comet"), "工程安全", ["工程", "结构安全", "设计细节"]),
    (("wombat", "octopus", "shark", "berry", "banana", "strawberry", "fossil"), "动物与演化", ["生物", "动物行为", "演化"]),
    (("capsaicin", "spicy", "taste", "trpv1"), "感官", ["生活", "感官", "身体机制"]),
    (("byzantine", "oxford", "aztec", "surrender", "world cup", "seismologists"), "历史", ["历史", "时间线", "历史事件"]),
    (("sculpture", "metmuseum", "princess mononoke", "movie", "color", "painted"), "艺术设计", ["艺术", "设计", "视觉文化"]),
    (("language", "word", "name", "roman", "identity"), "语言与命名", ["语言", "命名", "文化"]),
]


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", " ", text)


def compact_label(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip()
    value = re.sub(r"[\s，,、;；|/]+", " ", value).strip()
    return value[:12]


def stable_tags(card: dict) -> tuple[str, list[str]]:
    haystack = normalize_text(
        " ".join(
            str(card.get(field, ""))
            for field in (
                "title",
                "lead",
                "explanation",
                "angle",
                "category",
                "source_name",
                "source_url",
                "evidence",
            )
        )
    )
    category = str(card.get("category") or "综合")
    tags: list[str] = []
    topic = ""

    for keywords, rule_topic, rule_tags in RULES:
        if any(keyword in haystack for keyword in keywords):
            if not topic:
                topic = rule_topic
            tags.extend(rule_tags)

    tags.extend(CATEGORY_TAGS.get(category, CATEGORY_TAGS["综合"]))
    if not topic:
        topic = tags[0] if tags else category

    result: list[str] = []
    seen: set[str] = set()
    for raw in [topic, *tags, category]:
        label = compact_label(raw)
        key = normalize_text(label)
        if not label or key in seen:
            continue
        seen.add(key)
        result.append(label)
        if len(result) >= 6:
            break

    while len(result) < 3:
        fallback = compact_label(category if category not in result else "综合")
        if not fallback or fallback in result:
            break
        result.append(fallback)

    return compact_label(topic), result[:6]


def backfill_cards(payload: dict) -> int:
    changed = 0
    for card in payload.get("cards", []):
        topic, tags = stable_tags(card)
        if card.get("topic") != topic:
            card["topic"] = topic
            changed += 1
        if card.get("tags") != tags:
            card["tags"] = tags
            changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill optional topic/tags fields in cards.json.")
    parser.add_argument("--check", action="store_true", help="Fail if cards.json would change.")
    args = parser.parse_args()

    payload = json.loads(CARDS_PATH.read_text(encoding="utf-8"))
    changed = backfill_cards(payload)
    if args.check:
        print(f"cards needing semantic tag updates: {changed}")
        return 1 if changed else 0
    if changed:
        payload["updated_at"] = payload.get("updated_at")
        CARDS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"updated semantic tag fields: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
