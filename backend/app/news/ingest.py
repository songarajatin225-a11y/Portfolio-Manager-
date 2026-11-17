"""RSS/Atom ingestion with de-duplication, entity tagging and sentiment classification.

Sources are configured by an administrator (publisher or exchange RSS feeds). Requests use
conditional GET (ETag / Last-Modified) to be polite and respect publisher bandwidth.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Instrument, NewsArticle, NewsArticleInstrument, NewsSource
from app.news.sentiment import classify

logger = logging.getLogger(__name__)
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|ref$|ref_src|cmpid|from$)")
USER_AGENT = "MarketIntel news reader (+https://github.com/)"
CORPORATE_SUFFIXES = {"ltd", "ltd.", "limited", "corporation", "corp", "company", "co", "inc", "plc"}
# Single words too generic to identify a company on their own.
GENERIC_FIRST_WORDS = {
    "india",
    "indian",
    "state",
    "national",
    "bharat",
    "hindustan",
    "united",
    "power",
    "finance",
    "financial",
    "capital",
    "steel",
    "energy",
    "bank",
    "motors",
    "global",
    "international",
    "simulated",
    "general",
    "new",
    "first",
    "central",
    "southern",
    "eastern",
    "western",
    "northern",
    "asian",
    "tata",
    "adani",
    "bajaj",
}


def canonical_url(url: str) -> str:
    p = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False) if not TRACKING_PARAMS.match(k.lower())]
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", "", urlencode(query), ""))


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", title.lower())).strip()


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


class EntityMatcher:
    """Matches company names and NSE symbols in headlines to instruments."""

    def __init__(self, instruments: list[Instrument]) -> None:
        self.patterns: list[tuple[re.Pattern[str], int, str]] = []
        for inst in instruments:
            if inst.is_index:
                continue
            sym = inst.symbol.upper()
            if len(sym) >= 4 and sym.isalpha():
                self.patterns.append((re.compile(rf"\b{re.escape(sym)}\b"), inst.id, "symbol"))
            words = re.findall(r"[A-Za-z&]+", inst.name or "")
            if words and words[0].lower() == "the":
                words = words[1:]
            while words and words[-1].lower() in CORPORATE_SUFFIXES:
                words = words[:-1]
            if len(words) >= 2:
                core = " ".join(words[:3])
                self.patterns.append((re.compile(rf"\b{re.escape(core)}\b", re.IGNORECASE), inst.id, "name"))
            if words and len(words[0]) >= 6 and words[0].lower() not in GENERIC_FIRST_WORDS:
                self.patterns.append((re.compile(rf"\b{re.escape(words[0])}\b"), inst.id, "name"))

    def match(self, text: str) -> dict[int, str]:
        found: dict[int, str] = {}
        for pattern, iid, kind in self.patterns:
            if iid not in found and pattern.search(text):
                found[iid] = kind
        return found


def parse_published(entry: Any) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return datetime(*st[:6], tzinfo=UTC)
    return datetime.now(UTC)


async def fetch_source(http: httpx.AsyncClient, source: NewsSource) -> tuple[list[dict[str, Any]], dict[str, str]]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml"}
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.modified:
        headers["If-Modified-Since"] = source.modified
    resp = await http.get(source.url, headers=headers, follow_redirects=True, timeout=20)
    if resp.status_code == 304:
        return [], {}
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Feed could not be parsed: {parsed.get('bozo_exception')}")
    cache = {
        k: v for k, v in (("etag", resp.headers.get("etag")), ("modified", resp.headers.get("last-modified"))) if v
    }
    items = []
    for e in parsed.entries[:200]:
        link, title = e.get("link"), strip_html(e.get("title", ""))
        if not link or not title:
            continue
        items.append(
            {
                "url": link,
                "title": title[:1000],
                "summary": strip_html(e.get("summary", ""))[:4000],
                "published_at": parse_published(e),
            }
        )
    return items, cache


async def store_items(
    db: AsyncSession, source: NewsSource, items: list[dict[str, Any]], matcher: EntityMatcher
) -> list[int]:
    added: list[int] = []
    window_start = datetime.now(UTC) - timedelta(days=3)
    for item in items:
        url = canonical_url(item["url"])
        title_hash = sha(normalize_title(item["title"]))
        # duplicate syndicated story: same normalised headline seen recently from any source
        dup = await db.scalar(
            select(func.count())
            .select_from(NewsArticle)
            .where(NewsArticle.title_hash == title_hash, NewsArticle.published_at >= window_start)
        )
        if dup:
            continue
        s = classify(item["title"], item["summary"])
        stmt = (
            insert(NewsArticle)
            .values(
                source_id=source.id,
                source_name=source.name,
                url=url,
                url_hash=sha(url),
                title_hash=title_hash,
                title=item["title"],
                summary=item["summary"],
                category=source.category,
                published_at=min(item["published_at"], datetime.now(UTC)),
                sentiment_label=s.label,
                sentiment_score=s.score,
                sentiment_confidence=s.confidence,
                sentiment_method=s.method,
                impact=s.impact,
            )
            .on_conflict_do_nothing(index_elements=["url_hash"])
            .returning(NewsArticle.id)
        )
        article_id = await db.scalar(stmt)
        if article_id is None:
            continue
        added.append(article_id)
        for iid, kind in matcher.match(f"{item['title']} {item['summary'][:500]}").items():
            db.add(NewsArticleInstrument(article_id=article_id, instrument_id=iid, match_type=kind))
    return added


async def ingest_all(db: AsyncSession, http: httpx.AsyncClient | None = None) -> dict[str, Any]:
    from app.services.market_data import is_simulated_mode

    sources = (await db.scalars(select(NewsSource).where(NewsSource.is_active.is_(True)))).all()
    if not sources:
        return {"sources": 0, "added": 0, "errors": []}
    simulated = await is_simulated_mode(db)
    instruments = (
        await db.scalars(
            select(Instrument).where(
                Instrument.is_simulated.is_(simulated), Instrument.segment == "EQ", Instrument.is_active.is_(True)
            )
        )
    ).all()
    matcher = EntityMatcher(list(instruments))
    own = http is None
    http = http or httpx.AsyncClient()
    new_ids: list[int] = []
    errors = []
    try:
        for src in sources:
            try:
                items, cache = await fetch_source(http, src)
                new_ids += await store_items(db, src, items, matcher)
                src.etag = cache.get("etag", src.etag)
                src.modified = cache.get("modified", src.modified)
                src.last_error = None
            except Exception as exc:  # noqa: BLE001 - one failing feed must not stop the others
                logger.warning("News source %s failed: %s", src.name, exc)
                src.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                errors.append({"source": src.name, "error": src.last_error})
            src.last_fetched_at = datetime.now(UTC)
            await db.commit()
    finally:
        if own:
            await http.aclose()
    from app.news.ai_sentiment import classify_with_claude

    ai_classified = await classify_with_claude(db, new_ids)
    return {"sources": len(sources), "added": len(new_ids), "ai_classified": ai_classified, "errors": errors}


async def instrument_sentiment(db: AsyncSession, days: int = 7) -> dict[int, dict[str, float]]:
    """Confidence-weighted average sentiment per instrument over recent articles."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(NewsArticleInstrument.instrument_id, NewsArticle.sentiment_score, NewsArticle.sentiment_confidence)
            .join(NewsArticle, NewsArticle.id == NewsArticleInstrument.article_id)
            .where(NewsArticle.published_at >= since)
        )
    ).all()
    acc: dict[int, list[tuple[float, float]]] = {}
    for iid, score, conf in rows:
        acc.setdefault(iid, []).append((score, max(conf, 0.05)))
    out = {}
    for iid, vals in acc.items():
        w = sum(c for _, c in vals)
        out[iid] = {"score": sum(s * c for s, c in vals) / w, "articles": len(vals)}
    return out
