"""Persistent store for RSS items with simple deduplication helpers."""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Iterable, List, Optional


class NewsLibrary:
    """Manage a collection of RSS items persisted to a JSON file.

    The library keeps track of previously ingested articles using a combination
    of their identifier and published timestamp.  This makes it resilient
    against feeds that occasionally recycle identifiers but emit a new
    timestamp when an article is updated.
    """

    VALID_STATUSES = {"new", "processed", "ignored"}

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

        self._apply_defaults()
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
                new_item = dict(item)
                self._ensure_status(new_item)
                self._items.append(new_item)
                inserted += 1

            if inserted:
                self._items.sort(
                    key=lambda it: str(it.get("published") or ""), reverse=True
                )
                self.save()

        return inserted

    # ------------------------------------------------------------------
    def set_item_status(self, item: Dict[str, Any], status: str) -> Dict[str, Any]:
        """Update ``item`` to ``status`` and persist the change.

        ``item`` must be one of the dictionaries previously returned by
        :meth:`get_items`.  ``status`` is normalised and validated against
        :data:`VALID_STATUSES`.
        """

        if not isinstance(item, dict):  # pragma: no cover - guard against misuse
            raise TypeError("item must be a mapping")

        normalised_status = str(status or "").strip().lower() or "new"
        if normalised_status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid news item status: {status!r}")

        key = self._dedup_key(item)

        with self._lock:
            target = self._find_item_by_key(key)
            if target is None:
                raise KeyError("News item not found")

            if target.get("status") != normalised_status:
                target["status"] = normalised_status
                self.save()

            return dict(target)

    def mark_processed(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return self.set_item_status(item, "processed")

    def mark_ignored(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return self.set_item_status(item, "ignored")

    def reset_status(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return self.set_item_status(item, "new")

    # ------------------------------------------------------------------
    def _apply_defaults(self) -> None:
        for item in self._items:
            if not isinstance(item, dict):
                continue
            self._ensure_status(item)

    def _ensure_status(self, item: Dict[str, Any]) -> None:
        status = str(item.get("status", "new") or "").strip().lower()
        if status not in self.VALID_STATUSES:
            status = "new"
        item["status"] = status

    def _find_item_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        for existing in self._items:
            if self._dedup_key(existing) == key:
                self._ensure_status(existing)
                return existing
        return None

