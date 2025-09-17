import json
from pathlib import Path

from post_library import PostLibrary, store_generated_draft


def test_post_library_migrates_legacy_strings(tmp_path):
    path = Path(tmp_path) / "posts.json"
    path.write_text(json.dumps(["Legacy one", "Legacy two"]))

    library = PostLibrary(str(path))
    entries = library.get_entries()

    assert [entry["text"] for entry in entries] == ["Legacy one", "Legacy two"]
    assert all(entry["status"] == "draft" for entry in entries)
    assert all(entry["source"] == "manual" for entry in entries)
    assert all(entry["created_at"] for entry in entries)
    assert all(entry["id"] for entry in entries)

    on_disk = json.loads(path.read_text())
    assert isinstance(on_disk, list)
    assert isinstance(on_disk[0], dict)
    assert on_disk[0]["text"] == "Legacy one"


def test_post_library_adds_and_updates_metadata(tmp_path):
    path = Path(tmp_path) / "posts.json"
    library = PostLibrary(str(path))

    record = library.add_post(
        "Draft text",
        source="Example Feed",
        rss_link="https://example.com/article",
        status="used",
        metadata={"rss": {"id": "item-1"}},
    )

    assert record is not None
    assert record["status"] == "used"
    assert record["rss_link"] == "https://example.com/article"
    assert record["metadata"]["rss"]["id"] == "item-1"

    fetched = library.get_post_by_id(record["id"])
    assert fetched is not None
    assert fetched["text"] == "Draft text"

    updated = library.set_status(record["id"], "archived")
    assert updated is not None
    assert updated["status"] == "archived"

    library.delete_post(record["id"])
    assert library.get_entries() == []


def test_store_generated_draft_links_queue(tmp_path):
    path = Path(tmp_path) / "posts.json"
    library = PostLibrary(str(path))

    class DummyQueue:
        def __init__(self) -> None:
            self.saved = []

        def push(self, draft):
            self.saved.append(draft)

    queue = DummyQueue()

    rss_item = {"id": "item-42", "link": "https://feed.local/item", "source": "Feed"}
    record = store_generated_draft(library, queue, "Generated post", rss_item=rss_item)

    assert record is not None
    assert record["source"] == "Feed"
    assert record["rss_link"] == "https://feed.local/item"
    assert queue.saved and queue.saved[0]["id"] == record["id"]
    assert queue.saved[0]["metadata"]["rss"]["id"] == "item-42"
