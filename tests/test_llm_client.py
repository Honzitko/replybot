import pytest

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import llm_client


def test_load_llm_settings_requires_api_key():
    settings = {"rss_persona_text": "Voice", "rss_max_post_length": 200}
    assert llm_client.load_llm_settings(settings) is None


def test_load_llm_settings_parses_fields():
    settings = {
        "openai_api_key": "  key  ",
        "rss_persona_text": "Confident operator voice",
        "rss_max_post_length": "320",
        "openai_model": "gpt-test",
        "openai_base_url": "https://example.com/api",
        "openai_temperature": "0.42",
        "openai_timeout": "18",
        "rss_persona_tone": "Calm and direct.",
    }
    cfg = llm_client.load_llm_settings(settings)
    assert cfg is not None
    assert cfg["api_key"] == "key"
    assert cfg["persona_text"] == "Confident operator voice"
    assert cfg["max_length"] == 320
    assert cfg["model"] == "gpt-test"
    assert cfg["base_url"] == "https://example.com/api"
    assert cfg["tone"] == "Calm and direct."
    assert cfg["temperature"] == pytest.approx(0.42)
    assert cfg["timeout"] == pytest.approx(18.0)


def test_summarize_rss_item_builds_prompt(monkeypatch):
    captured = {}

    class DummyResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Generated post"}}]}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        captured["timeout"] = timeout
        return DummyResponse(json)

    class DummyRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            return fake_post(url, headers, json, timeout)

    monkeypatch.setattr(llm_client, "requests", DummyRequests)

    persona_settings = {
        "api_key": "secret",
        "persona_text": "Energetic builder voice that highlights the takeaway.",
        "max_length": 190,
        "model": "gpt-4o-mini",
        "base_url": "https://example.com/v1",
        "tone": "Direct yet friendly.",
        "temperature": 0.2,
        "timeout": 12,
    }
    rss_item = {
        "title": "Launch announced",
        "summary": "A startup released a new product",
        "link": "https://news.example.com/item",
        "source": "NewsWire",
    }

    text = llm_client.summarize_rss_item(rss_item, persona_settings)
    assert text == "Generated post"

    payload = captured["payload"]
    assert payload["model"] == "gpt-4o-mini"
    assert payload["messages"][1]["content"].count("Launch announced") == 1
    assert "Energetic builder voice" in payload["messages"][1]["content"]
    assert "190" in payload["messages"][1]["content"]
    assert "Direct yet friendly." in payload["messages"][1]["content"]
    assert "https://news.example.com/item" in payload["messages"][1]["content"]
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["timeout"] == 12
