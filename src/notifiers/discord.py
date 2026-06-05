"""
Sends the daily digest to a Discord channel via an incoming webhook.

Discord limits:
  - 10 embeds per message payload
  - 6000 chars total across all embeds in one payload
  - 4096 chars per embed description

We batch into multiple requests when we exceed 10 embeds.
"""

from __future__ import annotations

import logging
import os
import httpx

from src.fetcher import Article
from src.notifiers.base import BaseNotifier, Digest

log = logging.getLogger(__name__)

_MAX_EMBEDS_PER_MSG = 10
_DISCORD_BLUE = 0x5865F2


def _fmt_date(article: Article) -> str:
    if article.published:
        return article.published.strftime("%b %d")
    return "?"


def _build_embed(source: str, articles: list[Article]) -> dict:
    lines = [
        f"[{a.title}]({a.url})  ·  {_fmt_date(a)}"
        for a in articles
    ]
    description = "\n".join(lines)
    # truncate if over Discord's 4096 char embed description limit
    if len(description) > 4000:
        description = description[:3997] + "..."
    return {
        "title": source,
        "description": description,
        "color": _DISCORD_BLUE,
    }


def _send_payload(webhook_url: str, payload: dict, client: httpx.Client) -> None:
    r = client.post(webhook_url, json=payload, timeout=15)
    if r.status_code not in (200, 204):
        log.error("Discord webhook returned %d: %s", r.status_code, r.text[:200])
    else:
        log.info("Sent Discord payload with %d embed(s)", len(payload.get("embeds", [])))


class DiscordNotifier(BaseNotifier):
    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or os.environ["DISCORD_WEBHOOK_URL"]

    def send(self, digest: Digest) -> None:
        if digest.is_empty():
            log.info("Digest is empty — nothing to send.")
            return

        grouped = digest.grouped()
        embeds = [_build_embed(source, articles) for source, articles in grouped.items()]

        with httpx.Client() as client:
            # batch into chunks of _MAX_EMBEDS_PER_MSG
            for i in range(0, len(embeds), _MAX_EMBEDS_PER_MSG):
                batch = embeds[i : i + _MAX_EMBEDS_PER_MSG]
                is_first = i == 0
                payload: dict = {"embeds": batch}
                if is_first:
                    payload["content"] = (
                        f"**Daily digest — {len(digest.articles)} new post(s) "
                        f"across {len(grouped)} source(s)**"
                    )
                _send_payload(self.webhook_url, payload, client)
