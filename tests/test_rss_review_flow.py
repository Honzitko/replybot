from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import x
from news_library import NewsLibrary


def _sample_item() -> dict:
    return {
        "id": "item-1",
        "title": "Example entry",
        "link": "https://example.com/item-1",
        "summary": "A short teaser",
        "published": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc).isoformat(),
    }


def test_generate_draft_from_selected_news_item(monkeypatch):
    app = x.App.__new__(x.App)
    app.news_library = MagicMock()
    app.ingest_generated_draft = MagicMock(return_value={"id": "draft-1"})
    app._collect_config = MagicMock(return_value={"rss_persona_text": "Persona"})
    app._refresh_news_items = MagicMock()
    app._news_items_by_key = {}

    item = _sample_item()
    app._get_selected_news_item = MagicMock(return_value=item)

    generator = MagicMock(return_value=("Generated body", None))
    monkeypatch.setattr(x, "generate_post_from_rss", generator)

    result = x.App._news_generate_draft(app)

    generator.assert_called_once_with(item, {"rss_persona_text": "Persona"})
    app.ingest_generated_draft.assert_called_once_with(
        "Generated body", rss_item=item, source="rss"
    )
    app.news_library.mark_processed.assert_called_once_with(item)
    app._refresh_news_items.assert_called_once()
    assert result == {"id": "draft-1"}


def test_generate_draft_no_record_keeps_status(monkeypatch):
    app = x.App.__new__(x.App)
    app.news_library = MagicMock()
    app.ingest_generated_draft = MagicMock(return_value=None)
    app._collect_config = MagicMock(return_value={})
    app._refresh_news_items = MagicMock()

    item = _sample_item()

    generator = MagicMock(return_value=("", None))
    monkeypatch.setattr(x, "generate_post_from_rss", generator)

    result = x.App._generate_draft_from_news_item(app, item)

    generator.assert_called_once_with(item, {})
    app.ingest_generated_draft.assert_called_once_with("", rss_item=item, source="rss")
    app.news_library.mark_processed.assert_not_called()
    app._refresh_news_items.assert_called_once()
    assert result is None


def test_news_library_status_persistence(tmp_path):
    path = tmp_path / "news.json"
    library = NewsLibrary(str(path))

    item = _sample_item()
    assert library.add_items([item]) == 1

    stored = library.get_items()[0]
    assert stored["status"] == "new"

    library.mark_processed(stored)
    assert library.get_items()[0]["status"] == "processed"

    library.mark_ignored(stored)
    assert library.get_items()[0]["status"] == "ignored"

    library.reset_status(stored)
    assert library.get_items()[0]["status"] == "new"

    reloaded = NewsLibrary(str(path))
    persisted = reloaded.get_items()[0]
    assert persisted["status"] == "new"

    with pytest.raises(ValueError):
        library.set_item_status(stored, "invalid")
