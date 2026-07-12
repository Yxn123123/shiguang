#!/usr/bin/env python3
"""Harvest public candidates or generate reviewed Chinese knowledge cards."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import requests

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[assignment]

try:
    from pydantic import BaseModel, Field
except ImportError:
    class BaseModel:  # type: ignore[no-redef]
        def model_dump(self) -> dict:
            return dict(getattr(self, "__dict__", {}))

    def Field(*args, **kwargs):  # type: ignore[no-redef]
        return None


ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "site" / "data" / "cards.json"
STATE_PATH = ROOT / "data" / "generation_state.json"
CANDIDATE_POOL_PATH = ROOT / "data" / "candidate_pool.json"
POOL_STATUS_PATH = ROOT / "site" / "data" / "pool_status.json"
HARVEST_REPORT_PATH = ROOT / "data" / "harvest_report.json"

MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
RUN_SOURCE = os.getenv("RUN_SOURCE", "scheduled").strip() or "scheduled"
PIPELINE_MODE = os.getenv("PIPELINE_MODE", "generate").strip() or "generate"
TARGET_NEW_CARDS_ENV = os.getenv("TARGET_NEW_CARDS", "").strip()
MAX_CANDIDATES_PER_ROUND = int(os.getenv("MAX_CANDIDATES_PER_ROUND", "24"))
MAX_PROCESS_ROUNDS = int(os.getenv("MAX_PROCESS_ROUNDS", "3"))
MAX_POOL_SIZE = int(os.getenv("MAX_POOL_SIZE", "1000"))
MAX_CARDS_STORED = int(os.getenv("MAX_CARDS_STORED", "2000"))
HARVEST_TARGET_PENDING = int(os.getenv("HARVEST_TARGET_PENDING", "500"))
HARVEST_REFILL_TRIGGER = int(os.getenv("HARVEST_REFILL_TRIGGER", "300"))
HARVEST_MAX_PASSES = int(os.getenv("HARVEST_MAX_PASSES", "4"))
HARVEST_MIN_ADDED = int(os.getenv("HARVEST_MIN_ADDED", "20"))
MIN_CANDIDATES_TO_GENERATE = int(os.getenv("MIN_CANDIDATES_TO_GENERATE", "20"))
SEEN_TTL_DAYS = int(os.getenv("SEEN_TTL_DAYS", "30"))

USER_AGENT = (
    "ShiguangKnowledgePWA/2.5 "
    "(+https://github.com/Yxn123123/shiguang; educational knowledge cards)"
)
TIMEOUT = (8, 24)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

CATEGORIES = ["生物", "科学", "历史", "艺术", "科技", "生活", "综合"]
MIN_REVIEW_SCORE = 78
USAGE = {"input_tokens": 0, "output_tokens": 0}
HARVEST_DIAGNOSTICS: dict = {
    "version": 1,
    "generated_at": None,
    "user_agent": USER_AGENT,
    "sources": {},
    "requests": [],
}


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


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def canonical_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(char for char in text if char.isalnum())


def batch(items: list, size: int) -> Iterable[list]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def response_excerpt(text: str, limit: int = 260) -> str:
    return normalize_space(text)[:limit]


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def source_report(source_name: str) -> dict:
    return HARVEST_DIAGNOSTICS.setdefault("sources", {}).setdefault(
        source_name,
        {
            "attempted": False,
            "ok": False,
            "titles": 0,
            "summaries": 0,
            "candidates": 0,
            "errors": [],
        },
    )


def add_source_error(source_name: str, message: str) -> None:
    source_report(source_name).setdefault("errors", []).append(
        normalize_space(message)[:260]
    )


def record_request_attempt(
    *,
    source_name: str,
    url: str,
    attempt: int,
    status_code: int | None = None,
    error_type: str = "",
    response_text: str = "",
    timed_out: bool = False,
    rate_limited: bool = False,
    retry_after: float | None = None,
) -> None:
    HARVEST_DIAGNOSTICS.setdefault("requests", []).append(
        {
            "source": source_name,
            "url": url,
            "attempt": attempt,
            "status_code": status_code,
            "error_type": error_type,
            "response_excerpt": response_excerpt(response_text),
            "timed_out": timed_out,
            "rate_limited": rate_limited,
            "retry_after_seconds": retry_after,
        }
    )


def request_json(url: str, *, source_name: str = "", **params) -> dict:
    label = source_name or url
    last_error: Exception | None = None
    for attempt in range(1, 4):
        response = None
        try:
            response = SESSION.get(url, params=params, timeout=TIMEOUT)
            retry_after = retry_after_seconds(response.headers.get("Retry-After"))
            record_request_attempt(
                source_name=label,
                url=response.url,
                attempt=attempt,
                status_code=response.status_code,
                response_text=response.text,
                rate_limited=response.status_code == 429,
                retry_after=retry_after,
            )
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < 3:
                wait = retry_after if response.status_code == 429 and retry_after is not None else 2 ** (attempt - 1)
                time.sleep(min(float(wait), 12.0))
                continue
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                add_source_error(
                    label,
                    f"JSON parse error: {exc}; body={response_excerpt(response.text)}",
                )
                raise
        except requests.Timeout as exc:
            last_error = exc
            record_request_attempt(
                source_name=label,
                url=response.url if response is not None else url,
                attempt=attempt,
                error_type=type(exc).__name__,
                timed_out=True,
            )
        except requests.RequestException as exc:
            last_error = exc
            record_request_attempt(
                source_name=label,
                url=response.url if response is not None else url,
                attempt=attempt,
                status_code=getattr(response, "status_code", None),
                error_type=type(exc).__name__,
                response_text=getattr(response, "text", ""),
            )
        if attempt < 3:
            time.sleep(2 ** (attempt - 1))
    message = f"{type(last_error).__name__}: {last_error}" if last_error else "request failed"
    add_source_error(label, message)
    if last_error:
        raise last_error
    raise requests.RequestException(message)


def load_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return fallback


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_cards() -> dict:
    payload = load_json(CARDS_PATH, {"version": 1, "updated_at": None, "cards": []})
    if not isinstance(payload.get("cards"), list):
        payload["cards"] = []
    return payload


def load_state() -> dict:
    payload = load_json(STATE_PATH, {"version": 1, "seen": {}})
    if not isinstance(payload.get("seen"), dict):
        payload["seen"] = {}
    return payload


def save_state(state: dict) -> None:
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
    write_json(STATE_PATH, state)


def load_candidate_pool() -> dict:
    payload = load_json(
        CANDIDATE_POOL_PATH,
        {"version": 1, "updated_at": None, "candidates": []},
    )
    if not isinstance(payload.get("candidates"), list):
        payload["candidates"] = []
    return payload


def save_candidate_pool(payload: dict) -> None:
    payload["version"] = 1
    payload["updated_at"] = iso_now()
    payload["candidates"] = payload.get("candidates", [])[:MAX_POOL_SIZE]
    write_json(CANDIDATE_POOL_PATH, payload)


def load_pool_status() -> dict:
    payload = load_json(
        POOL_STATUS_PATH,
        {
            "version": 1,
            "updated_at": None,
            "approved_cards": 0,
            "pending_candidates": 0,
            "last_run": None,
            "recent_runs": [],
        },
    )
    payload.setdefault("recent_runs", [])
    return payload


def save_pool_status(*, cards_count: int, pending_count: int, run_stats: dict) -> None:
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
    write_json(POOL_STATUS_PATH, payload)


def save_harvest_status(*, cards_count: int, pending_count: int, stats: dict) -> None:
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
    write_json(POOL_STATUS_PATH, payload)


def save_harvest_report(report: dict) -> None:
    report["generated_at"] = iso_now()
    write_json(HARVEST_REPORT_PATH, report)


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


def mark_seen(state: dict, candidate: Candidate, outcome: str, reason: str = "") -> None:
    state.setdefault("seen", {})[candidate.source_id] = {
        "last_seen": iso_now(),
        "source_url": candidate.source_url,
        "outcome": outcome,
        "reason": normalize_space(reason)[:180],
    }


def wikipedia_extracts(titles: list[str], language: str, source_name: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for title_batch in batch(titles, 20):
        payload = request_json(
            f"https://{language}.wikipedia.org/w/api.php",
            source_name=source_name,
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
    time.sleep(0.35)
    return result


def random_title_is_weak(title: str) -> bool:
    lowered = title.lower()
    if lowered.startswith(("list of ", "outline of ", "index of ", "timeline of ", "glossary of ")):
        return True
    return bool(re.fullmatch(r"\d{3,4}", title)) or len(title) < 3 or len(title) > 90


def fetch_random_titles(language: str, count: int, source_name: str) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    while len(titles) < count:
        payload = request_json(
            f"https://{language}.wikipedia.org/w/api.php",
            source_name=source_name,
            action="query",
            list="random",
            rnnamespace=0,
            rnlimit=min(50, count - len(titles) + 20),
            format="json",
            origin="*",
        )
        for item in payload.get("query", {}).get("random", []):
            title = normalize_space(item.get("title", ""))
            if title and title not in seen and not random_title_is_weak(title):
                titles.append(title)
                seen.add(title)
            if len(titles) >= count:
                break
        time.sleep(0.35)
        if not payload.get("query", {}).get("random"):
            break
    return titles


def candidate_from_wikipedia_page(
    *,
    source_id_prefix: str,
    language: str,
    page: dict,
    category_hint: str,
    source_rank: int,
) -> Candidate | None:
    title = normalize_space(page.get("title", ""))
    extract = normalize_space(page.get("extract", ""))
    if not title or random_title_is_weak(title) or len(extract) < 180:
        return None
    page_id = page.get("pageid", title)
    return Candidate(
        source_id=f"{source_id_prefix}:{language}:{page_id}",
        source_name=f"{language.upper()} Wikipedia：{title}",
        source_url=page.get(
            "fullurl",
            f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
        ),
        title=title,
        excerpt=extract,
        category_hint=category_hint,
        source_rank=source_rank,
    )


def fetch_wikipedia_random_samples(language: str, request_count: int, source_rank: int = 4) -> list[Candidate]:
    source_name = f"{language.upper()} Wikipedia random"
    titles = fetch_random_titles(language, request_count * 2, source_name)
    pages = wikipedia_extracts(titles, language, source_name) if titles else {}
    candidates = []
    for title in titles:
        page = pages.get(title) or next(
            (value for key, value in pages.items() if canonical_text(key) == canonical_text(title)),
            {},
        )
        candidate = candidate_from_wikipedia_page(
            source_id_prefix="wiki-random",
            language=language,
            page=page,
            category_hint="综合",
            source_rank=source_rank,
        )
        if candidate:
            candidates.append(candidate)
        if len(candidates) >= request_count:
            break
    random.SystemRandom().shuffle(candidates)
    return candidates


def fetch_category_member_titles(language: str, category: str, limit: int, source_name: str) -> list[str]:
    payload = request_json(
        f"https://{language}.wikipedia.org/w/api.php",
        source_name=source_name,
        action="query",
        list="categorymembers",
        cmtitle=f"Category:{category}",
        cmnamespace=0,
        cmtype="page",
        cmlimit=min(500, max(1, limit)),
        format="json",
        origin="*",
    )
    return [
        normalize_space(item.get("title", ""))
        for item in payload.get("query", {}).get("categorymembers", [])
        if normalize_space(item.get("title", ""))
    ]


def fetch_wikipedia_topic_samples() -> list[Candidate]:
    topics = [
        ("en", "Biological processes", "生物"),
        ("en", "Physical phenomena", "科学"),
        ("en", "Earth sciences", "科学"),
        ("en", "Food science", "生活"),
        ("en", "Materials science", "科技"),
        ("en", "History of technology", "历史"),
        ("en", "Art techniques", "艺术"),
        ("en", "Human behavior", "生活"),
        ("en", "Linguistics", "综合"),
        ("en", "Ecology", "生物"),
        ("en", "Astronomy", "科学"),
        ("en", "Engineering", "科技"),
        ("en", "Everyday life", "生活"),
    ]
    rng = random.SystemRandom()
    rng.shuffle(topics)
    candidates: list[Candidate] = []
    source_name = "Wikipedia topic samples"
    for language, category, hint in topics:
        titles = fetch_category_member_titles(language, category, 35, source_name)
        rng.shuffle(titles)
        pages = wikipedia_extracts(titles[:24], language, source_name) if titles else {}
        added_here = 0
        for title in titles[:24]:
            page = pages.get(title) or {}
            candidate = candidate_from_wikipedia_page(
                source_id_prefix="wiki-topic",
                language=language,
                page=page,
                category_hint=hint,
                source_rank=2,
            )
            if candidate:
                candidates.append(candidate)
                added_here += 1
            if added_here >= 12:
                break
    unique = {item.source_id: item for item in candidates}
    result = list(unique.values())
    rng.shuffle(result)
    return result


def fetch_nasa_apod() -> list[Candidate]:
    payload = request_json(
        "https://api.nasa.gov/planetary/apod",
        source_name="NASA APOD",
        api_key=os.getenv("NASA_API_KEY", "DEMO_KEY"),
        count=4,
        thumbs=True,
    )
    candidates = []
    for item in payload if isinstance(payload, list) else []:
        title = normalize_space(item.get("title", ""))
        explanation = normalize_space(item.get("explanation", ""))
        if title and len(explanation) >= 160:
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


def fetch_on_this_day() -> list[Candidate]:
    today = utc_now()
    month_day = f"{today.month:02d}/{today.day:02d}"
    payload = None
    language = "en"
    for language_code in ("zh", "en"):
        try:
            payload = request_json(
                f"https://api.wikimedia.org/feed/v1/wikipedia/{language_code}/onthisday/all/{month_day}",
                source_name=f"Wikimedia On This Day {language_code}",
            )
            language = language_code
            break
        except (requests.RequestException, ValueError) as exc:
            add_source_error(f"Wikimedia On This Day {language_code}", f"{type(exc).__name__}: {exc}")
    if not payload:
        return []
    candidates = []
    for index, event in enumerate(payload.get("events", [])[:16]):
        text = normalize_space(event.get("text", ""))
        year = event.get("year", "")
        pages = event.get("pages", [])
        page = pages[0] if pages else {}
        title = page.get("normalizedtitle") or page.get("title") or f"{year} event"
        extract = normalize_space(page.get("extract", ""))
        source_url = page.get("content_urls", {}).get("desktop", {}).get("page", "")
        if len(text) >= 35 and len(extract) >= 100:
            candidates.append(
                Candidate(
                    source_id=f"onthisday:{language}:{month_day}:{year}:{index}",
                    source_name=f"Wikimedia On This Day：{title}",
                    source_url=source_url or f"https://{language}.wikipedia.org/wiki/{quote(str(title))}",
                    title=f"{year}: {text}",
                    excerpt=f"事件：{text}\n相关页面摘要：{extract}",
                    category_hint="历史",
                    source_rank=3,
                )
            )
    return candidates


def run_source(name: str, provider) -> list[Candidate]:
    report = source_report(name)
    report["attempted"] = True
    try:
        items = provider()
    except (requests.RequestException, ValueError) as exc:
        add_source_error(name, f"{type(exc).__name__}: {exc}")
        print(f"[source] {name}: failed {type(exc).__name__}: {exc}", file=sys.stderr)
        return []
    report["titles"] += len([item for item in items if normalize_space(item.title)])
    report["summaries"] += len([item for item in items if normalize_space(item.excerpt)])
    report["candidates"] += len(items)
    report["ok"] = report["ok"] or bool(items)
    print(
        f"[source] {name}: titles={report['titles']} "
        f"summaries={report['summaries']} candidates={len(items)}"
    )
    return items


def fetch_candidates(harvest_pass: int = 0) -> list[Candidate]:
    collected: list[Candidate] = []
    if harvest_pass == 0:
        collected.extend(run_source("Wikipedia topic samples", fetch_wikipedia_topic_samples))
        collected.extend(run_source("NASA APOD", fetch_nasa_apod))
        collected.extend(run_source("Wikimedia On This Day", fetch_on_this_day))
    collected.extend(
        run_source(
            f"English Wikipedia random pass {harvest_pass + 1}",
            lambda: fetch_wikipedia_random_samples("en", 100, 4),
        )
    )
    collected.extend(
        run_source(
            f"Chinese Wikipedia random pass {harvest_pass + 1}",
            lambda: fetch_wikipedia_random_samples("zh", 60, 4),
        )
    )
    return list({item.source_id: item for item in collected}.values())


def candidate_is_low_information(candidate: Candidate) -> bool:
    text = normalize_space(candidate.excerpt)
    if candidate.source_id.startswith("met:"):
        return True
    if len(text) < 120:
        return True
    weak_labels = ("born ", "died ", "politician", "出生于", "逝世于", "是一名政治人物")
    return sum(label.lower() in text.lower() for label in weak_labels) >= 2 and candidate.source_rank >= 3


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


def prune_unusable_pool_records(pool_payload: dict) -> dict[str, int]:
    counts = {"invalid_record": 0, "low_information": 0}
    kept = []
    for record in pool_payload.get("candidates", []):
        candidate = candidate_from_record(record)
        if not candidate:
            counts["invalid_record"] += 1
        elif candidate_is_low_information(candidate):
            counts["low_information"] += 1
        else:
            kept.append(record)
    pool_payload["candidates"] = kept
    return counts


def merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value)


def merge_candidates_into_pool(
    pool_payload: dict,
    fetched: list[Candidate],
    cards: list[dict],
    state: dict,
) -> tuple[int, dict[str, int]]:
    existing_source_ids = {str(card.get("source_id", "")) for card in cards if card.get("source_id")}
    existing_urls = {normalize_space(card.get("source_url", "")) for card in cards if card.get("source_url")}
    records = pool_payload.get("candidates", [])
    pool_ids = {str(record.get("source_id", "")) for record in records if record.get("source_id")}
    counts = {
        "already_in_pool": 0,
        "already_published": 0,
        "seen_recently": 0,
        "low_information": 0,
        "pool_limit": 0,
    }
    added = 0
    for candidate in sorted(fetched, key=lambda item: (item.source_rank, random.random())):
        if candidate.source_id in pool_ids:
            counts["already_in_pool"] += 1
        elif candidate.source_id in existing_source_ids or normalize_space(candidate.source_url) in existing_urls:
            counts["already_published"] += 1
        elif seen_recently(candidate.source_id, state):
            counts["seen_recently"] += 1
        elif candidate_is_low_information(candidate):
            counts["low_information"] += 1
        elif len(records) >= MAX_POOL_SIZE:
            counts["pool_limit"] += 1
        else:
            records.append(candidate_to_record(candidate))
            pool_ids.add(candidate.source_id)
            added += 1
    records.sort(key=lambda record: (int(record.get("source_rank", 9)), str(record.get("added_at", ""))))
    pool_payload["candidates"] = records[:MAX_POOL_SIZE]
    return added, counts


def source_family(source_id: str) -> str:
    if source_id.startswith("wiki-topic:"):
        return "维基主题分类"
    if source_id.startswith("wiki-random:en:"):
        return "英文维基随机"
    if source_id.startswith("wiki-random:zh:"):
        return "中文维基随机"
    if source_id.startswith("nasa-apod:"):
        return "NASA APOD"
    if source_id.startswith("onthisday:"):
        return "历史上的今天"
    if source_id.startswith("met:"):
        return "大都会博物馆"
    return "其他"


def harvest_until_reserve(pool_payload: dict, cards: list[dict], state: dict) -> tuple[int, int, dict[str, int], int, dict[str, int]]:
    total_fetched = 0
    total_added = 0
    combined_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    passes_used = 0
    consecutive_empty_passes = 0
    while len(pool_payload.get("candidates", [])) < HARVEST_TARGET_PENDING and passes_used < HARVEST_MAX_PASSES:
        fetched = fetch_candidates(passes_used)
        passes_used += 1
        total_fetched += len(fetched)
        for candidate in fetched:
            family = source_family(candidate.source_id)
            source_counts[family] = source_counts.get(family, 0) + 1
        added, counts = merge_candidates_into_pool(pool_payload, fetched, cards, state)
        if len(pool_payload.get("candidates", [])) > HARVEST_TARGET_PENDING:
            overflow = len(pool_payload["candidates"]) - HARVEST_TARGET_PENDING
            pool_payload["candidates"] = pool_payload["candidates"][:HARVEST_TARGET_PENDING]
            added = max(0, added - overflow)
            counts["pool_limit"] = counts.get("pool_limit", 0) + overflow
        total_added += added
        merge_counts(combined_counts, counts)
        print(f"[harvest {passes_used}] fetched={len(fetched)} added={added} pending={len(pool_payload.get('candidates', []))}")
        consecutive_empty_passes = consecutive_empty_passes + 1 if added == 0 else 0
        if consecutive_empty_passes >= 2:
            break
    return total_fetched, total_added, combined_counts, passes_used, source_counts


def select_pool_batch(
    pool_payload: dict,
    cards: list[dict],
    state: dict,
    limit: int,
) -> tuple[list[Candidate], list[str], dict[str, int]]:
    records = pool_payload.get("candidates", [])
    existing_urls = {normalize_space(card.get("source_url", "")) for card in cards if card.get("source_url")}
    selected: list[Candidate] = []
    selected_ids: list[str] = []
    counts = {"invalid_record": 0, "duplicate_source": 0, "seen_recently": 0, "low_information": 0}
    records.sort(key=lambda record: (int(record.get("source_rank", 9)), random.random()))
    for record in records:
        candidate = candidate_from_record(record)
        if not candidate:
            counts["invalid_record"] += 1
        elif normalize_space(candidate.source_url) in existing_urls:
            counts["duplicate_source"] += 1
        elif seen_recently(candidate.source_id, state):
            counts["seen_recently"] += 1
        elif candidate_is_low_information(candidate):
            counts["low_information"] += 1
        else:
            selected.append(candidate)
            selected_ids.append(candidate.source_id)
            if len(selected) >= limit:
                break
    return selected, selected_ids, counts


def remove_candidates_from_pool(pool_payload: dict, source_ids: set[str]) -> None:
    pool_payload["candidates"] = [
        record
        for record in pool_payload.get("candidates", [])
        if str(record.get("source_id", "")) not in source_ids
    ]


EXTRACTION_INSTRUCTIONS = """
你是“拾光”知识编辑。请从候选材料里找适合普通人轻阅读的具体知识。
只能依据候选材料，不能补充原文没有的事实。每个候选最多生成1条。
优先保留反直觉的时间关系、日常现象背后的原因、设计细节、语言与历史误区、生物和材料机制。
拒绝纯人物生平、职位定义、国家概况、军舰参数、普通日期罗列和宽泛主题介绍。
标题8到36个汉字，直接呈现具体事实或自然问题。分类只能是：生物、科学、历史、艺术、科技、生活、综合。
evidence 必须逐字复制候选材料中的一句或一段，不能翻译、改写或补标点，最多100字。
""".strip()


REVIEW_INSTRUCTIONS = """
你是“拾光”的内容主编。审核时只依据候选来源，不要为了显得严格而一律拒绝。
硬性拒绝：核心事实无法由来源支持；只是人物、机构、职位或国家简介；标题和正文说的不是同一件事；
宽泛空话或模板化标题；evidence 不是来源中的原文。
可以在不增加事实的前提下修改标题、lead、解释和角度。质量达到78分且没有硬性问题，应 approved=true。
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


def record_usage(response) -> None:
    usage = getattr(response, "usage", None)
    if usage:
        USAGE["input_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
        USAGE["output_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)


def extract_proposals(client: OpenAI, candidates: list[Candidate]) -> list[Proposal]:
    proposals: list[Proposal] = []
    for candidate_batch in batch(candidates, 10):
        response = client.responses.parse(
            model=MODEL,
            reasoning={"effort": "low"},
            input=[
                {"role": "system", "content": EXTRACTION_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": "从这些候选中提取可用知识卡："
                    + json.dumps([compact_candidate(item) for item in candidate_batch], ensure_ascii=False, separators=(",", ":")),
                },
            ],
            text_format=ProposalBatch,
        )
        record_usage(response)
        if response.output_parsed:
            proposals.extend(response.output_parsed.cards)
    return proposals


def normalized(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def title_is_bad(title: str) -> bool:
    return any(
        re.search(pattern, title)
        for pattern in (r"有什么值得注意", r"是什么[？?]?$", r"关于.+你知道", r"简介[？?]?$", r"谁是.+[？?]?$")
    )


def similar_to_existing(title: str, existing_titles: list[str]) -> bool:
    target = normalized(title)
    return any(SequenceMatcher(None, target, normalized(other)).ratio() >= 0.80 for other in existing_titles)


def prequalify_proposals(
    proposals: list[Proposal],
    candidate_map: dict[str, Candidate],
    existing_cards: list[dict],
) -> tuple[list[Proposal], dict[str, int]]:
    existing_titles = [card.get("title", "") for card in existing_cards]
    counts = {"low_confidence": 0, "missing_candidate": 0, "bad_title": 0, "bad_category": 0, "similar_title": 0}
    accepted: list[Proposal] = []
    for proposal in proposals:
        title = normalize_space(proposal.title)
        if proposal.source_id not in candidate_map:
            counts["missing_candidate"] += 1
        elif proposal.confidence < 68:
            counts["low_confidence"] += 1
        elif not 8 <= len(title) <= 38 or title_is_bad(title):
            counts["bad_title"] += 1
        elif proposal.category not in CATEGORIES:
            counts["bad_category"] += 1
        elif similar_to_existing(title, existing_titles):
            counts["similar_title"] += 1
        else:
            accepted.append(proposal)
    return accepted, counts


def review_proposals(client: OpenAI, proposals: list[Proposal], candidate_map: dict[str, Candidate]) -> list[ReviewItem]:
    reviewed: list[ReviewItem] = []
    for proposal_batch in batch(proposals, 10):
        review_input = [
            {"proposal": proposal.model_dump(), "source": compact_candidate(candidate_map[proposal.source_id])}
            for proposal in proposal_batch
            if proposal.source_id in candidate_map
        ]
        if not review_input:
            continue
        response = client.responses.parse(
            model=MODEL,
            reasoning={"effort": "low"},
            input=[
                {"role": "system", "content": REVIEW_INSTRUCTIONS},
                {"role": "user", "content": "审核并修正以下知识卡：" + json.dumps(review_input, ensure_ascii=False, separators=(",", ":"))},
            ],
            text_format=ReviewBatch,
        )
        record_usage(response)
        if response.output_parsed:
            reviewed.extend(response.output_parsed.items)
    return reviewed


def evidence_supported(evidence: str, source_text: str) -> bool:
    evidence_key = canonical_text(evidence)
    return len(evidence_key) >= 8 and evidence_key in canonical_text(source_text)


def build_new_cards(
    reviews: list[ReviewItem],
    candidate_map: dict[str, Candidate],
    existing_cards: list[dict],
    limit: int,
) -> tuple[list[dict], dict[str, int], dict[str, str]]:
    existing_titles = [item.get("title", "") for item in existing_cards]
    existing_urls = {item.get("source_url", "") for item in existing_cards if item.get("source_url")}
    counts = {"model_rejected": 0, "score_below_78": 0, "bad_title": 0, "bad_category": 0, "evidence_mismatch": 0, "duplicate_source": 0, "similar_title": 0}
    reasons: dict[str, str] = {}
    new_cards: list[dict] = []
    for item in sorted(reviews, key=lambda value: value.quality_score, reverse=True):
        candidate = candidate_map.get(item.source_id)
        title = normalize_space(item.title)
        if not candidate:
            continue
        if not item.approved:
            counts["model_rejected"] += 1
            reasons[item.source_id] = item.rejection_reason or "AI主编未批准"
        elif item.quality_score < MIN_REVIEW_SCORE:
            counts["score_below_78"] += 1
            reasons[item.source_id] = f"质量分 {item.quality_score}"
        elif not 8 <= len(title) <= 38 or title_is_bad(title):
            counts["bad_title"] += 1
            reasons[item.source_id] = "标题格式未通过"
        elif item.category not in CATEGORIES:
            counts["bad_category"] += 1
            reasons[item.source_id] = "分类无效"
        elif not evidence_supported(item.evidence, candidate.excerpt):
            counts["evidence_mismatch"] += 1
            reasons[item.source_id] = "证据未能在原文中匹配"
        elif candidate.source_url in existing_urls:
            counts["duplicate_source"] += 1
            reasons[item.source_id] = "来源已经使用过"
        elif similar_to_existing(title, existing_titles):
            counts["similar_title"] += 1
            reasons[item.source_id] = "与已有标题过于相似"
        else:
            digest = hashlib.sha256(f"{title}|{candidate.source_url}".encode("utf-8")).hexdigest()[:16]
            new_cards.append(
                {
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
            )
            existing_titles.append(title)
            existing_urls.add(candidate.source_url)
            reasons[item.source_id] = "accepted"
        if len(new_cards) >= limit:
            break
    return new_cards, counts, reasons


def print_counts(label: str, counts: dict[str, int]) -> None:
    useful = ", ".join(f"{key}={value}" for key, value in counts.items() if value)
    print(f"[filter:{label}] {useful or 'none'}")


def resolved_target_new_cards() -> int:
    if TARGET_NEW_CARDS_ENV:
        try:
            return max(1, min(40, int(TARGET_NEW_CARDS_ENV)))
        except ValueError:
            pass
    return 12 if RUN_SOURCE in {"web", "manual", "workflow_dispatch"} else 5


def resolved_max_rounds() -> int:
    return MAX_PROCESS_ROUNDS if RUN_SOURCE in {"web", "manual", "workflow_dispatch"} else 1


def run_harvest(started_at: datetime, cards_payload: dict, pool_payload: dict, state: dict) -> int:
    existing_cards = cards_payload.get("cards", [])
    pool_before = len(pool_payload.get("candidates", []))
    prune_counts = prune_unusable_pool_records(pool_payload)
    pool_after_prune = len(pool_payload.get("candidates", []))
    refill_needed = pool_after_prune < HARVEST_REFILL_TRIGGER
    if refill_needed:
        harvested, added, filters, passes, source_counts = harvest_until_reserve(pool_payload, existing_cards, state)
    else:
        harvested, added, filters, passes, source_counts = 0, 0, {}, 0, {}
    merge_counts(filters, prune_counts)
    pool_after = len(pool_payload.get("candidates", []))
    inventory_sufficient = pool_before >= HARVEST_TARGET_PENDING or pool_after >= HARVEST_TARGET_PENDING
    attempted_harvest = passes > 0
    ok_sources = [name for name, info in HARVEST_DIAGNOSTICS.get("sources", {}).items() if info.get("ok")]
    success = (
        inventory_sufficient
        or not refill_needed
        or (attempted_harvest and added >= HARVEST_MIN_ADDED and bool(ok_sources))
    )
    if inventory_sufficient:
        message = "候选池库存充足，无需补充"
    elif not refill_needed:
        message = "候选池库存尚可，等待继续消耗"
    elif success:
        message = "候选囤货成功"
    else:
        message = f"候选囤货未达标：新增 {added} 条，成功阈值 {HARVEST_MIN_ADDED} 条"
    stats = {
        "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
        "source": RUN_SOURCE,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": iso_now(),
        "passes": passes,
        "fetched": harvested,
        "added": added,
        "pool_before": pool_before,
        "pool_after": pool_after,
        "target_pool_size": HARVEST_TARGET_PENDING,
        "refill_trigger": HARVEST_REFILL_TRIGGER,
        "refill_needed": refill_needed,
        "source_counts": source_counts,
        "success": success,
        "min_added_for_success": HARVEST_MIN_ADDED,
        "ok_sources": ok_sources,
        "diagnostic_report": "data/harvest_report.json",
        "message": message,
    }
    HARVEST_DIAGNOSTICS.update(
        {
            "started_at": stats["started_at"],
            "finished_at": stats["finished_at"],
            "pool_before": pool_before,
            "pool_after": pool_after,
            "target_pool_size": HARVEST_TARGET_PENDING,
            "refill_trigger": HARVEST_REFILL_TRIGGER,
            "refill_needed": refill_needed,
            "summary": stats,
            "filters": filters,
        }
    )
    save_harvest_report(HARVEST_DIAGNOSTICS)
    if success:
        save_candidate_pool(pool_payload)
        save_state(state)
    save_harvest_status(cards_count=len(existing_cards), pending_count=len(pool_payload.get("candidates", [])), stats=stats)
    print_counts("harvest", filters)
    print(f"[harvest] fetched={harvested} added={added} pending={len(pool_payload.get('candidates', []))}")
    if inventory_sufficient:
        return 0
    if not refill_needed:
        return 0
    if not ok_sources:
        print("[harvest] all major sources failed", file=sys.stderr)
        return 1
    if attempted_harvest and added < HARVEST_MIN_ADDED:
        print(f"[harvest] added {added}, below required {HARVEST_MIN_ADDED}", file=sys.stderr)
        return 1
    return 0


def run_generate(started_at: datetime, cards_payload: dict, pool_payload: dict, state: dict) -> int:
    target_new_cards = resolved_target_new_cards()
    existing_cards = cards_payload.get("cards", [])
    pending_now = len(pool_payload.get("candidates", []))
    if pending_now < MIN_CANDIDATES_TO_GENERATE:
        run_stats = {
            "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
            "source": RUN_SOURCE,
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "finished_at": iso_now(),
            "target_new_cards": target_new_cards,
            "rounds": 0,
            "pool_before": pending_now,
            "pool_after": pending_now,
            "processed": 0,
            "proposals": 0,
            "reviewed": 0,
            "added": 0,
            "pass_rate": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "message": f"候选池只有 {pending_now} 条，低于 {MIN_CANDIDATES_TO_GENERATE} 条；本次未调用AI",
            "filters": {"before_ai": {}, "before_review": {}, "final": {}},
            "source_stats": {},
        }
        save_candidate_pool(pool_payload)
        save_state(state)
        save_pool_status(cards_count=len(existing_cards), pending_count=pending_now, run_stats=run_stats)
        print(f"[pipeline] {run_stats['message']}")
        return 0
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is missing.", file=sys.stderr)
        return 2
    if OpenAI is None:
        print("OpenAI SDK is not installed.", file=sys.stderr)
        return 2
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=os.getenv("OPENAI_BASE_URL", "").strip() or None)
    all_new_cards: list[dict] = []
    processed_total = proposals_total = reviewed_total = rounds_used = 0
    source_stats: dict[str, dict[str, int]] = {}
    before_ai_counts: dict[str, int] = {}
    before_review_counts: dict[str, int] = {}
    final_filter_counts: dict[str, int] = {}
    for _ in range(resolved_max_rounds()):
        remaining = target_new_cards - len(all_new_cards)
        if remaining <= 0:
            break
        selected, selected_ids, pool_filter_counts = select_pool_batch(pool_payload, all_new_cards + existing_cards, state, MAX_CANDIDATES_PER_ROUND)
        merge_counts(before_ai_counts, pool_filter_counts)
        if not selected:
            break
        rounds_used += 1
        processed_total += len(selected)
        for candidate in selected:
            family = source_family(candidate.source_id)
            source_stats.setdefault(family, {"processed": 0, "accepted": 0})
            source_stats[family]["processed"] += 1
        proposals = extract_proposals(client, selected)
        proposals_total += len(proposals)
        candidate_map = {item.source_id: item for item in selected}
        prequalified, proposal_filter_counts = prequalify_proposals(proposals, candidate_map, all_new_cards + existing_cards)
        merge_counts(before_review_counts, proposal_filter_counts)
        reviews = review_proposals(client, prequalified, candidate_map) if prequalified else []
        reviewed_total += len(reviews)
        new_cards, round_final_counts, reasons = build_new_cards(reviews, candidate_map, all_new_cards + existing_cards, remaining)
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
                mark_seen(state, candidate, "rejected", reasons[candidate.source_id])
            elif candidate.source_id in reviewed_ids:
                mark_seen(state, candidate, "reviewed_not_saved")
            elif candidate.source_id in proposal_ids:
                mark_seen(state, candidate, "filtered_before_review")
            else:
                mark_seen(state, candidate, "no_proposal")
        remove_candidates_from_pool(pool_payload, set(selected_ids))
    if all_new_cards:
        cards_payload["version"] = 1
        cards_payload["updated_at"] = iso_now()
        cards_payload["cards"] = (all_new_cards + existing_cards)[:MAX_CARDS_STORED]
        write_json(CARDS_PATH, cards_payload)
    save_candidate_pool(pool_payload)
    save_state(state)
    duration_seconds = round((utc_now() - started_at).total_seconds(), 2)
    pass_rate = round(len(all_new_cards) / processed_total * 100, 1) if processed_total else 0
    run_stats = {
        "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
        "source": RUN_SOURCE,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": iso_now(),
        "duration_seconds": duration_seconds,
        "target_new_cards": target_new_cards,
        "rounds": rounds_used,
        "pool_before": pending_now,
        "pool_after": len(pool_payload.get("candidates", [])),
        "processed": processed_total,
        "proposals": proposals_total,
        "reviewed": reviewed_total,
        "added": len(all_new_cards),
        "pass_rate": pass_rate,
        "input_tokens": USAGE["input_tokens"],
        "output_tokens": USAGE["output_tokens"],
        "filters": {"before_ai": before_ai_counts, "before_review": before_review_counts, "final": final_filter_counts},
        "source_stats": source_stats,
    }
    save_pool_status(cards_count=len(cards_payload.get("cards", existing_cards)), pending_count=len(pool_payload.get("candidates", [])), run_stats=run_stats)
    print(f"[pipeline] added {len(all_new_cards)} cards; pending={len(pool_payload.get('candidates', []))}")
    return 0


def main() -> int:
    started_at = utc_now()
    cards_payload = load_cards()
    state = load_state()
    pool_payload = load_candidate_pool()
    prune_unusable_pool_records(pool_payload)
    if PIPELINE_MODE == "harvest":
        return run_harvest(started_at, cards_payload, pool_payload, state)
    return run_generate(started_at, cards_payload, pool_payload, state)


if __name__ == "__main__":
    raise SystemExit(main())
