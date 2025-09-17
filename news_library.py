"""Persistent store for RSS items with simple deduplication helpers."""

from __future__ import annotations

import json
import os
import threading
from typing import Dict, Iterable, List


class NewsLibrary:
    """Manage a collection of RSS items persisted to a JSON file.

    The library keeps track of previously ingested articles using a combination
    of their identifier and published timestamp.  This makes it resilient
    against feeds that occasionally recycle identifiers but emit a new
    timestamp when an article is updated.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        base_dir = os.path.abspath(os.path.dirname(__file__))
        if path is None:
            base_dir = os.path.join(base_dir, "configs")
            os.makedirs(base_dir, exist_ok=True)
            path = os.path.join(base_dir, "news.json")

        self.path = str(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        self._lock = threading.Lock()
        self._items: List[Dict] = []
        self._seen: set[str] = set()
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> List[Dict]:
        """Load stored items from :attr:`path`.  Missing files are created."""

        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                self._items = list(data)
            else:  # pragma: no cover - corrupted file
                self._items = []
        except FileNotFoundError:
            self._items = []
            self.save()
        except Exception:  # pragma: no cover - unexpected parse failure
            self._items = []

        self._rebuild_index()
        return list(self._items)

    def save(self) -> None:
        """Persist the current items to :attr:`path`."""

        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._items, fh, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    def _rebuild_index(self) -> None:
        self._seen = {self._dedup_key(item) for item in self._items}

    def _dedup_key(self, item: Dict) -> str:
        ident = str(item.get("id") or item.get("link") or item.get("title") or "").strip()
        published = item.get("published") or ""
        return f"{ident}|{published}"

    # ------------------------------------------------------------------
    def get_items(self) -> List[Dict]:
        """Return a shallow copy of stored articles."""

        with self._lock:
            return list(self._items)

    def add_items(self, items: Iterable[Dict]) -> int:
        """Add ``items`` to the library, skipping entries already seen.

        Returns the number of newly inserted items.
        """

        inserted = 0
        with self._lock:
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = self._dedup_key(item)
                if key in self._seen:
                    continue
                self._seen.add(key)
                self._items.append(dict(item))
                inserted += 1

            if inserted:
                self._items.sort(
                    key=lambda it: str(it.get("published") or ""), reverse=True
                )
                self.save()

        return inserted

