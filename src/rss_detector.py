"""
Attempts to find an RSS/Atom feed URL for a given site URL.
Returns the feed URL string, or None if none is found.
"""

from urllib.parse import urljoin, urlparse
import httpx
import feedparser
from bs4 import BeautifulSoup

_COMMON_FEED_PATHS = [
    "/feed",
    "/feed.xml",
    "/rss",
    "/rss.xml",
    "/atom.xml",
    "/feed/atom",
    "/blog/feed",
    "/blog/rss",
    "/blog/feed.xml",
    "/index.xml",
]

_FEED_CONTENT_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
}


def _base(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _is_valid_feed(url: str, client: httpx.Client) -> bool:
    try:
        r = client.get(url, follow_redirects=True, timeout=10)
        if r.status_code != 200:
            return False
        ct = r.headers.get("content-type", "")
        if any(ft in ct for ft in _FEED_CONTENT_TYPES):
            return True
        parsed = feedparser.parse(r.text)
        return bool(parsed.entries)
    except Exception:
        return False


def _scan_html_link_tags(url: str, client: httpx.Client) -> str | None:
    try:
        r = client.get(url, follow_redirects=True, timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        for link in soup.find_all("link", rel=lambda v: v and "alternate" in v):
            t = link.get("type", "")
            if "rss" in t or "atom" in t or "xml" in t:
                href = link.get("href", "")
                if href:
                    return urljoin(url, href)
    except Exception:
        pass
    return None


def detect(url: str) -> str | None:
    """Return a feed URL for *url*, or None."""
    with httpx.Client(headers={"User-Agent": "blogaggregate/1.0"}) as client:
        # 1. Check common path patterns on the base domain
        base = _base(url)
        for path in _COMMON_FEED_PATHS:
            candidate = base + path
            if _is_valid_feed(candidate, client):
                return candidate

        # 2. Scan <link rel="alternate"> tags on the actual page
        feed_url = _scan_html_link_tags(url, client)
        if feed_url and _is_valid_feed(feed_url, client):
            return feed_url

    return None
