"""Utilities for scheduling posts with sleep awareness."""

from __future__ import annotations

from collections import deque
from datetime import datetime, time as dtime, timedelta
from threading import Condition, Event, Thread
from typing import Callable, Deque, Iterable, Optional


class PostScheduler:
    """Dispatch posts at regular intervals while observing quiet periods.

    Parameters
    ----------
    interval:
        Seconds between posts.  The scheduler waits this long after delivering a
        post before processing the next one.
    sleep_window:
        Optional tuple of ``datetime.time`` objects describing a period during
        which no posts should be emitted.  When the current time falls inside
        this window the scheduler pauses and invokes :pyattr:`on_pause`.  Once
        the window passes :pyattr:`on_resume` is fired and normal processing
        continues.
    now:
        Callable returning ``datetime`` used to obtain the current time.  Tests
        can supply a custom implementation if they need tighter control over
        the clock.  By default :func:`datetime.now` is used.
    """

    #: Hooks that callers may replace.  They default to simple no-op lambdas so
    #: users can override only what they need.
    on_post_ready: Callable[[str], None]
    on_pause: Callable[[], None]
    on_resume: Callable[[], None]

    def __init__(
        self,
        interval: float,
        sleep_window: Optional[tuple[dtime, dtime]] = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.interval = float(interval)
        self.sleep_window = sleep_window
        self._now = now

        self._queue: Deque[str] = deque()
        self._cv = Condition()
        self._running = Event()
        self._thread: Optional[Thread] = None
        self._paused = False

        # Public hooks
        self.on_post_ready = lambda post: None
        self.on_pause = lambda: None
        self.on_resume = lambda: None

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start processing posts in a background thread."""

        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the scheduler and wait for the worker to exit."""

        self._running.clear()
        with self._cv:
            self._cv.notify_all()
        if self._thread:
            self._thread.join()

    def enqueue(self, posts: Iterable[str]) -> None:
        """Append ``posts`` to the internal queue."""

        with self._cv:
            for p in posts:
                self._queue.append(p)
            self._cv.notify_all()

    # ------------------------------------------------------------------
    def _run(self) -> None:
        while self._running.is_set():
            with self._cv:
                while not self._queue and self._running.is_set():
                    self._cv.wait()
                if not self._running.is_set():
                    return

            # Respect sleep window before popping the next item.  We perform the
            # check outside of the ``with`` block to avoid blocking ``enqueue``.
            while self._running.is_set() and self._in_sleep(self._now()):
                if not self._paused:
                    self._paused = True
                    self.on_pause()
                remaining = self._sleep_remaining(self._now())
                with self._cv:
                    self._cv.wait(timeout=remaining)
            if not self._running.is_set():
                return
            if self._paused:
                self._paused = False
                self.on_resume()

            with self._cv:
                if not self._queue:
                    continue
                post = self._queue.popleft()

            self.on_post_ready(post)

            # Wait for interval before processing the next post.  ``enqueue``
            # can wake the condition which allows immediate processing when
            # interval is ``0``.
            with self._cv:
                self._cv.wait(timeout=self.interval)

    # ------------------------------------------------------------------
    def _in_sleep(self, now: datetime) -> bool:
        if not self.sleep_window:
            return False
        start, end = self.sleep_window
        t = now.time()
        if start <= end:
            return start <= t < end
        return t >= start or t < end

    def _sleep_remaining(self, now: datetime) -> float:
        """Return seconds remaining in the current sleep window."""
        if not self.sleep_window:
            return 0.0
        start, end = self.sleep_window
        today = now.date()
        if start <= end:
            end_dt = datetime.combine(today, end)
            return max(0.0, (end_dt - now).total_seconds())
        # Window wraps midnight
        if now.time() >= start:
            end_dt = datetime.combine(today + timedelta(days=1), end)
        else:
            end_dt = datetime.combine(today, end)
        return max(0.0, (end_dt - now).total_seconds())
