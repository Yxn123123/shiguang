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
CANDIDATE_POOL_PATH = ROOT / "data" / "candidate_pool.json"
POOL_STATUS_PATH = ROOT / "site" / "data" / "pool_status.json"

MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
RUN_SOURCE = os.getenv("RUN_SOURCE", "scheduled").strip() or "scheduled"
TARGET_NEW_CARDS_ENV = os.getenv("TARGET_NEW_CARDS", "").strip()
MAX_CANDIDATES_PER_ROUND = int(
    os.getenv("MAX_CANDIDATES_PER_ROUND", "24")
)
MAX_PROCESS_ROUNDS = int(os.getenv("MAX_PROCESS_ROUNDS", "3"))
POOL_MIN_PENDING = int(os.getenv("POOL_MIN_PENDING", "160"))
MAX_POOL_SIZE = int(os.getenv("MAX_POOL_SIZE", "1000"))
MAX_CARDS_STORED = int(os.getenv("MAX_CARDS_STORED", "2000"))
HARVEST_TARGET_PENDING = int(
    os.getenv("HARVEST_TARGET_PENDING", "240")
)
HARVEST_MAX_PASSES = int(os.getenv("HARVEST_MAX_PASSES", "4"))
PIPELINE_MODE = os.getenv("PIPELINE_MODE", "generate").strip() or "generate"
SEEN_TTL_DAYS = int(os.getenv("SEEN_TTL_DAYS", "30"))

USER_AGENT = "ShiguangKnowledgePWA/2.2 (personal educational project)"
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


def wikipedia_extracts(
    titles: list[str],
    language: str = "zh",
) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for title_batch in batch(titles, 18):
        payload = request_json(
            f"https://{language}.wikipedia.org/w/api.php",
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
            rvlimit=200,
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
            for revision in revisions[3::8][:24]
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

    pages = wikipedia_extracts([title for _, title in raw[:180]], "zh")
    candidates: list[Candidate] = []

    for question_text, article_title in raw[:180]:
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



def fetch_english_recent_additions() -> list[Candidate]:
    """Read a larger, high-yield DYK page from English Wikipedia."""
    try:
        payload = request_json(
            "https://en.wikipedia.org/w/api.php",
            action="parse",
            page="Wikipedia:Recent additions",
            prop="text",
            format="json",
            origin="*",
        )
    except requests.RequestException:
        return []

    html = payload.get("parse", {}).get("text", {}).get("*", "")
    raw = parse_dyk_html(html)

    seen_titles: set[str] = set()
    unique_raw: list[tuple[str, str]] = []
    for question_text, article_title in raw:
        if article_title in seen_titles:
            continue
        seen_titles.add(article_title)
        unique_raw.append((question_text, article_title))

    # Shuffle before truncating so successive harvests are not tied to page order.
    random.SystemRandom().shuffle(unique_raw)
    unique_raw = unique_raw[:180]

    pages = wikipedia_extracts(
        [title for _, title in unique_raw],
        "en",
    )
    candidates: list[Candidate] = []

    for question_text, article_title in unique_raw:
        page = pages.get(article_title)
        if not page:
            page = next(
                (
                    value
                    for key, value in pages.items()
                    if canonical_text(key) == canonical_text(article_title)
                ),
                {},
            )

        extract = normalize_space(page.get("extract", ""))
        if len(extract) < 140:
            continue

        page_id = page.get("pageid", article_title)
        full_url = page.get(
            "fullurl",
            "https://en.wikipedia.org/wiki/"
            + quote(article_title.replace(" ", "_")),
        )

        candidates.append(
            Candidate(
                source_id=f"en-dyk:{page_id}",
                source_name=f"English Wikipedia DYK：{article_title}",
                source_url=full_url,
                title=question_text,
                excerpt=f"DYK question: {question_text}\nArticle introduction: {extract}",
                category_hint="综合",
                source_rank=0,
            )
        )

    return candidates


def fetch_wikipedia_category_samples() -> list[Candidate]:
    """Sample article introductions from categories that tend to contain mechanisms."""
    categories = [
        ("Biological phenomena", "生物"),
        ("Physical phenomena", "科学"),
        ("Optical phenomena", "科学"),
        ("Food science", "生活"),
        ("Materials science", "科技"),
        ("History of technology", "历史"),
        ("Art techniques", "艺术"),
    ]
    rng = random.SystemRandom()
    rng.shuffle(categories)
    candidates: list[Candidate] = []

    for category_name, category_hint in categories:
        try:
            payload = request_json(
                "https://en.wikipedia.org/w/api.php",
                action="query",
                generator="categorymembers",
                gcmtitle=f"Category:{category_name}",
                gcmtype="page",
                gcmlimit=40,
                prop="extracts|info",
                exintro=1,
                explaintext=1,
                exchars=1400,
                inprop="url",
                format="json",
                origin="*",
            )
        except requests.RequestException:
            continue

        pages = list(payload.get("query", {}).get("pages", {}).values())
        rng.shuffle(pages)

        for page in pages[:8]:
            title = normalize_space(page.get("title", ""))
            extract = normalize_space(page.get("extract", ""))
            if not title or len(extract) < 150:
                continue

            candidates.append(
                Candidate(
                    source_id=f"en-category:{page.get('pageid', title)}",
                    source_name=f"English Wikipedia：{title}",
                    source_url=page.get(
                        "fullurl",
                        "https://en.wikipedia.org/wiki/"
                        + quote(title.replace(" ", "_")),
                    ),
                    title=title,
                    excerpt=extract,
                    category_hint=category_hint,
                    source_rank=2,
                )
            )

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



def random_title_is_weak(title: str) -> bool:
    lowered = title.lower()
    weak_prefixes = (
        "list of ",
        "outline of ",
        "index of ",
        "timeline of ",
        "glossary of ",
    )
    if lowered.startswith(weak_prefixes):
        return True
    if re.fullmatch(r"\d{3,4}", title):
        return True
    return len(title) < 3 or len(title) > 90


def fetch_wikipedia_random_samples(
    language: str,
    request_count: int,
    source_rank: int = 4,
) -> list[Candidate]:
    """Fetch random article introductions to keep the raw reserve stocked."""
    candidates: list[Candidate] = []
    requests_needed = max(1, (request_count + 19) // 20)

    for _ in range(requests_needed):
        try:
            payload = request_json(
                f"https://{language}.wikipedia.org/w/api.php",
                action="query",
                generator="random",
                grnnamespace=0,
                grnlimit=20,
                prop="extracts|info|pageprops",
                exintro=1,
                explaintext=1,
                exchars=1500,
                inprop="url",
                format="json",
                origin="*",
            )
        except requests.RequestException:
            continue

        for page in payload.get("query", {}).get("pages", {}).values():
            title = normalize_space(page.get("title", ""))
            extract = normalize_space(page.get("extract", ""))
            pageprops = page.get("pageprops", {})

            if "disambiguation" in pageprops:
                continue
            if random_title_is_weak(title):
                continue
            if not 180 <= len(extract) <= 1500:
                continue

            page_id = page.get("pageid", title)
            full_url = page.get(
                "fullurl",
                f"https://{language}.wikipedia.org/wiki/"
                + quote(title.replace(" ", "_")),
            )
            candidates.append(
                Candidate(
                    source_id=f"wiki-random:{language}:{page_id}",
                    source_name=f"{language.upper()} Wikipedia：{title}",
                    source_url=full_url,
                    title=title,
                    excerpt=extract,
                    category_hint="综合",
                    source_rank=source_rank,
                )
            )

    unique = {candidate.source_id: candidate for candidate in candidates}
    result = list(unique.values())
    random.SystemRandom().shuffle(result)
    return result[:request_count]

def fetch_candidates(harvest_pass: int = 0) -> list[Candidate]:
    providers = [
        ("Chinese Wikipedia DYK history", fetch_wikipedia_dyk_history),
        ("English Wikipedia recent additions", fetch_english_recent_additions),
        ("Wikipedia category samples", fetch_wikipedia_category_samples),
        ("NASA APOD", fetch_nasa_apod),
        ("The Met", fetch_met_objects),
        ("Wikimedia On This Day", fetch_on_this_day),
    ]
    collected: list[Candidate] = []

    # Expensive/static sources are read on the first pass only. Later passes
    # rely on random article introductions to reach a genuine reserve level.
    if harvest_pass == 0:
        for name, provider in providers:
            try:
                items = provider()
                print(f"[source] {name}: {len(items)} candidates")
                collected.extend(items)
            except Exception as exc:
                print(f"[source] {name} failed: {exc}", file=sys.stderr)

    random_requests = [
        ("English Wikipedia random", "en", 90, 4),
        ("Chinese Wikipedia random", "zh", 50, 4),
    ]
    for name, language, count, rank in random_requests:
        try:
            items = fetch_wikipedia_random_samples(
                language,
                count,
                rank,
            )
            print(
                f"[source] {name} pass={harvest_pass + 1}: "
                f"{len(items)} candidates"
            )
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



def candidate_to_record(candidate: Candidate) -> dict:
    record = asdict(candidate)
    record["added_at"] = iso_now()
    return record


def candidate_from_record(record: dict) -> Candidate | None:
    try:
        return Candidate(
            source_id=str(record["source_id"]),
            source_name=str(record["source_name"]),
            source_url=str(record["source_url"]),
            title=str(record["title"]),
            excerpt=str(record["excerpt"]),
            category_hint=str(record.get("category_hint", "综合")),
            source_rank=int(record.get("source_rank", 9)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def load_candidate_pool() -> dict:
    if not CANDIDATE_POOL_PATH.exists():
        return {"version": 1, "updated_at": None, "candidates": []}
    try:
        payload = json.loads(
            CANDIDATE_POOL_PATH.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "updated_at": None, "candidates": []}

    if not isinstance(payload.get("candidates"), list):
        payload["candidates"] = []
    return payload


def save_candidate_pool(payload: dict) -> None:
    CANDIDATE_POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload["version"] = 1
    payload["updated_at"] = iso_now()
    payload["candidates"] = payload.get("candidates", [])[:MAX_POOL_SIZE]
    CANDIDATE_POOL_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_pool_status() -> dict:
    if not POOL_STATUS_PATH.exists():
        return {
            "version": 1,
            "updated_at": None,
            "approved_cards": 0,
            "pending_candidates": 0,
            "last_run": None,
            "recent_runs": [],
        }

    try:
        payload = json.loads(
            POOL_STATUS_PATH.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        payload = {}

    payload.setdefault("recent_runs", [])
    return payload


def save_pool_status(
    *,
    cards_count: int,
    pending_count: int,
    run_stats: dict,
) -> None:
    POOL_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = load_pool_status()
    recent_runs = [
        item
        for item in payload.get("recent_runs", [])
        if item.get("github_run_id") != run_stats.get("github_run_id")
    ]
    recent_runs.insert(0, run_stats)

    payload.update(
        {
            "version": 1,
            "updated_at": iso_now(),
            "approved_cards": cards_count,
            "pending_candidates": pending_count,
            "last_run": run_stats,
            "recent_runs": recent_runs[:20],
        }
    )
    POOL_STATUS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def merge_candidates_into_pool(
    pool_payload: dict,
    fetched: list[Candidate],
    cards: list[dict],
    state: dict,
) -> tuple[int, dict[str, int]]:
    existing_source_ids = {
        str(card.get("source_id", ""))
        for card in cards
        if card.get("source_id")
    }
    existing_urls = {
        normalize_space(card.get("source_url", ""))
        for card in cards
        if card.get("source_url")
    }

    records = pool_payload.get("candidates", [])
    pool_ids = {
        str(record.get("source_id", ""))
        for record in records
        if record.get("source_id")
    }

    counts = {
        "already_in_pool": 0,
        "already_published": 0,
        "seen_recently": 0,
        "low_information": 0,
        "pool_limit": 0,
    }
    added = 0

    # High-ranked sources are kept first if the pending pool reaches its cap.
    fetched = sorted(
        fetched,
        key=lambda item: (item.source_rank, random.random()),
    )

    for candidate in fetched:
        if candidate.source_id in pool_ids:
            counts["already_in_pool"] += 1
            continue
        if (
            candidate.source_id in existing_source_ids
            or normalize_space(candidate.source_url) in existing_urls
        ):
            counts["already_published"] += 1
            continue
        if seen_recently(candidate.source_id, state):
            counts["seen_recently"] += 1
            continue
        if candidate_is_low_information(candidate):
            counts["low_information"] += 1
            continue
        if len(records) >= MAX_POOL_SIZE:
            counts["pool_limit"] += 1
            continue

        records.append(candidate_to_record(candidate))
        pool_ids.add(candidate.source_id)
        added += 1

    records.sort(
        key=lambda record: (
            int(record.get("source_rank", 9)),
            str(record.get("added_at", "")),
        )
    )
    pool_payload["candidates"] = records[:MAX_POOL_SIZE]
    return added, counts


def select_pool_batch(
    pool_payload: dict,
    cards: list[dict],
    state: dict,
    limit: int,
) -> tuple[list[Candidate], list[str], dict[str, int]]:
    records = pool_payload.get("candidates", [])
    existing_urls = {
        normalize_space(card.get("source_url", ""))
        for card in cards
        if card.get("source_url")
    }
    selected: list[Candidate] = []
    selected_ids: list[str] = []
    counts = {
        "invalid_record": 0,
        "duplicate_source": 0,
        "seen_recently": 0,
        "low_information": 0,
    }

    # Shuffle within source-rank bands so each run consumes a different mixture.
    records.sort(
        key=lambda record: (
            int(record.get("source_rank", 9)),
            random.random(),
        )
    )

    for record in records:
        candidate = candidate_from_record(record)
        if not candidate:
            counts["invalid_record"] += 1
            continue
        if normalize_space(candidate.source_url) in existing_urls:
            counts["duplicate_source"] += 1
            continue
        if seen_recently(candidate.source_id, state):
            counts["seen_recently"] += 1
            continue
        if candidate_is_low_information(candidate):
            counts["low_information"] += 1
            continue

        selected.append(candidate)
        selected_ids.append(candidate.source_id)
        if len(selected) >= limit:
            break

    return selected, selected_ids, counts


def remove_candidates_from_pool(
    pool_payload: dict,
    source_ids: set[str],
) -> None:
    pool_payload["candidates"] = [
        record
        for record in pool_payload.get("candidates", [])
        if str(record.get("source_id", "")) not in source_ids
    ]


def resolved_target_new_cards() -> int:
    if TARGET_NEW_CARDS_ENV:
        try:
            return max(1, min(40, int(TARGET_NEW_CARDS_ENV)))
        except ValueError:
            pass

    if RUN_SOURCE in {"web", "manual", "workflow_dispatch"}:
        return 12
    return 5


def resolved_max_rounds() -> int:
    if RUN_SOURCE in {"web", "manual", "workflow_dispatch"}:
        return MAX_PROCESS_ROUNDS
    return 1


def source_family(source_id: str) -> str:
    if source_id.startswith("wiki-dyk:"):
        return "中文维基你知道吗"
    if source_id.startswith("en-dyk:"):
        return "英文维基你知道吗"
    if source_id.startswith("en-category:"):
        return "英文维基分类"
    if source_id.startswith("nasa-apod:"):
        return "NASA APOD"
    if source_id.startswith("met:"):
        return "大都会博物馆"
    if source_id.startswith("onthisday:"):
        return "历史上的今天"
    return "其他"


def save_harvest_status(
    *,
    cards_count: int,
    pending_count: int,
    stats: dict,
) -> None:
    POOL_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = load_pool_status()
    payload.update(
        {
            "version": 1,
            "updated_at": iso_now(),
            "approved_cards": cards_count,
            "pending_candidates": pending_count,
            "last_harvest": stats,
        }
    )
    POOL_STATUS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def harvest_until_reserve(
    pool_payload: dict,
    cards: list[dict],
    state: dict,
) -> tuple[int, int, dict[str, int], int]:
    total_fetched = 0
    total_added = 0
    combined_counts: dict[str, int] = {}
    passes_used = 0

    while (
        len(pool_payload.get("candidates", [])) < HARVEST_TARGET_PENDING
        and passes_used < HARVEST_MAX_PASSES
    ):
        fetched = fetch_candidates(passes_used)
        passes_used += 1
        total_fetched += len(fetched)

        added, counts = merge_candidates_into_pool(
            pool_payload,
            fetched,
            cards,
            state,
        )
        total_added += added
        merge_counts(combined_counts, counts)

        print(
            f"[harvest {passes_used}] fetched={len(fetched)} "
            f"added={added} "
            f"pending={len(pool_payload.get('candidates', []))}"
        )

        # Avoid looping pointlessly if a pass produced no new material.
        if not fetched or added == 0:
            break

    return total_fetched, total_added, combined_counts, passes_used

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
    limit: int,
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

        if len(new_cards) >= limit:
            break

    return new_cards, counts, reasons


def print_counts(label: str, counts: dict[str, int]) -> None:
    useful = ", ".join(
        f"{key}={value}"
        for key, value in counts.items()
        if value
    )
    print(f"[filter:{label}] {useful or 'none'}")


def merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value)


def main() -> int:
    started_at = utc_now()
    target_new_cards = resolved_target_new_cards()
    max_rounds = resolved_max_rounds()
    github_run_id = os.getenv("GITHUB_RUN_ID", "")

    payload = load_cards()
    existing_cards = payload.get("cards", [])
    state = load_state()
    pool_payload = load_candidate_pool()

    pool_before = len(pool_payload.get("candidates", []))
    harvested = 0
    new_candidates = 0
    harvest_passes = 0
    harvest_filter_counts: dict[str, int] = {}

    if pool_before < POOL_MIN_PENDING or PIPELINE_MODE == "harvest":
        (
            harvested,
            new_candidates,
            harvest_filter_counts,
            harvest_passes,
        ) = harvest_until_reserve(
            pool_payload,
            existing_cards,
            state,
        )
        print_counts("harvest", harvest_filter_counts)
        print(
            f"[pool] reserve after harvest: "
            f"{len(pool_payload.get('candidates', []))}"
        )
    else:
        print(
            f"[pool] pending candidates already sufficient: "
            f"{pool_before} >= {POOL_MIN_PENDING}"
        )

    if PIPELINE_MODE == "harvest":
        save_candidate_pool(pool_payload)
        save_state(state)
        harvest_stats = {
            "github_run_id": github_run_id,
            "source": RUN_SOURCE,
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "finished_at": iso_now(),
            "passes": harvest_passes,
            "fetched": harvested,
            "added": new_candidates,
            "pool_before": pool_before,
            "pool_after": len(pool_payload.get("candidates", [])),
        }
        save_harvest_status(
            cards_count=len(existing_cards),
            pending_count=len(pool_payload.get("candidates", [])),
            stats=harvest_stats,
        )
        print(
            f"[harvest] finished with "
            f"{len(pool_payload.get('candidates', []))} pending candidates"
        )
        return 0

    if not os.getenv("OPENAI_API_KEY"):
        run_stats = {
            "github_run_id": github_run_id,
            "source": RUN_SOURCE,
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "finished_at": iso_now(),
            "target_new_cards": target_new_cards,
            "harvested": harvested,
            "new_candidates": new_candidates,
            "harvest_passes": harvest_passes,
            "pool_before": pool_before,
            "pool_after": len(pool_payload.get("candidates", [])),
            "processed": 0,
            "proposals": 0,
            "reviewed": 0,
            "added": 0,
            "pass_rate": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "message": "OPENAI_API_KEY is missing",
        }
        save_candidate_pool(pool_payload)
        save_state(state)
        save_pool_status(
            cards_count=len(existing_cards),
            pending_count=len(pool_payload.get("candidates", [])),
            run_stats=run_stats,
        )
        print("OPENAI_API_KEY is missing.", file=sys.stderr)
        return 2

    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url or None,
    )
    if base_url:
        print(f"[pipeline] using custom base URL: {base_url}")

    all_new_cards: list[dict] = []
    processed_total = 0
    proposals_total = 0
    reviewed_total = 0
    rounds_used = 0

    source_stats: dict[str, dict[str, int]] = {}
    before_ai_counts: dict[str, int] = {}
    before_review_counts: dict[str, int] = {}
    final_filter_counts: dict[str, int] = {}

    for round_index in range(max_rounds):
        remaining = target_new_cards - len(all_new_cards)
        if remaining <= 0:
            break

        cards_for_dedup = all_new_cards + existing_cards
        selected, selected_ids, pool_filter_counts = select_pool_batch(
            pool_payload,
            cards_for_dedup,
            state,
            MAX_CANDIDATES_PER_ROUND,
        )
        merge_counts(before_ai_counts, pool_filter_counts)

        if not selected:
            print("[pipeline] candidate pool has no usable pending item")
            break

        rounds_used += 1
        processed_total += len(selected)
        print(
            f"[round {round_index + 1}] candidates sent to AI: "
            f"{len(selected)}"
        )

        for candidate in selected:
            family = source_family(candidate.source_id)
            source_stats.setdefault(
                family,
                {"processed": 0, "accepted": 0},
            )
            source_stats[family]["processed"] += 1

        proposals = extract_proposals(client, selected)
        proposals_total += len(proposals)
        print(
            f"[round {round_index + 1}] extracted proposals: "
            f"{len(proposals)}"
        )

        candidate_map = {item.source_id: item for item in selected}
        prequalified, proposal_filter_counts = prequalify_proposals(
            proposals,
            candidate_map,
            cards_for_dedup,
        )
        merge_counts(before_review_counts, proposal_filter_counts)
        print(
            f"[round {round_index + 1}] proposals sent to review: "
            f"{len(prequalified)}"
        )

        reviews: list[ReviewItem] = []
        if prequalified:
            reviews = review_proposals(
                client,
                prequalified,
                candidate_map,
            )
        reviewed_total += len(reviews)

        new_cards, round_final_counts, reasons = build_new_cards(
            reviews,
            candidate_map,
            cards_for_dedup,
            remaining,
        )
        merge_counts(final_filter_counts, round_final_counts)

        all_new_cards.extend(new_cards)
        accepted_ids = {card["source_id"] for card in new_cards}
        proposal_ids = {proposal.source_id for proposal in proposals}
        reviewed_ids = {item.source_id for item in reviews}

        for candidate in selected:
            family = source_family(candidate.source_id)
            if candidate.source_id in accepted_ids:
                source_stats[family]["accepted"] += 1
                mark_seen(state, candidate, "accepted")
            elif candidate.source_id in reasons:
                mark_seen(
                    state,
                    candidate,
                    "rejected",
                    reasons[candidate.source_id],
                )
            elif candidate.source_id in reviewed_ids:
                mark_seen(
                    state,
                    candidate,
                    "reviewed_not_saved",
                )
            elif candidate.source_id in proposal_ids:
                mark_seen(
                    state,
                    candidate,
                    "filtered_before_review",
                )
            else:
                mark_seen(state, candidate, "no_proposal")

        # Every selected item has now consumed its AI opportunity. Remove it from
        # the raw candidate queue so the next click works on genuinely new material.
        remove_candidates_from_pool(
            pool_payload,
            set(selected_ids),
        )

        print(
            f"[round {round_index + 1}] added cards: "
            f"{len(new_cards)}"
        )

    print_counts("before_ai", before_ai_counts)
    print_counts("before_review", before_review_counts)
    print_counts("final", final_filter_counts)

    if all_new_cards:
        merged = all_new_cards + existing_cards
        payload["version"] = 1
        payload["updated_at"] = iso_now()
        payload["cards"] = merged[:MAX_CARDS_STORED]
        CARDS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    save_candidate_pool(pool_payload)
    save_state(state)

    finished_at = utc_now()
    duration_seconds = round(
        (finished_at - started_at).total_seconds(),
        2,
    )
    pass_rate = (
        round(len(all_new_cards) / processed_total * 100, 1)
        if processed_total
        else 0
    )

    run_stats = {
        "github_run_id": github_run_id,
        "source": RUN_SOURCE,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": finished_at.isoformat().replace("+00:00", "Z"),
        "duration_seconds": duration_seconds,
        "target_new_cards": target_new_cards,
        "rounds": rounds_used,
        "harvested": harvested,
        "new_candidates": new_candidates,
        "harvest_passes": harvest_passes,
        "pool_before": pool_before,
        "pool_after": len(pool_payload.get("candidates", [])),
        "processed": processed_total,
        "proposals": proposals_total,
        "reviewed": reviewed_total,
        "added": len(all_new_cards),
        "pass_rate": pass_rate,
        "input_tokens": USAGE["input_tokens"],
        "output_tokens": USAGE["output_tokens"],
        "filters": {
            "harvest": harvest_filter_counts,
            "before_ai": before_ai_counts,
            "before_review": before_review_counts,
            "final": final_filter_counts,
        },
        "source_stats": source_stats,
    }

    current_cards_count = (
        len(payload.get("cards", []))
        if all_new_cards
        else len(existing_cards)
    )
    save_pool_status(
        cards_count=current_cards_count,
        pending_count=len(pool_payload.get("candidates", [])),
        run_stats=run_stats,
    )

    print(
        "[usage] "
        f"input_tokens={USAGE['input_tokens']} "
        f"output_tokens={USAGE['output_tokens']}"
    )
    print(
        "[pool] "
        f"approved_cards={current_cards_count} "
        f"pending_candidates={len(pool_payload.get('candidates', []))} "
        f"added={len(all_new_cards)} "
        f"pass_rate={pass_rate}%"
    )

    if not all_new_cards:
        print("[pipeline] no card passed this replenishment run")
        return 0

    print(f"[pipeline] added {len(all_new_cards)} cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
