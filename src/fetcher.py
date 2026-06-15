"""
Fetches articles from RSS feeds or HTML listing pages.
Returns a list of Article dataclasses filtered to the last `window_hours`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup

from src import rss_detector

log = logging.getLogger(__name__)

WINDOW_HOURS = 24


@dataclass
class Article:
    title: str
    url: str
    source: str
    source_url: str
    published: datetime | None  # UTC, or None if date unavailable


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _within_window(dt: datetime | None) -> bool:
    if dt is None:
        return True  # include undated articles — can't rule them out
    return (_now_utc() - dt) <= timedelta(hours=WINDOW_HOURS)


def _parse_rss_date(entry: Any) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                import time as _time
                return datetime.fromtimestamp(_time.mktime(t), tz=timezone.utc)
            except Exception:
                pass
    # fallback: try raw string
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
            except Exception:
                pass
    return None


# ── RSS fetcher ───────────────────────────────────────────────────────────────

def fetch_rss(source_name: str, feed_url: str, source_url: str = "") -> list[Article]:
    parsed = feedparser.parse(
        feed_url,
        request_headers={"User-Agent": "blogaggregate/1.0"},
    )
    if parsed.bozo and not parsed.entries:
        log.warning("%s: feed parse error — %s", source_name, parsed.bozo_exception)
        return []

    articles = []
    for entry in parsed.entries:
        pub = _parse_rss_date(entry)
        if not _within_window(pub):
            continue
        url = entry.get("link", "")
        title = entry.get("title", url)
        if not url:
            continue
        articles.append(Article(title=title.strip(), url=url, source=source_name, source_url=source_url, published=pub))

    log.info("%s (rss): %d new article(s)", source_name, len(articles))
    return articles


# ── Scrape fetcher ────────────────────────────────────────────────────────────

_DATE_FORMATS = [
    "%B %d, %Y",    # January 5, 2025
    "%b %d, %Y",    # Jan 5, 2025
    "%d %B %Y",     # 5 January 2025
    "%Y-%m-%d",     # 2025-01-05
    "%d/%m/%Y",     # 05/01/2025
    "%m/%d/%Y",     # 01/05/2025
    "%B %Y",        # January 2025
]


def _parse_date_text(text: str) -> datetime | None:
    text = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            naive = datetime.strptime(text, fmt)
            return naive.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    # ISO 8601 with T separator
    for suffix in ("Z", "+00:00"):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


def _find_date_near(el: Any, date_selector: str) -> datetime | None:
    """
    Look for a date element near `el`: first in its parents, then siblings,
    then children — using the CSS selector as a tag name / class hint.
    """
    candidates: list[Any] = []

    # Check within the element itself
    candidates += el.select(date_selector)
    # Walk up to 3 parent levels
    parent = el.parent
    for _ in range(3):
        if parent is None:
            break
        candidates += parent.select(date_selector)
        parent = parent.parent

    for c in candidates:
        dt = _parse_date_text(c.get_text())
        if dt:
            return dt
    return None


def fetch_scrape(source_name: str, source_url: str, selectors: dict, listing_url: str = "") -> list[Article]:
    article_sel = selectors.get("articles", "a")
    date_sel = selectors.get("date", "")
    custom_fmt = selectors.get("date_format")

    try:
        r = httpx.get(
            source_url,
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": "blogaggregate/1.0"},
        )
        r.raise_for_status()
    except Exception as exc:
        log.error("%s: fetch failed — %s", source_name, exc)
        return []

    soup = BeautifulSoup(r.text, "lxml")
    link_els = soup.select(article_sel)

    # When falling back to default "a" selector, discard nav/anchor/external links
    using_default_sel = not selectors.get("articles")
    if using_default_sel:
        link_els = [el for el in link_els if _is_article_href(el.get("href", ""))]

    if not link_els:
        log.warning("%s: selector %r matched 0 elements", source_name, article_sel)
        return []

    articles = []
    for el in link_els:
        href = el.get("href", "")
        if not href:
            continue
        url = urljoin(source_url, href)
        title = el.get_text(strip=True) or url

        pub: datetime | None = None
        if date_sel:
            if custom_fmt and custom_fmt.lower() != "iso":
                # use only the specified format
                date_el = el.find_next(lambda t: True) or el
                raw = date_el.get_text(strip=True) if date_el else ""
                try:
                    pub = datetime.strptime(raw, custom_fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    pub = _find_date_near(el, date_sel)
            else:
                pub = _find_date_near(el, date_sel)

        if not _within_window(pub):
            continue
        articles.append(Article(title=title, url=url, source=source_name, source_url=listing_url or source_url, published=pub))

    log.info("%s (scrape): %d new article(s)", source_name, len(articles))
    return articles


# ── Auto-resolve and dispatch ─────────────────────────────────────────────────

def _is_article_href(href: str) -> bool:
    """Filter out nav/anchor/external links when no selectors are configured."""
    if href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return False
    path = urlparse(href).path.strip("/")
    return len(path.split("/")) >= 2


def fetch_source(source: dict) -> list[Article]:
    if source.get("disabled"):
        log.info("%s: disabled, skipping", source.get("name"))
        return []

    name = source["name"]
    url = source["url"]
    src_type = source.get("type", "auto")
    selectors = source.get("selectors", {})

    if src_type == "rss":
        feed_url = source.get("rss_url", url)
        return fetch_rss(name, feed_url, source_url=url)

    if src_type == "scrape":
        return fetch_scrape(name, url, selectors, listing_url=url)

    # auto: try RSS detection first
    log.info("%s: detecting feed type...", name)
    feed_url = rss_detector.detect(url)
    if feed_url:
        log.info("%s: found feed at %s", name, feed_url)
        return fetch_rss(name, feed_url, source_url=url)

    log.info("%s: no RSS found, falling back to scrape", name)
    return fetch_scrape(name, url, selectors, listing_url=url)
