"""Background thread that periodically ingests RSS feeds."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from xml.etree import ElementTree as ET

try:  # pragma: no cover - keep optional dependency soft
    import requests
except Exception:  # pragma: no cover - requests may be unavailable
    requests = None  # type: ignore[assignment]

DEFAULT_INTERVAL_SECONDS = 4 * 60 * 60  # 4 hours


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _find_direct_text(node: ET.Element, names: Iterable[str]) -> str:
    targets = set(names)
    for child in node:
        if _local_name(child.tag) in targets:
            text = child.text or ""
            text = text.strip()
            if text:
                return text
    return ""


def _find_any_text(node: ET.Element, names: Iterable[str]) -> str:
    targets = set(names)
    for child in node.iter():
        if _local_name(child.tag) in targets:
            text = child.text or ""
            text = text.strip()
            if text:
                return text
    return ""


def _find_link(node: ET.Element) -> str:
    for child in node.iter():
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href") if hasattr(child, "attrib") else None
        if href:
            return href.strip()
        text = child.text or ""
        text = text.strip()
        if text:
            return text
    return ""


def _parse_datetime(value: str | None) -> Optional[datetime]:
    if not value:
        return None

    value = value.strip()
    if not value:
        return None

    for parser in (_from_isoformat, _from_email_date):
        dt = parser(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    return None


def _from_isoformat(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _from_email_date(value: str) -> Optional[datetime]:
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return dt


def _normalise_entry(entry: ET.Element, source_url: str) -> Dict[str, Any]:
    title = _find_direct_text(entry, {"title"}) or _find_any_text(entry, {"title"})
    summary = _find_direct_text(entry, {"summary", "description"})
    if not summary:
        summary = _find_any_text(entry, {"summary", "description", "content"})

    link = _find_link(entry)
    identifier = _find_direct_text(entry, {"id", "guid"})
    if not identifier:
        identifier = _find_any_text(entry, {"id", "guid"})
    published_raw = _find_direct_text(entry, {"published", "pubDate", "updated"})
    if not published_raw:
        published_raw = _find_any_text(entry, {"published", "pubDate", "updated"})

    published_dt = _parse_datetime(published_raw)
    if published_dt is None:
        published_dt = datetime.now(timezone.utc)
    published_iso = published_dt.isoformat()

    if not identifier:
        identifier = link or f"{title}:{published_iso}"

    return {
        "id": identifier,
        "title": title,
        "summary": summary,
        "link": link,
        "published": published_iso,
        "source": source_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def parse_feed(document: str, source_url: str) -> List[Dict[str, Any]]:
    """Parse ``document`` and return normalised feed entries."""

    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        logging.warning("rss_ingestor: failed to parse feed from %s", source_url)
        return []

    items: List[ET.Element] = []

    channel = root.find("channel")
    if channel is not None:
        items.extend(channel.findall("item"))
    if not items:
        items.extend(root.findall(".//item"))
    if not items:
        items.extend(root.findall(".//{*}entry"))
    if not items:
        items.extend(root.findall("entry"))

    normalised: List[Dict[str, Any]] = []
    for entry in items:
        try:
            normalised.append(_normalise_entry(entry, source_url))
        except Exception:  # pragma: no cover - defensive against malformed feeds
            logging.exception("rss_ingestor: failed to normalise entry from %s", source_url)
    return normalised


class RSSIngestor(threading.Thread):
    """Background worker that polls RSS feeds and stores new entries."""

    def __init__(
        self,
        feeds: Sequence[str],
        library,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        stop_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
        status_callback: Optional[Callable[[Optional[datetime], Optional[datetime], bool], None]] = None,
        fetcher: Optional[Callable[[str], str]] = None,
        request_timeout: float = 10.0,
    ) -> None:
        super().__init__(daemon=True)
        self.feeds = [f for f in feeds if f]
        self.library = library
        self.interval_seconds = max(60.0, float(interval_seconds))
        self.stop_event = stop_event or threading.Event()
        self.pause_event = pause_event
        self.status_callback = status_callback
        self._fetcher = fetcher
        self.request_timeout = request_timeout
        self.last_fetch: Optional[datetime] = None
        self.next_fetch: Optional[datetime] = None
        self._was_paused = False

    # ------------------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - exercised indirectly in tests
        self.next_fetch = datetime.now(timezone.utc)
        self._notify_status(paused=False)

        while not self.stop_event.is_set():
            if self.pause_event is not None and self.pause_event.is_set():
                if not self._was_paused:
                    self._notify_status(paused=True)
                    self._was_paused = True
                if self.stop_event.wait(1.0):
                    break
                continue

            self._was_paused = False
            self._ingest_once()
            if self.stop_event.wait(self.interval_seconds):
                break

    # ------------------------------------------------------------------
    def _ingest_once(self) -> None:
        items = self.fetch_once()
        if items:
            try:
                self.library.add_items(items)
            except Exception:  # pragma: no cover - persistence errors are rare
                logging.exception("rss_ingestor: failed to persist items")

        self.last_fetch = datetime.now(timezone.utc)
        self.next_fetch = self.last_fetch + timedelta(seconds=self.interval_seconds)
        self._notify_status(paused=False)

    def fetch_once(self) -> List[Dict[str, Any]]:
        """Download and parse each configured feed once."""

        collected: List[Dict[str, Any]] = []
        for url in self.feeds:
            try:
                document = self._download(url)
            except Exception as exc:  # pragma: no cover - network dependent
                logging.warning("rss_ingestor: failed to fetch %s: %s", url, exc)
                continue

            entries = parse_feed(document, url)
            collected.extend(entries)

        return collected

    # ------------------------------------------------------------------
    def _download(self, url: str) -> str:
        if self._fetcher is not None:
            return self._fetcher(url)

        if requests is None:
            raise RuntimeError("requests dependency is not available")

        resp = requests.get(url, timeout=self.request_timeout)
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        return resp.text

    # ------------------------------------------------------------------
    def _notify_status(self, paused: bool) -> None:
        if self.status_callback is None:
            return
        try:
            self.status_callback(self.last_fetch, self.next_fetch, paused)
        except Exception:  # pragma: no cover - UI callback failure should not kill thread
            logging.exception("rss_ingestor: status callback failed")

