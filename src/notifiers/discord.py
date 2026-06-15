"""
Sends the daily digest to a Discord channel via an incoming webhook.

Discord limits:
  - 10 embeds per message payload
  - 6000 chars total across all embeds in one payload
  - 4096 chars per embed description

We enforce both limits by splitting payloads when approaching 5000 total chars
(leaving headroom for content field and embed metadata overhead).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
import httpx

from src.fetcher import Article
from src.notifiers.base import BaseNotifier, Digest

log = logging.getLogger(__name__)

_MAX_EMBEDS_PER_MSG = 10
_MAX_CHARS_PER_PAYLOAD = 5000
_MAX_ARTICLES_PER_SOURCE = 5

# Rotating palette — visually distinct, works on dark and light Discord themes
_COLORS = [
    0xE06C75,  # red
    0x61AFEF,  # blue
    0x98C379,  # green
    0xE5C07B,  # yellow
    0xC678DD,  # purple
    0x56B6C2,  # cyan
    0xD19A66,  # orange
    0xABB2BF,  # grey
]


def _color_for(source: str) -> int:
    return _COLORS[hash(source) % len(_COLORS)]


def _fmt_date(article: Article) -> str:
    if article.published:
        return article.published.strftime("%b %d")
    return "?"


def _embed_chars(embed: dict) -> int:
    return len(embed.get("title", "")) + len(embed.get("description", ""))


def _build_embed(source: str, articles: list[Article]) -> dict:
    capped = articles[:_MAX_ARTICLES_PER_SOURCE]
    lines = [f"· [{a.title}]({a.url})  `{_fmt_date(a)}`" for a in capped]
    if len(articles) > _MAX_ARTICLES_PER_SOURCE:
        overflow = len(articles) - _MAX_ARTICLES_PER_SOURCE
        source_url = articles[0].source_url
        lines.append(f"[+{overflow} more]({source_url})")
    description = "\n".join(lines)
    if len(description) > 4000:
        description = description[:3997] + "..."
    return {
        "title": source,
        "url": articles[0].source_url,
        "description": description,
        "color": _color_for(source),
    }


def _send_payload(webhook_url: str, payload: dict, client: httpx.Client) -> None:
    r = client.post(webhook_url, json=payload, timeout=15)
    if r.status_code not in (200, 204):
        raise RuntimeError(
            f"Discord webhook returned {r.status_code}: {r.text[:200]}"
        )
    log.info("Sent Discord payload with %d embed(s)", len(payload.get("embeds", [])))


def _batch_embeds(embeds: list[dict]) -> list[list[dict]]:
    """Split embeds into payloads that respect both the count and char limits."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0

    for embed in embeds:
        chars = _embed_chars(embed)
        if current and (
            len(current) >= _MAX_EMBEDS_PER_MSG
            or current_chars + chars > _MAX_CHARS_PER_PAYLOAD
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(embed)
        current_chars += chars

    if current:
        batches.append(current)
    return batches


class DiscordNotifier(BaseNotifier):
    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or os.environ["DISCORD_WEBHOOK_URL"]

    def send(self, digest: Digest) -> None:
        if digest.is_empty():
            log.info("Digest is empty — nothing to send.")
            return

        grouped = digest.grouped()
        embeds = [_build_embed(source, articles) for source, articles in grouped.items()]
        batches = _batch_embeds(embeds)

        now = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
        with httpx.Client() as client:
            for i, batch in enumerate(batches):
                payload: dict = {"embeds": batch}
                if i == 0:
                    payload["content"] = (
                        f"## Daily digest\n"
                        f"`{len(digest.articles)} new post(s)` across "
                        f"`{len(grouped)} source(s)` · {now}"
                    )
                _send_payload(self.webhook_url, payload, client)
