from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from src.fetcher import Article


@dataclass
class Digest:
    articles: list[Article] = field(default_factory=list)

    def grouped(self) -> dict[str, list[Article]]:
        groups: dict[str, list[Article]] = {}
        for a in self.articles:
            groups.setdefault(a.source, []).append(a)
        return groups

    def is_empty(self) -> bool:
        return not self.articles


class BaseNotifier(ABC):
    @abstractmethod
    def send(self, digest: Digest) -> None: ...
