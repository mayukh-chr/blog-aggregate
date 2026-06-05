"""
Persists seen article URLs to data/seen_urls.json so we never notify twice.
Format: { url: iso_timestamp_when_seen, ... }
URLs older than PRUNE_DAYS are dropped on each save.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

PRUNE_DAYS = 30
_DATA_FILE = Path(__file__).parent.parent / "data" / "seen_urls.json"


def _load() -> dict[str, str]:
    try:
        return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, str]) -> None:
    _DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prune(data: dict[str, str]) -> dict[str, str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)
    kept = {}
    for url, ts in data.items():
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                kept[url] = ts
        except ValueError:
            pass  # drop malformed entries
    return kept


def is_seen(url: str) -> bool:
    return url in _load()


def filter_unseen(urls: list[str]) -> list[str]:
    seen = _load()
    return [u for u in urls if u not in seen]


def mark_seen(urls: list[str]) -> None:
    if not urls:
        return
    data = _load()
    ts = _now_iso()
    for url in urls:
        data[url] = ts
    data = _prune(data)
    _save(data)
    log.info("Marked %d URL(s) as seen (total tracked: %d)", len(urls), len(data))
