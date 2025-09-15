import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta

root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from post_scheduler import PostScheduler


def _dt(hour, minute=0):
    return datetime(2024, 1, 1, hour, minute)


def test_schedule_outside_sleep():
    start = _dt(22)
    end = datetime(2024, 1, 2, 6, 0)
    sched = PostScheduler(start, end)
    now = _dt(12)
    next_time = sched.schedule_next(now, timedelta(hours=1))
    assert sched.night_sleep_start == start
    assert sched.night_sleep_end == end
    assert next_time == now + timedelta(hours=1)


def test_schedule_during_sleep_delays_until_end():
    start = _dt(22)
    end = datetime(2024, 1, 2, 6, 0)
    sched = PostScheduler(start, end)
    now = datetime(2024, 1, 1, 23, 30)
    next_time = sched.schedule_next(now, timedelta(hours=1))
    assert next_time == end + timedelta(hours=1)
