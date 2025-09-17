from __future__ import annotations

from datetime import datetime, timezone

from news_library import NewsLibrary
from rss_ingestor import RSSIngestor


SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <guid>item-1</guid>
      <title>First entry</title>
      <link>https://example.com/first</link>
      <pubDate>Tue, 02 Jan 2024 12:00:00 GMT</pubDate>
      <description>First summary</description>
    </item>
    <item>
      <guid>item-2</guid>
      <title>Second entry</title>
      <link>https://example.com/second</link>
      <pubDate>Mon, 01 Jan 2024 08:00:00 GMT</pubDate>
      <description>Second summary</description>
    </item>
  </channel>
</rss>
"""


def test_news_library_deduplicates(tmp_path):
    path = tmp_path / "news.json"
    library = NewsLibrary(str(path))

    item = {
        "id": "item-1",
        "title": "First entry",
        "summary": "First summary",
        "link": "https://example.com/first",
        "published": datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc).isoformat(),
    }

    assert library.add_items([item]) == 1
    assert library.add_items([dict(item)]) == 0

    stored = library.get_items()
    assert len(stored) == 1
    assert stored[0]["id"] == "item-1"

    # Reload from disk to ensure persistence
    library_reloaded = NewsLibrary(str(path))
    assert library_reloaded.get_items()[0]["id"] == "item-1"


def test_rss_ingestor_fetch_once(tmp_path):
    path = tmp_path / "news.json"
    library = NewsLibrary(str(path))

    def fake_fetcher(url: str) -> str:
        assert url == "https://example.com/feed"
        return SAMPLE_FEED

    ingestor = RSSIngestor(
        ["https://example.com/feed"],
        library,
        interval_seconds=60,
        fetcher=fake_fetcher,
    )

    items = ingestor.fetch_once()
    assert len(items) == 2
    assert {item["id"] for item in items} == {"item-1", "item-2"}
    assert all(item["link"].startswith("https://example.com/") for item in items)
    assert all("summary" in item for item in items)

    inserted = library.add_items(items)
    assert inserted == 2

    # Items should be sorted newest first
    stored = library.get_items()
    assert stored[0]["id"] == "item-1"
    assert stored[1]["id"] == "item-2"

    # Adding the same items again should be ignored
    assert library.add_items(items) == 0

    library_reloaded = NewsLibrary(str(path))
    assert len(library_reloaded.get_items()) == 2
