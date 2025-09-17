from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

DraftRecord = Dict[str, Any]

RECORD_KEYS: Tuple[str, ...] = (
    "id",
    "text",
    "source",
    "created_at",
    "rss_link",
    "status",
    "metadata",
)
VALID_STATUSES = {"draft", "used", "archived"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_source(value: Any) -> str:
    if value is None:
        return "manual"
    source = str(value).strip()
    return source or "manual"


def _clean_link(value: Any) -> Optional[str]:
    if value is None:
        return None
    link = str(value).strip()
    return link or None


def _coerce_created_at(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc)
        return dt.isoformat()
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return _now_iso()


def _coerce_status(value: Any, *, strict: bool = False) -> str:
    if value is None:
        status = "draft"
    else:
        status = str(value).strip().lower()
    if status in VALID_STATUSES:
        return status
    if strict:
        raise ValueError(f"Invalid draft status: {value!r}")
    return "draft"


def _prepare_metadata(value: Any, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(value, dict):
        metadata: Dict[str, Any] = copy.deepcopy(value)
    else:
        metadata = {}
    if extra:
        legacy = metadata.setdefault("legacy", {})
        legacy.update(extra)
    return metadata


def create_post_record(
    text: str,
    *,
    source: Any = None,
    created_at: Any = None,
    rss_link: Any = None,
    status: Any = None,
    metadata: Any = None,
    record_id: Any = None,
) -> DraftRecord:
    clean_text = str(text or "").strip()
    record = {
        "id": str(record_id or uuid.uuid4().hex),
        "text": clean_text,
        "source": _clean_source(source),
        "created_at": _coerce_created_at(created_at),
        "rss_link": _clean_link(rss_link),
        "status": _coerce_status(status, strict=False),
        "metadata": _prepare_metadata(metadata),
    }
    return record


class PostLibrary:
    """Manage stored posts persisted to a JSON file."""

    def __init__(self, path: str | None = None) -> None:
        base_dir = os.path.abspath(os.path.dirname(__file__))
        if path is None:
            base_dir = os.path.join(base_dir, "configs")
            os.makedirs(base_dir, exist_ok=True)
            path = os.path.join(base_dir, "posts.json")
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.posts: List[DraftRecord] = []
        self.load()

    def load(self) -> List[DraftRecord]:
        """Load posts from ``self.path`` and migrate legacy data."""

        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            self.posts = []
            self.save()
            return []
        except Exception:
            self.posts = []
            return []

        if not isinstance(data, list):
            self.posts = []
            return []

        migrated: List[DraftRecord] = []
        needs_save = False
        for item in data:
            record, changed = self._normalise_entry(item)
            migrated.append(record)
            needs_save = needs_save or changed

        self.posts = migrated
        if needs_save:
            self.save()
        return self.get_entries()

    def save(self) -> None:
        """Persist current posts to ``self.path``."""

        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.posts, fh, ensure_ascii=False, indent=2)

    def get_posts(self) -> List[str]:
        return [record.get("text", "") for record in self.posts]

    def get_entries(self) -> List[DraftRecord]:
        return [copy.deepcopy(record) for record in self.posts]

    def get_post_by_id(self, record_id: str) -> Optional[DraftRecord]:
        idx = self._resolve_index(record_id)
        if idx is None:
            return None
        return copy.deepcopy(self.posts[idx])

    def add_post(
        self,
        text: str,
        *,
        source: Any = None,
        rss_link: Any = None,
        status: Any = None,
        metadata: Any = None,
        created_at: Any = None,
        record_id: Any = None,
    ) -> Optional[DraftRecord]:
        record = create_post_record(
            text,
            source=source,
            rss_link=rss_link,
            status=status,
            metadata=metadata,
            created_at=created_at,
            record_id=record_id,
        )
        if not record["text"]:
            return None
        self.posts.append(record)
        self.save()
        return copy.deepcopy(record)

    def update_post(self, index: int | str, text: Optional[str] = None, **updates: Any) -> Optional[DraftRecord]:
        idx = self._resolve_index(index)
        if idx is None:
            return None

        record = self.posts[idx]
        changed = False

        if text is not None:
            clean = str(text).strip()
            if clean and clean != record.get("text"):
                record["text"] = clean
                changed = True

        if "source" in updates:
            new_source = _clean_source(updates["source"])
            if new_source != record.get("source"):
                record["source"] = new_source
                changed = True

        if "rss_link" in updates:
            new_link = _clean_link(updates["rss_link"])
            if new_link != record.get("rss_link"):
                record["rss_link"] = new_link
                changed = True

        if "status" in updates:
            new_status = _coerce_status(updates["status"], strict=False)
            if new_status != record.get("status"):
                record["status"] = new_status
                changed = True

        if "metadata" in updates and isinstance(updates["metadata"], dict):
            new_meta = _prepare_metadata(updates["metadata"])
            if new_meta != record.get("metadata"):
                record["metadata"] = new_meta
                changed = True

        if "created_at" in updates:
            new_created = _coerce_created_at(updates["created_at"])
            if new_created != record.get("created_at"):
                record["created_at"] = new_created
                changed = True

        if changed:
            self.save()
        return copy.deepcopy(record)

    def set_status(self, index: int | str, status: str) -> Optional[DraftRecord]:
        validated = _coerce_status(status, strict=True)
        return self.update_post(index, status=validated)

    def delete_post(self, index: int | str) -> None:
        idx = self._resolve_index(index)
        if idx is None:
            return
        del self.posts[idx]
        self.save()

    # ------------------------------------------------------------------
    def _normalise_entry(self, entry: Any) -> Tuple[DraftRecord, bool]:
        if isinstance(entry, str):
            return create_post_record(entry), True
        if not isinstance(entry, dict):
            return create_post_record(str(entry)), True

        extra = {k: entry[k] for k in entry.keys() - set(RECORD_KEYS)}
        metadata_in = entry.get("metadata")
        metadata = _prepare_metadata(metadata_in, extra=extra if extra else None)
        record = create_post_record(
            entry.get("text", ""),
            source=entry.get("source"),
            created_at=entry.get("created_at"),
            rss_link=entry.get("rss_link"),
            status=entry.get("status"),
            metadata=metadata,
            record_id=entry.get("id"),
        )

        changed = bool(extra)
        if not changed:
            for key in RECORD_KEYS:
                original = entry.get(key)
                if key == "metadata":
                    original = metadata
                if record.get(key) != original:
                    changed = True
                    break
        return record, changed

    def _resolve_index(self, index: int | str) -> Optional[int]:
        if isinstance(index, int):
            if 0 <= index < len(self.posts):
                return index
            return None
        if isinstance(index, str):
            for idx, record in enumerate(self.posts):
                if record.get("id") == index:
                    return idx
        return None


def store_generated_draft(
    library: "PostLibrary",
    queue,
    text: str,
    *,
    rss_item: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
) -> Optional[DraftRecord]:
    """Persist ``text`` with provenance and queue it for review."""

    rss_item = rss_item or {}
    metadata: Dict[str, Any] = {}
    if rss_item:
        metadata["rss"] = copy.deepcopy(rss_item)

    record = library.add_post(
        text,
        source=source if source is not None else rss_item.get("source", "rss"),
        rss_link=rss_item.get("link") or rss_item.get("url"),
        metadata=metadata if metadata else None,
    )
    if record is None:
        return None

    try:
        if queue is not None:
            queue.push(record)
    except Exception:
        pass
    return record
