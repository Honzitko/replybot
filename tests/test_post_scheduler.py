
import importlib.util
import pathlib
import sys
import threading

root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("x", root / "x.py")
x = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = x
spec.loader.exec_module(x)

PostScheduler = x.PostScheduler

def test_post_scheduler_pauses_and_resumes():
    pause = threading.Event()
    stop = threading.Event()
    calls = []
    def cb():
        calls.append(pause.is_set())
    ps = PostScheduler(1, pause, stop, cb)
    ps._trigger_post()
    assert calls == [True]
    assert not pause.is_set()

def test_post_scheduler_handles_cancel():
    pause = threading.Event()
    stop = threading.Event()
    def cb():
        raise RuntimeError("cancelled")
    ps = PostScheduler(1, pause, stop, cb)
    ps._trigger_post()
    assert not pause.is_set()


def test_generate_post_from_rss_with_llm(monkeypatch):
    import post_scheduler
    import llm_client

    rss_item = {"title": "AI title"}
    settings = {
        "openai_api_key": "secret",
        "rss_persona_text": "Builder voice",
        "rss_max_post_length": 180,
    }

    captured = {}

    def fake_load(config):
        captured["settings"] = config
        return {"persona_text": "Builder", "api_key": "secret", "max_length": 180}

    def fake_summary(item, persona):
        captured["rss_item"] = item
        captured["persona"] = persona
        return "LLM summary"

    monkeypatch.setattr(post_scheduler, "_llm_client", llm_client)
    monkeypatch.setattr(llm_client, "load_llm_settings", fake_load)
    monkeypatch.setattr(llm_client, "summarize_rss_item", fake_summary)

    text, media = post_scheduler.generate_post_from_rss(rss_item, settings)
    assert text == "LLM summary"
    assert media is None
    assert captured["settings"] is settings
    assert captured["rss_item"] is rss_item
    assert captured["persona"]["api_key"] == "secret"


def test_generate_post_from_rss_llm_failure(monkeypatch, caplog):
    import post_scheduler
    import llm_client

    def fake_load(config):
        return {"persona_text": "Persona", "api_key": "secret", "max_length": 200}

    def fake_summary(item, persona):
        raise RuntimeError("boom")

    monkeypatch.setattr(post_scheduler, "_llm_client", llm_client)
    monkeypatch.setattr(llm_client, "load_llm_settings", fake_load)
    monkeypatch.setattr(llm_client, "summarize_rss_item", fake_summary)

    rss_item = {"title": "Original"}
    with caplog.at_level("ERROR"):
        text, media = post_scheduler.generate_post_from_rss(rss_item, {})

    assert text == "Original"
    assert media is None
    assert any("LLM summarisation failed" in rec.message for rec in caplog.records)


def test_generate_post_from_rss_without_credentials(monkeypatch):
    import post_scheduler

    monkeypatch.setattr(post_scheduler, "requests", None)
    rss_item = {"title": "Fallback"}
    text, media = post_scheduler.generate_post_from_rss(rss_item, {})
    assert text == "Fallback"
    assert media is None

