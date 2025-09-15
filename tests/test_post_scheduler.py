import time
from datetime import datetime, timedelta
import pathlib
import sys

# Allow importing modules from the repository root when the tests are executed
# from within the ``tests`` directory.
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from post_scheduler import PostScheduler


def test_posts_dispatched_in_order_and_interval():
    scheduler = PostScheduler(interval=0.01)
    results: list[str] = []
    timestamps: list[float] = []
    scheduler.on_post_ready = lambda post: (results.append(post), timestamps.append(time.time()))
    scheduler.start()
    scheduler.enqueue(["a", "b"])
    time.sleep(0.05)
    scheduler.stop()
    assert results == ["a", "b"]
    assert timestamps[1] - timestamps[0] >= 0.009


def test_sleep_window_pause_and_resume():
    now = datetime.now()
    sleep_start = (now - timedelta(seconds=0.01)).time()
    sleep_end = (now + timedelta(seconds=0.05)).time()
    scheduler = PostScheduler(interval=0.01, sleep_window=(sleep_start, sleep_end))
    events: list[str] = []
    results: list[str] = []
    scheduler.on_pause = lambda: events.append("pause")
    scheduler.on_resume = lambda: events.append("resume")
    scheduler.on_post_ready = lambda post: results.append(post)
    scheduler.start()
    scheduler.enqueue(["p1"])
    time.sleep(0.02)
    assert events == ["pause"]
    time.sleep(0.1)
    scheduler.stop()
    assert events == ["pause", "resume"]
    assert results == ["p1"]
