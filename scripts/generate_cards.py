#!/usr/bin/env python3
"""Generate high-quality Chinese knowledge cards from public source material."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "site" / "data" / "cards.json"
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
MAX_NEW_CARDS = int(os.getenv("MAX_NEW_CARDS", "8"))
USER_AGENT = "ShiguangKnowledgePWA/1.0 (personal educational project)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
TIMEOUT = 20

CATEGORIES = ["生物", "科学", "历史", "艺术", "科技", "生活", "综合"]


@dataclass
class Candidate:
    source_id: str
    source_name: str
    source_url: str
    title: str
    excerpt: str
    category_hint: str = "综合"


class Proposal(BaseModel):
    source_id: str
    title: str = Field(description="具体、有趣、自然的中文标题")
    lead: str = Field(description="一句核心结论")
    explanation: str = Field(description="解释原因或背景")
    angle: str = Field(description="换个角度的一句话")
    category: str
    evidence: str = Field(description="从候选原文中原样摘取的短依据")
    why_interesting: str
    confidence: int = Field(ge=0, le=100)


class ProposalBatch(BaseModel):
    cards: list[Proposal]


class ReviewItem(BaseModel):
    source_id: str
    approved: bool
    rejection_reason: str
    title: str
    lead: str
    explanation: str
    angle: str
    category: str
    evidence: str
    quality_score: int = Field(ge=0, le=100)


class ReviewBatch(BaseModel):
    items: list[ReviewItem]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def request_json(url: str, **params) -> dict:
    response = SESSION.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def batch(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def wikipedia_extracts(titles: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for title_batch in batch(titles, 18):
        payload = request_json(
            "https://zh.wikipedia.org/w/api.php",
            action="query",
            prop="extracts|info",
            exintro=1,
            explaintext=1,
            exchars=1600,
            inprop="url",
            titles="|".join(title_batch),
            format="json",
            origin="*",
        )
        for page in payload.get("query", {}).get("pages", {}).values():
            if page.get("title"):
                result[page["title"]] = page
    return result


def fetch_wikipedia_dyk() -> list[Candidate]:
    payload = request_json(
        "https://zh.wikipedia.org/w/api.php",
        action="parse",
        page="Template:Dyk",
        prop="text",
        format="json",
        origin="*",
    )
    html = payload.get("parse", {}).get("text", {}).get("*", "")
    soup = BeautifulSoup(html, "html.parser")

    raw: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for item in soup.select("li"):
        question = normalize_space(item.get_text(" ", strip=True))
        if len(question) < 12 or len(question) > 130:
            continue
        if not question.endswith(("？", "?")):
            continue

        links = []
        for anchor in item.select("a[href^='/wiki/']"):
            title = anchor.get("title", "")
            if not title or ":" in title:
                continue
            links.append(anchor)

        if not links:
            continue

        preferred = item.select_one("b a[href^='/wiki/'], strong a[href^='/wiki/']") or links[0]
        article_title = preferred.get("title", "")
        if not article_title or article_title in seen:
            continue
        seen.add(article_title)
        source_url = "https://zh.wikipedia.org" + preferred.get("href", "")
        raw.append((question, article_title, source_url))

    pages = wikipedia_extracts([item[1] for item in raw[:24]])
    candidates: list[Candidate] = []

    for question, article_title, source_url in raw[:24]:
        page = pages.get(article_title, {})
        extract = normalize_space(page.get("extract", ""))
        if len(extract) < 120:
            continue
        candidates.append(
            Candidate(
                source_id=f"wiki-dyk:{page.get('pageid', article_title)}",
                source_name=f"中文维基百科：{article_title}",
                source_url=page.get("fullurl", source_url),
                title=question,
                excerpt=f"栏目问题：{question}\n词条摘要：{extract}",
            )
        )
    return candidates


def fetch_on_this_day() -> list[Candidate]:
    today = utc_now()
    month_day = f"{today.month:02d}/{today.day:02d}"
    payload = None
    language = "zh"

    for language_code in ("zh", "en"):
        url = f"https://api.wikimedia.org/feed/v1/wikipedia/{language_code}/onthisday/all/{month_day}"
        try:
            response = SESSION.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            language = language_code
            break
        except requests.RequestException:
            continue

    if not payload:
        return []

    candidates: list[Candidate] = []
    events = payload.get("events", [])[:10]

    for index, event in enumerate(events):
        text = normalize_space(event.get("text", ""))
        year = event.get("year", "")
        pages = event.get("pages", [])
        page = pages[0] if pages else {}
        page_title = page.get("normalizedtitle") or page.get("title") or f"{year}年事件"
        extract = normalize_space(page.get("extract", ""))
        url = (
            page.get("content_urls", {})
            .get("desktop", {})
            .get("page", "")
        )
        if len(text) < 30:
            continue
        candidates.append(
            Candidate(
                source_id=f"onthisday:{language}:{month_day}:{year}:{index}",
                source_name=f"Wikimedia On This Day：{page_title}",
                source_url=url or f"https://{language}.wikipedia.org/wiki/{page_title}",
                title=f"{year}年的今天：{text}",
                excerpt=f"事件：{text}\n相关页面摘要：{extract}",
                category_hint="历史",
            )
        )
    return candidates


def fetch_nasa_apod() -> list[Candidate]:
    api_key = os.getenv("NASA_API_KEY", "DEMO_KEY")
    try:
        payload = request_json(
            "https://api.nasa.gov/planetary/apod",
            api_key=api_key,
            count=3,
            thumbs=True,
        )
    except requests.RequestException:
        return []

    candidates: list[Candidate] = []
    for item in payload if isinstance(payload, list) else []:
        explanation = normalize_space(item.get("explanation", ""))
        title = normalize_space(item.get("title", ""))
        if len(explanation) < 120:
            continue
        candidates.append(
            Candidate(
                source_id=f"nasa-apod:{item.get('date', title)}",
                source_name=f"NASA Astronomy Picture of the Day：{title}",
                source_url=item.get("url") or item.get("hdurl") or "https://apod.nasa.gov/",
                title=title,
                excerpt=explanation,
                category_hint="科学",
            )
        )
    return candidates


def fetch_met_objects() -> list[Candidate]:
    rng = random.Random(utc_now().date().isoformat())
    queries = ["ancient", "textile", "instrument", "ceramic", "painting", "jewelry"]
    rng.shuffle(queries)
    candidates: list[Candidate] = []

    for query in queries[:2]:
        try:
            search = request_json(
                "https://collectionapi.metmuseum.org/public/collection/v1/search",
                hasImages="true",
                q=query,
            )
            object_ids = search.get("objectIDs") or []
            if not object_ids:
                continue
            object_id = rng.choice(object_ids[: min(len(object_ids), 200)])
            item = request_json(
                f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{object_id}"
            )
        except requests.RequestException:
            continue

        title = normalize_space(item.get("title", ""))
        if not title:
            continue

        facts = {
            "作品": title,
            "年代": normalize_space(item.get("objectDate", "")),
            "文化": normalize_space(item.get("culture", "")),
            "材质": normalize_space(item.get("medium", "")),
            "类别": normalize_space(item.get("classification", "")),
            "部门": normalize_space(item.get("department", "")),
            "说明": normalize_space(item.get("creditLine", "")),
        }
        excerpt = "\n".join(f"{key}：{value}" for key, value in facts.items() if value)
        if len(excerpt) < 60:
            continue

        candidates.append(
            Candidate(
                source_id=f"met:{object_id}",
                source_name=f"The Metropolitan Museum of Art：{title}",
                source_url=item.get("objectURL", "https://www.metmuseum.org/art/collection"),
                title=title,
                excerpt=excerpt,
                category_hint="艺术",
            )
        )
    return candidates


def fetch_candidates() -> list[Candidate]:
    providers = [
        ("Wikipedia DYK", fetch_wikipedia_dyk),
        ("Wikimedia On This Day", fetch_on_this_day),
        ("NASA APOD", fetch_nasa_apod),
        ("The Met", fetch_met_objects),
    ]
    collected: list[Candidate] = []

    for name, provider in providers:
        try:
            items = provider()
            print(f"[source] {name}: {len(items)} candidates")
            collected.extend(items)
        except Exception as exc:
            print(f"[source] {name} failed: {exc}", file=sys.stderr)

    unique: dict[str, Candidate] = {}
    for item in collected:
        unique[item.source_id] = item
    return list(unique.values())


EXTRACTION_INSTRUCTIONS = """
你是“拾光”知识编辑。任务不是概括文章，而是从候选材料中找出真正适合轻阅读的、具体且可验证的小知识。

硬性规则：
1. 只能依据候选材料，禁止补充材料之外的事实。
2. 每个候选最多生成1条；没有值得记住的知识点就直接不输出。
3. 自动拒绝：纯人物生平、机构简介、职位定义、国家概况、军舰参数、普通日期罗列、宽泛主题介绍。
4. 禁止标题模板：“X有什么值得注意的地方”“X是什么”“关于X你知道吗”。
5. 标题应是具体事实或自然问题，8—34个汉字，读完就能知道意外点。
6. lead 15—55字；explanation 55—150字；angle 20—70字。
7. evidence 必须从候选原文中原样摘取，最多80字。
8. 分类只能是：生物、科学、历史、艺术、科技、生活、综合。
9. 宁缺毋滥。普通或无趣内容不要输出。
""".strip()

REVIEW_INSTRUCTIONS = """
你是严格的知识内容主编。逐条审查候选卡，只有同时满足以下条件才批准：
- 是具体、出乎意料但不猎奇造假的事实；
- 标题与正文完全对应；
- 所有事实都能由提供的原文证据支持；
- 不是百科定义、人物简介、机构介绍、职位说明或参数罗列；
- 中文自然，不像机器套模板；
- 首屏适合手机阅读；
- evidence 是来源中的原句；
- 不涉及未经来源支持的医疗建议或时效性政治判断。

可以在不增加新事实的前提下精简和润色。质量低于82分必须拒绝。
""".strip()


def extract_proposals(client: OpenAI, candidates: list[Candidate]) -> list[Proposal]:
    proposals: list[Proposal] = []

    for candidate_batch in batch(candidates, 6):
        input_data = [asdict(candidate) for candidate in candidate_batch]
        response = client.responses.parse(
            model=MODEL,
            reasoning={"effort": "low"},
            input=[
                {"role": "system", "content": EXTRACTION_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": "请从以下候选材料中提取知识卡：\n"
                    + json.dumps(input_data, ensure_ascii=False, indent=2),
                },
            ],
            text_format=ProposalBatch,
        )
        parsed = response.output_parsed
        if parsed:
            proposals.extend(parsed.cards)
    return proposals


def review_proposals(
    client: OpenAI,
    proposals: list[Proposal],
    candidate_map: dict[str, Candidate],
) -> list[ReviewItem]:
    approved: list[ReviewItem] = []

    for proposal_batch in batch(proposals, 8):
        review_input = []
        for proposal in proposal_batch:
            candidate = candidate_map.get(proposal.source_id)
            if not candidate:
                continue
            review_input.append(
                {
                    "proposal": proposal.model_dump(),
                    "source": asdict(candidate),
                }
            )

        if not review_input:
            continue

        response = client.responses.parse(
            model=MODEL,
            reasoning={"effort": "low"},
            input=[
                {"role": "system", "content": REVIEW_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": "请审核以下知识卡和来源：\n"
                    + json.dumps(review_input, ensure_ascii=False, indent=2),
                },
            ],
            text_format=ReviewBatch,
        )
        parsed = response.output_parsed
        if parsed:
            approved.extend(parsed.items)
    return approved


def normalized(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def evidence_supported(evidence: str, source_text: str) -> bool:
    evidence_norm = normalize_space(evidence)
    source_norm = normalize_space(source_text)
    return len(evidence_norm) >= 8 and evidence_norm in source_norm


def title_is_bad(title: str) -> bool:
    bad_patterns = [
        r"有什么值得注意",
        r"是什么[？?]?$",
        r"关于.+你知道",
        r"简介[？?]?$",
        r"谁是.+[？?]?$",
    ]
    return any(re.search(pattern, title) for pattern in bad_patterns)


def similar_to_existing(title: str, existing_titles: list[str]) -> bool:
    target = normalized(title)
    for other in existing_titles:
        ratio = SequenceMatcher(None, target, normalized(other)).ratio()
        if ratio >= 0.78:
            return True
    return False


def load_cards() -> dict:
    if not CARDS_PATH.exists():
        return {"version": 1, "updated_at": None, "cards": []}
    return json.loads(CARDS_PATH.read_text(encoding="utf-8"))


def build_new_cards(
    reviews: list[ReviewItem],
    candidate_map: dict[str, Candidate],
    existing_cards: list[dict],
) -> list[dict]:
    existing_titles = [item.get("title", "") for item in existing_cards]
    existing_urls = {item.get("source_url", "") for item in existing_cards}
    new_cards: list[dict] = []

    for item in sorted(reviews, key=lambda value: value.quality_score, reverse=True):
        if not item.approved or item.quality_score < 82:
            continue
        candidate = candidate_map.get(item.source_id)
        if not candidate:
            continue

        title = normalize_space(item.title)
        if not 8 <= len(title) <= 36 or title_is_bad(title):
            continue
        if item.category not in CATEGORIES:
            continue
        if not evidence_supported(item.evidence, candidate.excerpt):
            continue
        if candidate.source_url in existing_urls:
            continue
        if similar_to_existing(title, existing_titles):
            continue

        digest = hashlib.sha256(
            f"{title}|{candidate.source_url}".encode("utf-8")
        ).hexdigest()[:16]

        card = {
            "id": f"auto-{digest}",
            "title": title,
            "lead": normalize_space(item.lead),
            "explanation": normalize_space(item.explanation),
            "angle": normalize_space(item.angle),
            "category": item.category,
            "source_name": candidate.source_name,
            "source_url": candidate.source_url,
            "evidence": normalize_space(item.evidence),
            "created_at": utc_now().date().isoformat(),
            "source_type": f"AI审核 · {MODEL}",
        }
        new_cards.append(card)
        existing_titles.append(title)
        existing_urls.add(candidate.source_url)

        if len(new_cards) >= MAX_NEW_CARDS:
            break

    return new_cards


def main() -> int:
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is missing.", file=sys.stderr)
        return 2

    candidates = fetch_candidates()
    if not candidates:
        print("No source candidates were fetched.", file=sys.stderr)
        return 1

    print(f"[pipeline] total candidates: {len(candidates)}")
    client = OpenAI()
    proposals = extract_proposals(client, candidates)
    print(f"[pipeline] extracted proposals: {len(proposals)}")

    candidate_map = {item.source_id: item for item in candidates}
    reviews = review_proposals(client, proposals, candidate_map)
    print(f"[pipeline] reviewed items: {len(reviews)}")

    payload = load_cards()
    existing_cards = payload.get("cards", [])
    new_cards = build_new_cards(reviews, candidate_map, existing_cards)

    if not new_cards:
        print("[pipeline] no card passed the quality gate")
        return 0

    merged = new_cards + existing_cards
    payload["version"] = 1
    payload["updated_at"] = utc_now().isoformat().replace("+00:00", "Z")
    payload["cards"] = merged[:300]
    CARDS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[pipeline] added {len(new_cards)} cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
