#!/usr/bin/env python3
"""Generate reviewed Chinese knowledge cards with a high-yield, low-waste pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "site" / "data" / "cards.json"
STATE_PATH = ROOT / "data" / "generation_state.json"

MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
MAX_NEW_CARDS = int(os.getenv("MAX_NEW_CARDS", "8"))
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "24"))
SEEN_TTL_DAYS = int(os.getenv("SEEN_TTL_DAYS", "7"))

USER_AGENT = "ShiguangKnowledgePWA/1.4 (personal educational project)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
TIMEOUT = 22

CATEGORIES = ["生物", "科学", "历史", "艺术", "科技", "生活", "综合"]
MIN_REVIEW_SCORE = 78

USAGE = {"input_tokens": 0, "output_tokens": 0}


@dataclass
class Candidate:
    source_id: str
    source_name: str
    source_url: str
    title: str
    excerpt: str
    category_hint: str = "综合"
    source_rank: int = 9


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


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def request_json(url: str, **params) -> dict:
    response = SESSION.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def canonical_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(char for char in text if char.isalnum())


def batch(items: list, size: int) -> Iterable[list]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def record_usage(response) -> None:
    usage = getattr(response, "usage", None)
    if not usage:
        return
    USAGE["input_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
    USAGE["output_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)


def load_cards() -> dict:
    if not CARDS_PATH.exists():
        return {"version": 1, "updated_at": None, "cards": []}
    return json.loads(CARDS_PATH.read_text(encoding="utf-8"))


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": 1, "seen": {}}
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload.get("seen"), dict):
            payload["seen"] = {}
        return payload
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "seen": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cutoff = utc_now() - timedelta(days=45)
    pruned = {}

    for source_id, info in state.get("seen", {}).items():
        try:
            last_seen = datetime.fromisoformat(
                str(info.get("last_seen", "")).replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if last_seen >= cutoff:
            pruned[source_id] = info

    state["version"] = 1
    state["updated_at"] = iso_now()
    state["seen"] = pruned
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def seen_recently(source_id: str, state: dict) -> bool:
    info = state.get("seen", {}).get(source_id)
    if not info:
        return False
    try:
        last_seen = datetime.fromisoformat(
            str(info.get("last_seen", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return False
    return last_seen >= utc_now() - timedelta(days=SEEN_TTL_DAYS)


def mark_seen(
    state: dict,
    candidate: Candidate,
    outcome: str,
    reason: str = "",
) -> None:
    state.setdefault("seen", {})[candidate.source_id] = {
        "last_seen": iso_now(),
        "source_url": candidate.source_url,
        "outcome": outcome,
        "reason": normalize_space(reason)[:180],
    }


def wikipedia_extracts(titles: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for title_batch in batch(titles, 18):
        payload = request_json(
            "https://zh.wikipedia.org/w/api.php",
            action="query",
            prop="extracts|info",
            exintro=1,
            explaintext=1,
            exchars=1500,
            inprop="url",
            redirects=1,
            titles="|".join(title_batch),
            format="json",
            origin="*",
        )
        for page in payload.get("query", {}).get("pages", {}).values():
            if page.get("title"):
                result[page["title"]] = page
    return result


def parse_dyk_html(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[tuple[str, str]] = []

    for item in soup.select("li"):
        question_text = normalize_space(item.get_text(" ", strip=True))
        if not 12 <= len(question_text) <= 130:
            continue
        if not question_text.endswith(("？", "?")):
            continue

        anchors = []
        for anchor in item.select("a"):
            title = normalize_space(anchor.get("title", ""))
            href = anchor.get("href", "")
            if not title or ":" in title:
                continue
            if not (href.startswith("/wiki/") or href.startswith("./")):
                continue
            anchors.append(anchor)

        if not anchors:
            continue

        preferred = (
            item.select_one("b a, strong a")
            or anchors[0]
        )
        article_title = normalize_space(preferred.get("title", ""))
        if article_title and ":" not in article_title:
            entries.append((question_text, article_title))

    return entries


def fetch_wikipedia_dyk_history() -> list[Candidate]:
    revision_ids: list[int | None] = [None]

    try:
        revision_payload = request_json(
            "https://zh.wikipedia.org/w/api.php",
            action="query",
            prop="revisions",
            titles="Template:Dyk",
            rvprop="ids|timestamp",
            rvlimit=30,
            format="json",
            origin="*",
        )
        pages = revision_payload.get("query", {}).get("pages", {}).values()
        revisions = []
        for page in pages:
            revisions.extend(page.get("revisions", []))

        # Sampling spaced revisions gives older, more varied DYK questions,
        # instead of paying AI to reread almost identical revisions.
        revision_ids.extend(
            revision.get("revid")
            for revision in revisions[3::4][:7]
            if revision.get("revid")
        )
    except requests.RequestException:
        pass

    raw: list[tuple[str, str]] = []
    seen_titles: set[str] = set()

    for revision_id in revision_ids:
        try:
            params = {
                "action": "parse",
                "prop": "text",
                "format": "json",
                "origin": "*",
            }
            if revision_id:
                params["oldid"] = revision_id
            else:
                params["page"] = "Template:Dyk"

            payload = request_json(
                "https://zh.wikipedia.org/w/api.php",
                **params,
            )
            html = payload.get("parse", {}).get("text", {}).get("*", "")
        except requests.RequestException:
            continue

        for question_text, article_title in parse_dyk_html(html):
            if article_title in seen_titles:
                continue
            seen_titles.add(article_title)
            raw.append((question_text, article_title))

    pages = wikipedia_extracts([title for _, title in raw[:70]])
    candidates: list[Candidate] = []

    for question_text, article_title in raw[:70]:
        page = pages.get(article_title)
        if not page:
            # Redirect-normalized title may differ; try a loose lookup.
            page = next(
                (
                    value
                    for key, value in pages.items()
                    if canonical_text(key) == canonical_text(article_title)
                ),
                {},
            )

        extract = normalize_space(page.get("extract", ""))
        if len(extract) < 120:
            continue

        page_id = page.get("pageid", article_title)
        full_url = page.get(
            "fullurl",
            "https://zh.wikipedia.org/wiki/"
            + quote(article_title.replace(" ", "_")),
        )

        candidates.append(
            Candidate(
                source_id=f"wiki-dyk:{page_id}",
                source_name=f"中文维基百科：{article_title}",
                source_url=full_url,
                title=question_text,
                excerpt=f"栏目问题：{question_text}\n词条摘要：{extract}",
                category_hint="综合",
                source_rank=0,
            )
        )

    random.SystemRandom().shuffle(candidates)
    return candidates


def fetch_on_this_day() -> list[Candidate]:
    today = utc_now()
    month_day = f"{today.month:02d}/{today.day:02d}"
    payload = None
    language = "zh"

    for language_code in ("zh", "en"):
        url = (
            f"https://api.wikimedia.org/feed/v1/wikipedia/"
            f"{language_code}/onthisday/all/{month_day}"
        )
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
    events = payload.get("events", [])[:12]

    for index, event in enumerate(events):
        text = normalize_space(event.get("text", ""))
        year = event.get("year", "")
        pages = event.get("pages", [])
        page = pages[0] if pages else {}
        page_title = page.get("normalizedtitle") or page.get("title") or f"{year}年事件"
        extract = normalize_space(page.get("extract", ""))
        source_url = (
            page.get("content_urls", {})
            .get("desktop", {})
            .get("page", "")
        )

        if len(text) < 35 or len(extract) < 100:
            continue

        candidates.append(
            Candidate(
                source_id=f"onthisday:{language}:{month_day}:{year}:{index}",
                source_name=f"Wikimedia On This Day：{page_title}",
                source_url=source_url or f"https://{language}.wikipedia.org/wiki/{page_title}",
                title=f"{year}年的今天：{text}",
                excerpt=f"事件：{text}\n相关页面摘要：{extract}",
                category_hint="历史",
                source_rank=3,
            )
        )

    random.SystemRandom().shuffle(candidates)
    return candidates


def fetch_nasa_apod() -> list[Candidate]:
    api_key = os.getenv("NASA_API_KEY", "DEMO_KEY")
    try:
        payload = request_json(
            "https://api.nasa.gov/planetary/apod",
            api_key=api_key,
            count=4,
            thumbs=True,
        )
    except requests.RequestException:
        return []

    candidates: list[Candidate] = []

    for item in payload if isinstance(payload, list) else []:
        explanation = normalize_space(item.get("explanation", ""))
        title = normalize_space(item.get("title", ""))
        if len(explanation) < 160:
            continue

        candidates.append(
            Candidate(
                source_id=f"nasa-apod:{item.get('date', title)}",
                source_name=f"NASA Astronomy Picture of the Day：{title}",
                source_url=item.get("url") or item.get("hdurl") or "https://apod.nasa.gov/",
                title=title,
                excerpt=explanation,
                category_hint="科学",
                source_rank=1,
            )
        )

    return candidates


def fetch_met_objects() -> list[Candidate]:
    rng = random.SystemRandom()
    queries = ["ancient", "textile", "instrument", "ceramic", "painting", "jewelry"]
    rng.shuffle(queries)
    candidates: list[Candidate] = []

    for query in queries[:3]:
        try:
            search = request_json(
                "https://collectionapi.metmuseum.org/public/collection/v1/search",
                hasImages="true",
                q=query,
            )
            object_ids = search.get("objectIDs") or []
            if not object_ids:
                continue

            object_id = rng.choice(object_ids[: min(len(object_ids), 500)])
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
            "用途或对象": normalize_space(item.get("objectName", "")),
            "年代": normalize_space(item.get("objectDate", "")),
            "文化": normalize_space(item.get("culture", "")),
            "地区": normalize_space(item.get("geographyType", ""))
            + " "
            + normalize_space(item.get("country", "")),
            "材质": normalize_space(item.get("medium", "")),
            "尺寸": normalize_space(item.get("dimensions", "")),
            "类别": normalize_space(item.get("classification", "")),
            "说明": normalize_space(item.get("creditLine", "")),
        }
        excerpt = "\n".join(
            f"{key}：{normalize_space(value)}"
            for key, value in facts.items()
            if normalize_space(value)
        )

        if len(excerpt) < 90:
            continue

        candidates.append(
            Candidate(
                source_id=f"met:{object_id}",
                source_name=f"The Metropolitan Museum of Art：{title}",
                source_url=item.get(
                    "objectURL",
                    "https://www.metmuseum.org/art/collection",
                ),
                title=title,
                excerpt=excerpt,
                category_hint="艺术",
                source_rank=2,
            )
        )

    return candidates


def fetch_candidates() -> list[Candidate]:
    providers = [
        ("Wikipedia DYK history", fetch_wikipedia_dyk_history),
        ("NASA APOD", fetch_nasa_apod),
        ("The Met", fetch_met_objects),
        ("Wikimedia On This Day", fetch_on_this_day),
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


def candidate_is_low_information(candidate: Candidate) -> bool:
    text = normalize_space(candidate.excerpt)
    if len(text) < 90:
        return True

    # Free rejection before any token is spent.
    weak_labels = (
        "出生于",
        "逝世于",
        "是一名政治人物",
        "是一位政治人物",
        "是美国政治人物",
    )
    hits = sum(label in text for label in weak_labels)
    return hits >= 2 and candidate.source_rank >= 3


def select_candidates(
    candidates: list[Candidate],
    cards: list[dict],
    state: dict,
) -> tuple[list[Candidate], dict[str, int]]:
    existing_urls = {
        normalize_space(card.get("source_url", ""))
        for card in cards
        if card.get("source_url")
    }
    counts = {
        "duplicate_source": 0,
        "seen_recently": 0,
        "low_information": 0,
        "candidate_limit": 0,
    }
    selected: list[Candidate] = []

    candidates.sort(
        key=lambda item: (
            item.source_rank,
            random.random(),
        )
    )

    for candidate in candidates:
        if candidate.source_url in existing_urls:
            counts["duplicate_source"] += 1
            continue
        if seen_recently(candidate.source_id, state):
            counts["seen_recently"] += 1
            continue
        if candidate_is_low_information(candidate):
            counts["low_information"] += 1
            continue

        selected.append(candidate)
        if len(selected) >= MAX_CANDIDATES:
            counts["candidate_limit"] = max(0, len(candidates) - len(selected))
            break

    return selected, counts


EXTRACTION_INSTRUCTIONS = """
你是“拾光”知识编辑。请从候选材料里找适合普通人轻阅读的具体知识。

标准：
1. 只能依据候选材料，不能补充原文没有的事实。
2. 每个候选最多生成1条；实在普通、只有人物履历或参数时不输出。
3. 优先保留：反直觉的时间关系、日常现象背后的原因、设计细节、语言与历史误区、生物和材料的特殊机制。
4. 拒绝：纯人物生平、职位定义、国家概况、军舰参数、普通日期罗列、宽泛主题介绍。
5. 禁止模板标题：“X有什么值得注意的地方”“X是什么”“关于X你知道吗”。
6. 标题8—36个汉字，直接呈现具体事实或自然问题。
7. lead 15—60字；explanation 50—160字；angle 20—75字。
8. evidence 必须逐字复制候选材料中的一句或一段，不能翻译、改写或补标点，最多100字。
9. 分类只能是：生物、科学、历史、艺术、科技、生活、综合。
10. confidence 代表“这个事实是否值得做成卡片”，明确具体且有一点意外感即可达到75以上，不要求每条都惊世骇俗。
""".strip()


REVIEW_INSTRUCTIONS = """
你是“拾光”的内容主编。你的任务是校正和审核，不要为了显得严格而一律拒绝。

评分维度：
- 来源支持与事实准确：35分
- 具体程度：25分
- 阅读收获与意外感：20分
- 中文自然：10分
- 手机阅读长度：10分

硬性拒绝：
- 核心事实无法由来源支持；
- 只是人物、机构、职位或国家简介；
- 标题和正文说的不是同一件事；
- 是宽泛空话或模板化标题；
- evidence 不是来源中的原文。

处理原则：
- 可以在不增加事实的前提下修改标题、lead、解释和角度。
- evidence 有轻微截取问题时，直接从来源中重新复制一段原文。
- 质量达到78分且没有硬性问题，应 approved=true。
- 中等但清楚、有来源、读完能学到一点的知识，可以通过；不要求每条都“震撼”。
""".strip()


def compact_candidate(candidate: Candidate) -> dict:
    return {
        "source_id": candidate.source_id,
        "source_name": candidate.source_name,
        "source_url": candidate.source_url,
        "title": candidate.title,
        "excerpt": candidate.excerpt[:1250],
        "category_hint": candidate.category_hint,
    }


def extract_proposals(
    client: OpenAI,
    candidates: list[Candidate],
) -> list[Proposal]:
    proposals: list[Proposal] = []

    for candidate_batch in batch(candidates, 10):
        input_data = [compact_candidate(candidate) for candidate in candidate_batch]
        response = client.responses.parse(
            model=MODEL,
            reasoning={"effort": "low"},
            input=[
                {"role": "system", "content": EXTRACTION_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": "从这些候选中提取可用知识卡："
                    + json.dumps(
                        input_data,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            text_format=ProposalBatch,
        )
        record_usage(response)
        parsed = response.output_parsed
        if parsed:
            proposals.extend(parsed.cards)

    return proposals


def normalized(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


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
        if ratio >= 0.80:
            return True
    return False


def prequalify_proposals(
    proposals: list[Proposal],
    candidate_map: dict[str, Candidate],
    existing_cards: list[dict],
) -> tuple[list[Proposal], dict[str, int]]:
    existing_titles = [card.get("title", "") for card in existing_cards]
    counts = {
        "low_confidence": 0,
        "missing_candidate": 0,
        "bad_title": 0,
        "bad_category": 0,
        "similar_title": 0,
    }
    accepted: list[Proposal] = []

    for proposal in proposals:
        candidate = candidate_map.get(proposal.source_id)
        if not candidate:
            counts["missing_candidate"] += 1
            continue

        title = normalize_space(proposal.title)
        if proposal.confidence < 68:
            counts["low_confidence"] += 1
            continue
        if not 8 <= len(title) <= 38 or title_is_bad(title):
            counts["bad_title"] += 1
            continue
        if proposal.category not in CATEGORIES:
            counts["bad_category"] += 1
            continue
        if similar_to_existing(title, existing_titles):
            counts["similar_title"] += 1
            continue

        accepted.append(proposal)

    return accepted, counts


def review_proposals(
    client: OpenAI,
    proposals: list[Proposal],
    candidate_map: dict[str, Candidate],
) -> list[ReviewItem]:
    reviewed: list[ReviewItem] = []

    for proposal_batch in batch(proposals, 10):
        review_input = []
        for proposal in proposal_batch:
            candidate = candidate_map.get(proposal.source_id)
            if not candidate:
                continue
            review_input.append(
                {
                    "proposal": proposal.model_dump(),
                    "source": compact_candidate(candidate),
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
                    "content": "审核并修正以下知识卡："
                    + json.dumps(
                        review_input,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            text_format=ReviewBatch,
        )
        record_usage(response)
        parsed = response.output_parsed
        if parsed:
            reviewed.extend(parsed.items)

    return reviewed


def evidence_supported(evidence: str, source_text: str) -> bool:
    evidence_key = canonical_text(evidence)
    source_key = canonical_text(source_text)
    return len(evidence_key) >= 8 and evidence_key in source_key


def build_new_cards(
    reviews: list[ReviewItem],
    candidate_map: dict[str, Candidate],
    existing_cards: list[dict],
) -> tuple[list[dict], dict[str, int], dict[str, str]]:
    existing_titles = [item.get("title", "") for item in existing_cards]
    existing_urls = {
        item.get("source_url", "")
        for item in existing_cards
        if item.get("source_url")
    }

    counts = {
        "model_rejected": 0,
        "score_below_78": 0,
        "bad_title": 0,
        "bad_category": 0,
        "evidence_mismatch": 0,
        "duplicate_source": 0,
        "similar_title": 0,
    }
    reasons: dict[str, str] = {}
    new_cards: list[dict] = []

    for item in sorted(
        reviews,
        key=lambda value: value.quality_score,
        reverse=True,
    ):
        candidate = candidate_map.get(item.source_id)
        if not candidate:
            continue

        if not item.approved:
            counts["model_rejected"] += 1
            reasons[item.source_id] = item.rejection_reason or "AI主编未批准"
            print(
                f"[reject:model] {item.source_id} "
                f"score={item.quality_score} "
                f"reason={normalize_space(item.rejection_reason)}"
            )
            continue

        if item.quality_score < MIN_REVIEW_SCORE:
            counts["score_below_78"] += 1
            reasons[item.source_id] = f"质量分 {item.quality_score}"
            continue

        title = normalize_space(item.title)

        if not 8 <= len(title) <= 38 or title_is_bad(title):
            counts["bad_title"] += 1
            reasons[item.source_id] = "标题格式未通过"
            continue
        if item.category not in CATEGORIES:
            counts["bad_category"] += 1
            reasons[item.source_id] = "分类无效"
            continue
        if not evidence_supported(item.evidence, candidate.excerpt):
            counts["evidence_mismatch"] += 1
            reasons[item.source_id] = "证据未能在原文中匹配"
            continue
        if candidate.source_url in existing_urls:
            counts["duplicate_source"] += 1
            reasons[item.source_id] = "来源已经使用过"
            continue
        if similar_to_existing(title, existing_titles):
            counts["similar_title"] += 1
            reasons[item.source_id] = "与已有标题过于相似"
            continue

        digest = hashlib.sha256(
            f"{title}|{candidate.source_url}".encode("utf-8")
        ).hexdigest()[:16]

        card = {
            "id": f"auto-{digest}",
            "source_id": candidate.source_id,
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
            "quality_score": item.quality_score,
        }

        new_cards.append(card)
        existing_titles.append(title)
        existing_urls.add(candidate.source_url)
        reasons[item.source_id] = "accepted"

        if len(new_cards) >= MAX_NEW_CARDS:
            break

    return new_cards, counts, reasons


def print_counts(label: str, counts: dict[str, int]) -> None:
    useful = ", ".join(
        f"{key}={value}"
        for key, value in counts.items()
        if value
    )
    print(f"[filter:{label}] {useful or 'none'}")


def main() -> int:
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is missing.", file=sys.stderr)
        return 2

    payload = load_cards()
    existing_cards = payload.get("cards", [])
    state = load_state()

    candidates = fetch_candidates()
    print(f"[pipeline] fetched candidates: {len(candidates)}")

    selected, source_filter_counts = select_candidates(
        candidates,
        existing_cards,
        state,
    )
    print_counts("before_ai", source_filter_counts)
    print(f"[pipeline] candidates sent to AI: {len(selected)}")

    if not selected:
        print("[pipeline] no unseen candidate; no AI call was made")
        save_state(state)
        return 0

    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url or None,
    )
    if base_url:
        print(f"[pipeline] using custom base URL: {base_url}")

    proposals = extract_proposals(client, selected)
    print(f"[pipeline] extracted proposals: {len(proposals)}")

    candidate_map = {item.source_id: item for item in selected}
    prequalified, proposal_filter_counts = prequalify_proposals(
        proposals,
        candidate_map,
        existing_cards,
    )
    print_counts("before_review", proposal_filter_counts)
    print(f"[pipeline] proposals sent to review: {len(prequalified)}")

    reviews: list[ReviewItem] = []
    if prequalified:
        reviews = review_proposals(client, prequalified, candidate_map)
    print(f"[pipeline] reviewed items: {len(reviews)}")

    new_cards, final_counts, reasons = build_new_cards(
        reviews,
        candidate_map,
        existing_cards,
    )
    print_counts("final", final_counts)

    proposal_ids = {proposal.source_id for proposal in proposals}
    reviewed_ids = {item.source_id for item in reviews}
    accepted_ids = {card["source_id"] for card in new_cards}

    for candidate in selected:
        if candidate.source_id in accepted_ids:
            mark_seen(state, candidate, "accepted")
        elif candidate.source_id in reasons:
            mark_seen(
                state,
                candidate,
                "rejected",
                reasons[candidate.source_id],
            )
        elif candidate.source_id in reviewed_ids:
            mark_seen(state, candidate, "reviewed_not_saved")
        elif candidate.source_id in proposal_ids:
            mark_seen(state, candidate, "filtered_before_review")
        else:
            mark_seen(state, candidate, "no_proposal")

    save_state(state)

    print(
        "[usage] "
        f"input_tokens={USAGE['input_tokens']} "
        f"output_tokens={USAGE['output_tokens']}"
    )

    if not new_cards:
        print("[pipeline] no card passed this run")
        return 0

    merged = new_cards + existing_cards
    payload["version"] = 1
    payload["updated_at"] = iso_now()
    payload["cards"] = merged[:300]

    CARDS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"[pipeline] added {len(new_cards)} cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
