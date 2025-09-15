
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


class PostScheduler:
    """Simple helper for scheduling posts with a night sleep window.

    Parameters
    ----------
    night_sleep_start:
        Start of the daily window during which no posts should be scheduled.
    night_sleep_end:
        End of the sleep window.
    """

    def __init__(self, night_sleep_start: datetime, night_sleep_end: datetime):
        self.night_sleep_start = night_sleep_start
        self.night_sleep_end = night_sleep_end
        self.next_post: Optional[datetime] = None

    def schedule_next(self, now: datetime, delay: timedelta) -> datetime:
        """Schedule the next post after ``delay`` seconds.

        If ``now`` falls within the configured night sleep window, the post is
        delayed until ``night_sleep_end`` plus ``delay``.  Otherwise, the post
        is scheduled ``delay`` after ``now``.
        """

        if self.night_sleep_start <= now <= self.night_sleep_end:
            scheduled = self.night_sleep_end + delay
        else:
            scheduled = now + delay
        self.next_post = scheduled
        return scheduled
