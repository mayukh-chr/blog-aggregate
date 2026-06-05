#!/usr/bin/env python3
"""
Blog aggregator — daily digest runner.

Usage:
    python main.py                  # normal run
    python main.py --dry-run        # fetch + print digest, don't notify or persist
    python main.py --source "Name"  # run against one source only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.fetcher import fetch_source, Article
from src.notifiers.base import Digest
from src.notifiers.discord import DiscordNotifier
from src import store

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def load_sources(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def run(sources: list[dict], dry_run: bool = False) -> None:
    all_articles: list[Article] = []

    for source in sources:
        try:
            articles = fetch_source(source)
        except Exception as exc:
            log.error("Unhandled error fetching %s: %s", source.get("name"), exc)
            continue

        # Filter out URLs we've already notified about
        new_articles = [a for a in articles if not store.is_seen(a.url)]
        all_articles.extend(new_articles)

    digest = Digest(articles=all_articles)

    if dry_run:
        print(f"\n=== DRY RUN — {len(all_articles)} article(s) ===\n")
        for source_name, arts in digest.grouped().items():
            print(f"  [{source_name}]")
            for a in arts:
                date_str = a.published.strftime("%Y-%m-%d") if a.published else "no date"
                print(f"    • {a.title}  ({date_str})")
                print(f"      {a.url}")
        print()
        return

    if digest.is_empty():
        log.info("No new articles in the last 24 hours. Nothing to send.")
        return

    notifier = DiscordNotifier()
    try:
        notifier.send(digest)
        store.mark_seen([a.url for a in all_articles])
    except Exception as exc:
        log.error("Notification failed — seen URLs NOT updated: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Blog digest runner")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print without notifying")
    parser.add_argument("--source", metavar="NAME", help="Run against a single source by name")
    args = parser.parse_args()

    config_path = Path(__file__).parent / "config" / "sources.yaml"
    sources = load_sources(config_path)

    if args.source:
        sources = [s for s in sources if s["name"].lower() == args.source.lower()]
        if not sources:
            print(f"No source named {args.source!r}. Available:")
            config_sources = load_sources(config_path)
            for s in config_sources:
                print(f"  - {s['name']}")
            sys.exit(1)

    run(sources, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
