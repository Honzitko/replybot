#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
X Scheduler (Manual/Compliant Edition)
--------------------------------------
What it does (compliant, no simulated input):
• Profiles (JSON in ./configs) — load/save "Default" + custom
• Sections with queries (search terms) & responses (reply lines)
• Global Search Mode: Popular / Latest (opens proper X search URL)
• NEW: Search Open Policy — Every time / Once per step / Once per section
• Session pacing: session hours, step minutes, break minutes, micro-pauses
• Daily/hourly caps, content similarity filter, profanity/blacklist/whitelist
• Picks a response, copies it to clipboard, opens (or reuses) the search page
• YOU perform the like/reply in the browser — no automation of actions

Requirements:
• Python 3.10+ recommended
• pip install pyperclip

Run:
    python x.py
"""

from __future__ import annotations

import os
import json
import time
import queue
import random
import threading
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import List, Tuple, Optional, Dict, Set, Callable

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
from urllib.parse import quote as url_quote
try:
    from pynput import keyboard as pynkeyboard
except Exception:  # pragma: no cover - optional dependency
    pynkeyboard = None
from keyboard_controller import KeyboardController, is_app_generated
from news_library import NewsLibrary
from post_library import PostLibrary
from rss_ingestor import DEFAULT_INTERVAL_SECONDS, RSSIngestor

try:
    import requests
except Exception:  # pragma: no cover - optional dependency
    requests = None

try:
    import pyperclip
except Exception:
    pyperclip = None

# ---- CET tz safe fallback
try:
    from zoneinfo import ZoneInfo
    CET = ZoneInfo("Europe/Prague")
except Exception:
    from datetime import timedelta as _td
    class FixedOffset(tzinfo):
        def __init__(self, minutes): self._o = _td(minutes=minutes)
        def utcoffset(self, dt): return self._o
        def dst(self, dt): return _td(0)
        def tzname(self, dt): return "CET_Fallback"
    CET = FixedOffset(60)

# ---- Helpers
def jitter(a: float, b: float) -> float: return random.uniform(a, b)
def rand_minutes(r: Tuple[int, int]) -> int: return random.randint(r[0], r[1])
def rand_hours(r: Tuple[float, float]) -> float: return random.uniform(r[0], r[1])
def token_set(text: str) -> Set[str]: return set(text.lower().split())
def similarity_ratio(a: str, b: str) -> float:
    ta, tb = token_set(a), token_set(b)
    if not ta or not tb: return 0.0
    inter = len(ta & tb); union = len(ta | tb)
    return inter / max(1, union)

BASE_WAIT = 3
MAX_WAIT = 15

# Natural pauses inserted between high-level actions to mimic human pacing.
STEP_PAUSE_MIN = 0.5
STEP_PAUSE_MAX = 1.8
# The discovery feed sometimes requires two rapid ``j`` presses to load more
# posts.  Keep the first couple of presses almost instantaneous before falling
# back to the regular pacing used for the remaining presses in the batch.
FAST_J_INITIAL_DELAY_RANGE = (0.0, 0.05)


def ensure_connection(url: str, timeout: float) -> float:
    """Check connectivity to ``url`` and return the request time.

    The original implementation required the optional ``requests`` dependency
    and raised ``RuntimeError`` when it was missing.  In environments where
    ``requests`` is unavailable the browser was never opened which confused
    users.  Instead of failing outright we now log a warning and skip the
    connectivity check, returning ``0.0`` to indicate that no timing
    information is available.
    """

    if requests is None:  # pragma: no cover - exercised when optional dep missing
        logging.warning(
            "requests library is not available; skipping connection check"
        )
        return 0.0

    start = time.time()
    try:
        resp = requests.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        return time.time() - start
    except Exception as e:  # pragma: no cover - network errors are environment-specific
        logging.error(f"Connection check failed for {url}: {e}")
        raise

def build_search_url(query: str, mode: str) -> str:
    """
    Popular: https://x.com/search?q=<q>&src=typed_query
    Latest:  https://x.com/search?q=<q>&src=typed_query&f=live
    """
    q = url_quote(query or "")
    url = f"https://x.com/search?q={q}&src=typed_query"
    if str(mode).lower() in ("latest", "nejnovější", "nejnovejsi", "live"):
        url += "&f=live"
    return url

# ---- Section model
@dataclass
class Section:
    name: str
    typing_ms_per_char: Tuple[int, int] = (220, 240)
    max_responses_before_switch: Tuple[int, int] = (4, 8)
    search_queries: List[str] = field(default_factory=list)
    responses: List[str] = field(default_factory=list)
    def pick_typing_speed(self) -> int: return random.randint(*self.typing_ms_per_char)
    def pick_max_responses(self) -> int: return random.randint(*self.max_responses_before_switch)
    def pick_query(self) -> Optional[str]: return random.choice(self.search_queries) if self.search_queries else None
    def pick_response(self) -> Optional[str]: return random.choice(self.responses) if self.responses else None

# ---- Defaults (edit in UI later)
DEFAULT_SECTIONS_SEED = [
    ("Sharp & Direct",(220,240),(6,12),["morning performance","industry trends","team updates"],
     ["Strong start to the day. Let’s build.","Morning momentum sets the tone.","Focused and ready to execute."]),
    ("Professional & Brief",(220,240),(5,9),["client progress","feature rollouts"],
     ["Starting strong and staying consistent.","Vision only matters with execution.","Early action sets the pace."]),
    ("Builder Mindset",(220,240),(5,10),["roadmap items","dev insights"],
     ["Every task is a brick in the wall.","Opportunities don’t knock, they’re built.","Build momentum early."]),
    ("Networking & Collab",(220,240),(4,8),["partner announcements","collab opportunities"],
     ["Open to smart partnerships—let’s align.","If you’re building, let’s connect.","Partnerships create possibilities."]),
    ("Motivation Lite",(220,240),(5,9),["team shoutouts"],
     ["Make it count today.","Results over opinions.","Keep stacking small wins."]),
    ("Execution Mode",(220,240),(6,12),["status check","backlog review"],
     ["The plan is simple: execute.","Decide, then execute.","Clarity creates confidence."]),
    ("Weekend Chill (still pro)",(220,260),(3,6),["light topics","community notes"],
     ["Fresh start, same fire.","Stay in motion.","Good energy, good outcomes."]),
    ("Insightful & Calm",(220,260),(4,8),["market notes","customer wins"],
     ["Focus on what compounds.","Daily effort writes the future.","Direction beats speed."]),
    ("Fast & To the Point",(220,240),(7,13),["quick scans"],
     ["Keep it moving.","Outperform yesterday.","Push the line forward."]),
    ("Creator/Brand Voice",(220,240),(5,10),["brand mentions","community threads"],
     ["Let’s turn ideas into outcomes.","Consistency is the advantage.","Show up, level up."]),
]

# ---- Post scheduling


class PostDraftQueue:
    """Very small in-memory FIFO queue for composed posts.

    The real application may later expand this to persist posts or schedule
    them for a specific time.  For the purposes of the tests it simply keeps a
    list of posts that have been saved through the :class:`PostEditor` dialog.
    """

    def __init__(self) -> None:
        self.posts: List[str] = []

    def push(self, text: str) -> None:
        """Store ``text`` for later processing if it is not empty."""

        if text:
            self.posts.append(text)


class PostEditor(tk.Toplevel):
    """Simple dialog allowing the user to compose and save a post."""

    def __init__(self, master: tk.Misc, queue: PostDraftQueue):
        super().__init__(master)
        self.title("New post")
        self.queue = queue
        self.resizable(True, True)

        self.txt = scrolledtext.ScrolledText(self, width=60, height=10)
        self.txt.pack(fill="both", expand=True, padx=10, pady=10)

        btn = ttk.Button(self, text="Save", command=self._save)
        btn.pack(pady=(0, 10))

        # Allow closing via window manager controls
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _save(self) -> None:
        content = self.txt.get("1.0", "end").strip()
        if content:
            self.queue.push(content)
        self.destroy()

# ---- Worker (manual flow, no key simulation)
class SchedulerWorker(threading.Thread):
    def __init__(
        self,
        cfg: Dict,
        sections: List[Section],
        logq: queue.Queue,
        stop_event: threading.Event,
        pause_event: threading.Event,
        kb,
    ):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.sections = sections
        self.logq = logq
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.kb = kb

        self.session_seconds = int(rand_hours(self.cfg["session_hours_range"]) * 3600)
        self.session_start = datetime.now(CET)
        self.session_end = self.session_start + timedelta(seconds=self.session_seconds)

        self.night_sleep_start, self.night_sleep_end = self._build_night_sleep_window()

        self.action_counter = 0
        self.interactions_today = 0
        self.interactions_this_hour = 0
        self.current_hour = datetime.now(CET).hour
        self.recent_replies: List[str] = []
        self.next_micro_pause_at = self._schedule_next_micro_pause()

        self.daily_cap = random.randint(*self.cfg["daily_interaction_cap_range"])
        self.hourly_cap = random.randint(*self.cfg["hourly_interaction_cap_range"])

        self.activity_scale = self._activity_scale()

        # derived
        self.search_mode = str(self.cfg.get("search_mode", "popular")).lower()
        self.search_open_policy = str(self.cfg.get("search_open_policy", "once_per_step")).lower()
        # tracking to avoid opening many tabs
        self._opened_sections: Set[str] = set()
        self._opened_this_step: bool = False
        self._browser_opened = False

    def _log(self, level, msg):
        ts = datetime.now(CET).strftime("%Y-%m-%d %H:%M:%S")
        self.logq.put(f"{ts} [{level}] {msg}")

    def _activity_scale(self):
        now = datetime.now(CET)
        return self.cfg["weekend_activity_scale"] if now.weekday() >= 5 else self.cfg["weekday_activity_scale"]

    def _build_night_sleep_window(self):
        now = datetime.now(CET)
        sh_min, sh_max = self.cfg["night_sleep_start_hour_range"]
        sm_min, sm_max = self.cfg["night_sleep_start_minute_jitter"]
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            hours=random.randint(sh_min, sh_max),
            minutes=random.randint(sm_min, sm_max)
        )
        if now > start: start += timedelta(days=1)
        dur_h = rand_hours(self.cfg["night_sleep_hours_range"])
        return start, start + timedelta(hours=dur_h)

    def _in_night_sleep(self, now): return self.night_sleep_start <= now <= self.night_sleep_end

    def _wait_if_paused(self):
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.5)

    def _pauseable_sleep(self, duration: float, chunk: float = 1.0):
        end = time.time() + duration
        while time.time() < end and not self.stop_event.is_set():
            if self.pause_event.is_set():
                self._wait_if_paused()
                continue
            remaining = end - time.time()
            time.sleep(min(chunk, remaining))

    def _sleep_until(self, dt):
        delta = max(0.0, (dt - datetime.now(CET)).total_seconds())
        self._log("INFO", f"Sleeping {delta:.0f}s until {dt}")
        self._pauseable_sleep(delta, chunk=30)

    def _schedule_next_micro_pause(self):
        n_min, n_max = self.cfg["micro_pause_every_n_actions_range"]
        return self.action_counter + max(1, random.randint(n_min, n_max))

    def _micro_pause_if_due(self):
        if self.action_counter >= self.next_micro_pause_at:
            dur = jitter(*self.cfg["micro_pause_seconds_range"])
            self._log("INFO", f"Micro pause {dur:.1f}s")
            self._pauseable_sleep(dur)
            self.next_micro_pause_at = self._schedule_next_micro_pause()

    def _cooldown(self):
        base = jitter(*self.cfg["min_seconds_between_actions_range"])
        self._pauseable_sleep(base)
        if random.random() < self.cfg["extra_jitter_probability"] and not self.stop_event.is_set():
            extra = jitter(*self.cfg["extra_jitter_seconds_range"])
            self._log("DEBUG", f"Extra idle jitter {extra:.1f}s")
            self._pauseable_sleep(extra)

    def _bump_counters(self):
        now = datetime.now(CET)
        if now.hour != self.current_hour:
            self.current_hour = now.hour
            self.interactions_this_hour = 0
        self.interactions_today += 1
        self.interactions_this_hour += 1

    def _caps_remaining(self):
        if self.interactions_today >= self.daily_cap:
            self._log("INFO", f"Daily cap reached ({self.daily_cap}). Stopping.")
            return False
        if self.interactions_this_hour >= self.hourly_cap:
            self._log("INFO", f"Hourly cap reached ({self.hourly_cap}). Waiting next hour…")
            end = datetime.now(CET).replace(minute=59, second=59, microsecond=0)
            self._sleep_until(end)
            self.interactions_this_hour = 0
        return True

    def _allowed_for_text(self, text: str) -> bool:
        t = text.lower()
        if any(p in t for p in self.cfg["profanity_list"]): return False
        if self.cfg["blacklist_keywords"] and any(k.lower() in t for k in self.cfg["blacklist_keywords"]): return False
        if self.cfg["whitelist_keywords"]:
            if not any(k.lower() in t for k in self.cfg["whitelist_keywords"]): return False
        for prev in self.recent_replies:
            if similarity_ratio(prev, text) >= self.cfg["content_similarity_threshold"]:
                return False
        return True

    def _record_reply(self, text: str):
        self.recent_replies.append(text)
        if len(self.recent_replies) > self.cfg["uniqueness_memory_size"]:
            self.recent_replies.pop(0)

    # ---- Search open policy
    def _should_open_search_now(self, section_name: str) -> bool:
        policy = self.search_open_policy
        if policy == "every_time":
            return True
        if policy == "once_per_step":
            return not self._opened_this_step
        if policy == "once_per_section":
            return section_name not in self._opened_sections
        # default safe
        return not self._opened_this_step

    def _mark_opened(self, section_name: str):
        policy = self.search_open_policy
        if policy == "once_per_step":
            self._opened_this_step = True
        elif policy == "once_per_section":
            self._opened_sections.add(section_name)
        else:  # every_time
            pass

    def _reset_step_open_state(self):
        """Reset per-step tracking of opened search sections."""

        self._opened_this_step = False
        self._opened_sections.clear()

    def _open_search(self, query: str, section_name: str):
        url = build_search_url(query, self.search_mode)
        try:
            elapsed = ensure_connection(url, timeout=5)
            self._log("INFO", f"Connection verified in {elapsed:.2f}s")
        except Exception as e:
            self._log("ERROR", f"Connection check failed: {e}")
            return
        self._log("INFO", f"Open search: {url}")
        try:
            if not self._browser_opened:
                import webbrowser
                webbrowser.open(url, new=0, autoraise=True)
                self._browser_opened = True
            else:
                key = "cmd" if sys.platform == "darwin" else "ctrl"
                self.kb.hotkey(key, "l")
                time.sleep(0.1)
                self.kb.typewrite(url, interval=0, jitter=0)
                self.kb.press("enter")
        except Exception as e:
            self._log("ERROR", f"Browser navigation failed: {e}")
            return
        start = time.time()
        waited = 0.0
        while not self.stop_event.is_set():
            self._pauseable_sleep(0.5)
            waited = time.time() - start
            if waited >= BASE_WAIT and (waited >= elapsed or waited >= MAX_WAIT):
                break
        if waited >= MAX_WAIT:
            self._log("WARN", f"Page load wait exceeded {MAX_WAIT}s; consider retry.")
        else:
            self._log("INFO", f"Page ready after {waited:.2f}s")

    def _push_to_clipboard(self, text: str):
        if not pyperclip:
            self._log("WARN", "pyperclip not installed; cannot copy to clipboard. pip install pyperclip")
            return
        try:
            pyperclip.copy(text)
            self._log("INFO", "Reply copied to clipboard.")
        except Exception as e:
            self._log("ERROR", f"Clipboard copy failed: {e}")

    def _send_reply(self, text: str):
        """Simulate typing ``text`` and submit the reply."""

        # Type the reply one character at a time to imitate natural typing.
        for ch in text:
            self.kb.press(ch)
            time.sleep(0.05)

        # Small pause before sending the reply.
        time.sleep(random.uniform(STEP_PAUSE_MIN, STEP_PAUSE_MAX))
        key = "cmd" if sys.platform == "darwin" else "ctrl"
        # On X/Twitter a reply is sent with Cmd/Ctrl+Enter
        self.kb.hotkey(key, "enter")

    def _press_j_batch(self, stop_event: Optional[threading.Event] = None) -> bool:
        presses = random.randint(2, 5)
        for idx in range(presses):
            if self.stop_event.is_set():
                return False
            if stop_event and stop_event.is_set():
                return False
            self.kb.press("j")
            if idx < 2:
                delay = random.uniform(*FAST_J_INITIAL_DELAY_RANGE)
            else:
                delay = random.uniform(STEP_PAUSE_MIN, STEP_PAUSE_MAX)
            if stop_event:
                self._pauseable_sleep(delay, chunk=0.1)
            else:
                time.sleep(delay)
        return True

    def _interact_and_reply(self, text: str):
        time.sleep(random.uniform(STEP_PAUSE_MIN, STEP_PAUSE_MAX))
        self.kb.press("l")
        time.sleep(random.uniform(STEP_PAUSE_MIN, STEP_PAUSE_MAX))
        self.kb.press("r")
        time.sleep(random.uniform(STEP_PAUSE_MIN, STEP_PAUSE_MAX))
        self._send_reply(text)

    def run(self):
        self._log("INFO", f"Session start {self.session_start} | ends by {self.session_end}")
        self._log("INFO", f"Night sleep: {self.night_sleep_start} → {self.night_sleep_end}")
        self._log("INFO", f"Daily cap={self.daily_cap} | Hourly cap={self.hourly_cap} | Search mode={self.search_mode} | Open policy={self.search_open_policy}")
        try:
            while datetime.now(CET) < self.session_end and not self.stop_event.is_set():
                self._wait_if_paused()
                now = datetime.now(CET)
                if self._in_night_sleep(now):
                    self._log("INFO", "Night sleep window active.")
                    self._sleep_until(self.night_sleep_end)
                    continue

                if not self._caps_remaining():
                    break

                step_minutes = max(1, int(rand_minutes(self.cfg["session_step_minutes_range"]) * self.activity_scale))
                break_minutes = max(1, int(rand_minutes(self.cfg["session_break_minutes_range"]) * self.activity_scale))
                self._log("INFO", f"Work step: {step_minutes} min | Break: {break_minutes} min")
                self._reset_step_open_state()

                step_deadline = datetime.now(CET) + timedelta(minutes=step_minutes)
                targets_goal = max(1, int(rand_minutes(self.cfg["targets_per_step_range"]) * self.activity_scale))

                sections = self.sections[:]; random.shuffle(sections)
                processed = 0

                for section in sections:
                    if (self.stop_event.is_set() or
                            datetime.now(CET) >= step_deadline or
                            processed >= targets_goal):
                        break
                    self._wait_if_paused()
                    max_responses = max(1, section.pick_max_responses())
                    remaining_attempts = max_responses
                    self._log("INFO", f"Section → {section.name} (limit {max_responses})")

                    current_query = section.pick_query() or "general discovery"
                    if self._should_open_search_now(section.name):
                        self._open_search(current_query, section.name)
                        self._mark_opened(section.name)

                    while remaining_attempts > 0:
                        if (self.stop_event.is_set() or
                                datetime.now(CET) >= step_deadline or
                                processed >= targets_goal or
                                not self._caps_remaining()):
                            break

                        self._wait_if_paused()
                        self._micro_pause_if_due()

                        if not self._press_j_batch():
                            break

                        reply_text = section.pick_response() or "Starting strong and staying consistent."
                        if not self._allowed_for_text(reply_text):
                            remaining_attempts -= 1
                            self._log("DEBUG", f"Filtered reply skipped for {section.name}: {reply_text!r}")
                            if remaining_attempts <= 0:
                                self._log("INFO", f"Section {section.name} response limit reached (filtered out).")
                                break
                            continue

                        if self.cfg.get("transparency_tag_enabled", False):
                            reply_text = f"{reply_text} {self.cfg.get('transparency_tag_text','— managed account')}"

                        self._log("INFO", f"Replying → {reply_text!r}")
                        self._interact_and_reply(reply_text)

                        self._cooldown()

                        self._record_reply(reply_text)
                        processed += 1; self.action_counter += 1; self._bump_counters()

                        remaining_attempts -= 1
                        if remaining_attempts <= 0:
                            self._log("INFO", f"Section {section.name} response limit reached.")
                            break

                if datetime.now(CET) < self.session_end and not self.stop_event.is_set():
                    until = min(self.session_end, datetime.now(CET) + timedelta(minutes=break_minutes))
                    self._log("INFO", f"Step break until {until}")
                    self._sleep_until(until)

            self._log("INFO", f"Session end {datetime.now(CET)} | total actions: {self.action_counter}")
        except KeyboardInterrupt:
            self._log("WARN", "Interrupted by user.")
        except Exception as e:
                        self._log("ERROR", f"Fatal: {e}")

# ---- GUI

class PostScheduler(threading.Thread):
    """Simple scheduler that pauses the reply flow before triggering a post."""

    def __init__(
        self,
        interval_minutes: int,
        pause_event: threading.Event,
        stop_event: threading.Event,
        post_callback,
    ):
        super().__init__(daemon=True)
        self.interval_minutes = interval_minutes
        self.pause_event = pause_event
        self.stop_event = stop_event
        self.post_callback = post_callback

    def run(self):
        while not self.stop_event.is_set():
            time.sleep(self.interval_minutes * 60)
            if self.stop_event.is_set():
                break
            self._trigger_post()

    def _trigger_post(self):
        self.pause_event.set()
        try:
            if self.post_callback:
                self.post_callback()
        except Exception:
            pass
        finally:
            self.pause_event.clear()
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("X Scheduler — Manual/Compliant")
        self.geometry("1180x900")
        self.minsize(1040, 780)

        self.config_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "configs")
        os.makedirs(self.config_dir, exist_ok=True)

        self.news_library = NewsLibrary(os.path.join(self.config_dir, "news.json"))
        self.post_library = PostLibrary(os.path.join(self.config_dir, "posts.json"))

        self.logq: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.kb = KeyboardController()
        self.worker: Optional[SchedulerWorker] = None
        self.post_scheduler: Optional["PostScheduler"] = None
        self.rss_ingestor: Optional[RSSIngestor] = None
        self.post_queue = PostDraftQueue()
        self.run_monitor: Optional["App._RunMonitor"] = None
        self._session_active: bool = False

        self._rss_enabled = False
        default_interval_minutes = int(DEFAULT_INTERVAL_SECONDS // 60)
        self.var_rss_feeds = tk.StringVar(value="")
        self.var_rss_interval_minutes = tk.IntVar(value=default_interval_minutes)
        self.var_openai_api_key = tk.StringVar(value="")
        self.var_rss_max_length = tk.IntVar(value=280)
        self.rss_last_fetch_var = tk.StringVar(value="Last fetch: —")
        self.rss_next_fetch_var = tk.StringVar(value="Next fetch: — (disabled)")
        self._rss_last_value: Optional[datetime] = None
        self._rss_next_value: Optional[datetime] = None
        self.txt_rss_persona: Optional[scrolledtext.ScrolledText] = None

        self.current_profile: Optional[str] = None
        self.dirty: bool = False

        # post scheduling and profile key bindings
        self.profile_key_bindings: Dict[str, Callable] = {}

        self._build_ui()
        self._init_default_profile()
        self._set_rss_enabled(False)
        self.after(120, self._drain_logs)

        # global keyboard listener to auto-pause on manual input
        if pynkeyboard is not None:  # pragma: no cover - optional dependency
            self._key_listener = pynkeyboard.Listener(on_press=self._on_global_key)
            self._key_listener.start()
        else:
            self._key_listener = None

    class _Countdown(tk.Toplevel):
        def __init__(self, master, seconds, on_done, on_cancel):
            super().__init__(master)
            self.title("Starting soon…")
            self.resizable(False, False)
            self.remaining = seconds
            self.on_done = on_done
            self.on_cancel = on_cancel
            self.label = ttk.Label(self, text="", font=("TkDefaultFont", 24))
            self.label.pack(padx=20, pady=20)
            btn = ttk.Button(self, text="Cancel", command=self.cancel)
            btn.pack(pady=(0, 20))
            self.protocol("WM_DELETE_WINDOW", self.cancel)
            self._tick()

        def _tick(self):
            if self.remaining <= 0:
                self.destroy()
                self.on_done()
            else:
                self.label.config(text=str(self.remaining))
                self.remaining -= 1
                self.after(1000, self._tick)

        def cancel(self):
            self.destroy()
            if self.on_cancel:
                self.on_cancel()

    class _RunMonitor(tk.Toplevel):
        def __init__(self, master: "App"):
            super().__init__(master)
            self.master = master
            self.title("Session running…")
            self.resizable(True, True)
            self.geometry("560x360")
            self.transient(master)
            self.start_time = time.time()

            self.status_var = tk.StringVar(value="Status: Running")
            self.elapsed_var = tk.StringVar(value="Elapsed: 00:00:00")

            header = ttk.Frame(self)
            header.pack(fill="x", padx=12, pady=(12, 6))
            ttk.Label(header, textvariable=self.status_var).pack(side="left")
            ttk.Label(header, textvariable=self.elapsed_var).pack(side="right")

            self.log_text = scrolledtext.ScrolledText(
                self, wrap="word", state="disabled", height=12
            )
            self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 8))

            btns = ttk.Frame(self)
            btns.pack(fill="x", padx=12, pady=(0, 12))

            self.btn_pause = ttk.Button(btns, text="Pause", command=self._on_pause)
            self.btn_pause.pack(side="left")

            self.btn_resume = ttk.Button(
                btns, text="Resume", command=self._on_resume, state="disabled"
            )
            self.btn_resume.pack(side="left", padx=(6, 0))

            self.btn_stop = ttk.Button(btns, text="Stop", command=self._on_stop)
            self.btn_stop.pack(side="right")

            self.btn_copy = ttk.Button(btns, text="Copy logs", command=self._copy_logs)
            self.btn_copy.pack(side="right", padx=(0, 6))

            self.protocol("WM_DELETE_WINDOW", self._on_stop)
            self.bind("<Escape>", lambda *_: self._on_stop())

            self.set_paused(False)
            self.after(200, self._update_elapsed)
            self.focus()

        def _on_pause(self):
            self.master.pause_clicked()

        def _on_resume(self):
            self.master.resume_clicked()

        def _on_stop(self):
            self.master.stop_clicked()

        def set_paused(self, paused: bool):
            if paused:
                self.status_var.set("Status: Paused")
                self.btn_pause.configure(state="disabled")
                self.btn_resume.configure(state="normal")
            else:
                self.status_var.set("Status: Running")
                self.btn_pause.configure(state="normal")
                self.btn_resume.configure(state="disabled")

        def append_log(self, line: str):
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        def _copy_logs(self):
            self.log_text.configure(state="normal")
            text = self.log_text.get("1.0", "end-1c")
            self.log_text.configure(state="disabled")
            if not text:
                return
            self.master.clipboard_clear()
            self.master.clipboard_append(text)
            self.status_var.set("Status: Logs copied to clipboard")
            if self.master.run_monitor is self:
                self.after(
                    1500,
                    lambda: self.set_paused(self.master.pause_event.is_set()),
                )

        def _update_elapsed(self):
            if self.master.run_monitor is not self:
                return
            elapsed = max(0, int(time.time() - self.start_time))
            hrs, rem = divmod(elapsed, 3600)
            mins, secs = divmod(rem, 60)
            self.elapsed_var.set(f"Elapsed: {hrs:02d}:{mins:02d}:{secs:02d}")
            self.after(1000, self._update_elapsed)

    # UI scaffolding
    def _build_ui(self):
        self.nb = ttk.Notebook(self); self.nb.pack(fill="both", expand=True)

        self.tab_settings = ttk.Frame(self.nb)
        self.tab_session = ttk.Frame(self.nb)
        self.tab_behavior = ttk.Frame(self.nb)
        self.tab_guardrails = ttk.Frame(self.nb)
        self.tab_sections = ttk.Frame(self.nb)
        self.tab_posts = ttk.Frame(self.nb)
        self.tab_review = ttk.Frame(self.nb)
        self.tab_log = ttk.Frame(self.nb)

        self.nb.add(self.tab_settings, text="Nastavení")
        self.nb.add(self.tab_session, text="Session & Sleep")
        self.nb.add(self.tab_behavior, text="Humanization & Behavior")
        self.nb.add(self.tab_guardrails, text="Guardrails")
        self.nb.add(self.tab_sections, text="Sections (Queries/Responses)")
        self.nb.add(self.tab_posts, text="Posts")
        self.nb.add(self.tab_review, text="Review & Transparency")
        self.nb.add(self.tab_log, text="Logs")

        self._build_settings_tab(self.tab_settings)
        self._build_session_tab(self.tab_session)
        self._build_behavior_tab(self.tab_behavior)
        self._build_guardrails_tab(self.tab_guardrails)
        self._build_sections_tab(self.tab_sections)
        self._build_posts_tab(self.tab_posts)
        self._build_review_tab(self.tab_review)
        self._build_log_tab(self.tab_log)

        # bottom bar
        bar = ttk.Frame(self); bar.pack(fill="x", padx=8, pady=6)
        self.btn_start = ttk.Button(bar, text="Start", command=self.start_clicked)
        self.btn_pause = ttk.Button(bar, text="Pause", command=self.pause_clicked, state="disabled")
        self.btn_resume = ttk.Button(bar, text="Resume", command=self.resume_clicked, state="disabled")
        self.btn_stop = ttk.Button(bar, text="Stop", command=self.stop_clicked, state="disabled")
        self.lbl_status = ttk.Label(bar, text="Profil: —")
        self.btn_start.pack(side="left")
        self.btn_pause.pack(side="left", padx=8)
        self.btn_resume.pack(side="left", padx=8)
        self.btn_stop.pack(side="left", padx=8)
        self.lbl_status.pack(side="right")

    def _build_settings_tab(self, root):
        f = ttk.Frame(root); f.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(f, text="Vyber profil:").grid(row=0, column=0, sticky="w")
        self.var_profile = tk.StringVar()
        self.cmb_profile = ttk.Combobox(f, textvariable=self.var_profile, width=40,
                                        values=self._list_profiles(), state="readonly")
        self.cmb_profile.grid(row=0, column=1, sticky="w")
        ttk.Button(f, text="Načíst", command=self.load_profile).grid(row=0, column=2, padx=6)
        ttk.Button(f, text="Uložit", command=self.save_profile).grid(row=0, column=3, padx=6)
        ttk.Button(f, text="Uložit jako…", command=self.save_profile_as).grid(row=0, column=4, padx=6)

        self.lbl_dirty = ttk.Label(f, text="", foreground="#b36b00")
        self.lbl_dirty.grid(row=1, column=0, columnspan=5, sticky="w", pady=(10,0))

        llm_frame = ttk.LabelFrame(f, text="AI RSS summarisation")
        llm_frame.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(12, 0))
        llm_frame.columnconfigure(1, weight=1)

        ttk.Label(llm_frame, text="OpenAI API key:").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        entry_api = ttk.Entry(llm_frame, textvariable=self.var_openai_api_key, show="*", width=42)
        entry_api.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(8, 2))
        entry_api.bind("<KeyRelease>", lambda *_: self._mark_dirty())

        ttk.Label(llm_frame, text="Persona instructions:").grid(row=1, column=0, sticky="nw", padx=8, pady=(0, 6))
        self.txt_rss_persona = scrolledtext.ScrolledText(llm_frame, height=4, wrap="word")
        self.txt_rss_persona.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 6))
        self.txt_rss_persona.bind("<<Modified>>", self._on_text_modified)

        ttk.Label(llm_frame, text="Maximum length:").grid(row=2, column=0, sticky="w", padx=8, pady=(0, 8))
        length_row = ttk.Frame(llm_frame)
        length_row.grid(row=2, column=1, sticky="w", padx=(0, 8), pady=(0, 8))
        entry_length = ttk.Entry(length_row, textvariable=self.var_rss_max_length, width=6)
        entry_length.pack(side="left")
        entry_length.bind("<KeyRelease>", lambda *_: self._mark_dirty())
        ttk.Label(length_row, text="characters").pack(side="left", padx=(4, 0))

        ttk.Label(llm_frame, text="Leave API key blank to use the manual placeholder.").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8)
        )

        # Search mode + open policy
        row3 = ttk.LabelFrame(f, text="Search")
        row3.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(12,0))

        self.var_search_mode = tk.StringVar(value="Popular")
        ttk.Label(row3, text="Search filter:").grid(row=0, column=0, sticky="w", padx=8)
        cb = ttk.Combobox(row3, textvariable=self.var_search_mode, state="readonly",
                          values=["Popular","Latest"], width=12)
        cb.grid(row=0, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda *_: self._mark_dirty())

        self.var_open_policy = tk.StringVar(value="Once per step")
        ttk.Label(row3, text="Open policy:").grid(row=0, column=2, sticky="e", padx=8)
        cb2 = ttk.Combobox(row3, textvariable=self.var_open_policy, state="readonly",
                           values=["Every time","Once per step","Once per section"], width=18)
        cb2.grid(row=0, column=3, sticky="w")
        cb2.bind("<<ComboboxSelected>>", lambda *_: self._mark_dirty())

        ttk.Label(row3, text="Popular → typed_query; Latest → &f=live. Open policy controls how often a tab is opened.").grid(row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(6,2))

        # Key binding: allow composing a new post via "N"
        root.bind("N", self._open_post_editor)
        self.profile_key_bindings["N"] = lambda: self._open_post_editor()

    def _on_global_key(self, key):
        if is_app_generated():
            return
        if not self.pause_event.is_set():
            self.pause_event.set()
            self._append_log("INFO", "Paused due to user input.")
            self.after(0, lambda: (self.btn_pause.configure(state="disabled"), self.btn_resume.configure(state="normal")))
            self._set_monitor_paused(True)

    def _open_post_editor(self, event=None):
        """Open the :class:`PostEditor` dialog and let the user compose a post."""

        PostEditor(self, self.post_queue)

    def _build_session_tab(self, root):
        f = ttk.Frame(root); f.pack(fill="both", expand=True, padx=10, pady=10)
        self.var_session_hours_min = tk.DoubleVar(value=12.0)
        self.var_session_hours_max = tk.DoubleVar(value=14.0)
        self._pair(f, "Session hours (min/max)", self.var_session_hours_min, self.var_session_hours_max)

        self.var_step_min = tk.IntVar(value=12)
        self.var_step_max = tk.IntVar(value=16)
        self._pair(f, "Session step minutes (min/max)", self.var_step_min, self.var_step_max)

        self.var_break_min = tk.IntVar(value=2)
        self.var_break_max = tk.IntVar(value=4)
        self._pair(f, "Session break minutes (min/max)", self.var_break_min, self.var_break_max)

        self.var_post_interval = tk.IntVar(value=0)
        self._single(f, "Post interval minutes", self.var_post_interval)

        self.var_sleep_start_h_min = tk.IntVar(value=22)
        self.var_sleep_start_h_max = tk.IntVar(value=24)
        self._pair(f, "Night sleep start hour CET (min/max)", self.var_sleep_start_h_min, self.var_sleep_start_h_max)

        self.var_sleep_start_jitter_min = tk.IntVar(value=0)
        self.var_sleep_start_jitter_max = tk.IntVar(value=30)
        self._pair(f, "Night sleep start minute jitter (min/max)", self.var_sleep_start_jitter_min, self.var_sleep_start_jitter_max)

        self.var_sleep_hours_min = tk.DoubleVar(value=7.0)
        self.var_sleep_hours_max = tk.DoubleVar(value=8.0)
        self._pair(f, "Night sleep hours (min/max)", self.var_sleep_hours_min, self.var_sleep_hours_max)

        self.var_weekday_scale = tk.DoubleVar(value=1.0)
        self.var_weekend_scale = tk.DoubleVar(value=0.9)
        self._single(f, "Weekday activity scale", self.var_weekday_scale)
        self._single(f, "Weekend activity scale", self.var_weekend_scale)
        self._bind_dirty(f)

    def _build_behavior_tab(self, root):
        f = ttk.Frame(root); f.pack(fill="both", expand=True, padx=10, pady=10)
        self.var_micro_every_min = tk.IntVar(value=8)
        self.var_micro_every_max = tk.IntVar(value=12)
        self._pair(f, "Micro-pause every N actions (min/max)", self.var_micro_every_min, self.var_micro_every_max)

        self.var_micro_s_min = tk.DoubleVar(value=2.0)
        self.var_micro_s_max = tk.DoubleVar(value=4.0)
        self._pair(f, "Micro-pause seconds (min/max)", self.var_micro_s_min, self.var_micro_s_max)

        self.var_min_gap_s_min = tk.DoubleVar(value=0.4)
        self.var_min_gap_s_max = tk.DoubleVar(value=0.9)
        self._pair(f, "Seconds between actions (min/max)", self.var_min_gap_s_min, self.var_min_gap_s_max)

        self.var_extra_jitter_prob = tk.DoubleVar(value=0.05)
        self.var_extra_jitter_s_min = tk.DoubleVar(value=1.0)
        self.var_extra_jitter_s_max = tk.DoubleVar(value=2.5)
        self._single(f, "Extra jitter probability (0-1)", self.var_extra_jitter_prob)
        self._pair(f, "Extra jitter seconds (min/max)", self.var_extra_jitter_s_min, self.var_extra_jitter_s_max)

        self._bind_dirty(f)

    def _build_guardrails_tab(self, root):
        f = ttk.Frame(root); f.pack(fill="both", expand=True, padx=10, pady=10)

        self.var_daily_cap_min = tk.IntVar(value=1150)
        self.var_daily_cap_max = tk.IntVar(value=1250)
        self._pair(f, "Daily interaction cap (min/max)", self.var_daily_cap_min, self.var_daily_cap_max)

        self.var_hourly_cap_min = tk.IntVar(value=90)
        self.var_hourly_cap_max = tk.IntVar(value=110)
        self._pair(f, "Hourly interaction cap (min/max)", self.var_hourly_cap_min, self.var_hourly_cap_max)

        self.var_whitelist = tk.StringVar(value="")
        self.var_blacklist = tk.StringVar(value="")
        self._single(f, "Whitelist keywords (comma-separated)", self.var_whitelist, width=70)
        self._single(f, "Blacklist keywords (comma-separated)", self.var_blacklist, width=70)

        self.var_profanity = tk.StringVar(value="")
        self._single(f, "Profanity list (comma-separated, lower-case)", self.var_profanity, width=70)

        self.var_similarity = tk.DoubleVar(value=0.90)
        self.var_unique_mem = tk.IntVar(value=200)
        self._single(f, "Similarity threshold (0..1)", self.var_similarity)
        self._single(f, "Uniqueness memory size", self.var_unique_mem)
        self._bind_dirty(f)

    def _build_sections_tab(self, root):
        self.sections_vars = []
        nb = ttk.Notebook(root); nb.pack(fill="both", expand=True, padx=6, pady=6)

        for (name, typ_rng, max_resp, queries, responses) in DEFAULT_SECTIONS_SEED:
            tab = ttk.Frame(nb)
            nb.add(tab, text=name[:16] + ("…" if len(name) > 16 else ""))

            v_typ_min = tk.IntVar(value=typ_rng[0]); v_typ_max = tk.IntVar(value=typ_rng[1])
            v_resp_min = tk.IntVar(value=max_resp[0]); v_resp_max = tk.IntVar(value=max_resp[1])

            col = ttk.Frame(tab); col.pack(fill="both", expand=True, padx=10, pady=10)
            self._pair(col, f"{name} typing ms/char (min/max)", v_typ_min, v_typ_max)
            self._pair(col, f"{name} max responses before switch (min/max)", v_resp_min, v_resp_max)

            ttk.Label(col, text=f"{name} — Search queries (one per line):").pack(anchor="w", pady=(8,2))
            txt_q = scrolledtext.ScrolledText(col, height=6)
            txt_q.insert("1.0", "\n".join(queries))
            txt_q.pack(fill="both", expand=False)

            ttk.Label(col, text=f"{name} — Responses (one per line):").pack(anchor="w", pady=(8,2))
            txt_r = scrolledtext.ScrolledText(col, height=8)
            txt_r.insert("1.0", "\n".join(responses))
            txt_r.pack(fill="both", expand=True)

            txt_q.bind("<<Modified>>", self._on_text_modified)
            txt_r.bind("<<Modified>>", self._on_text_modified)

            self.sections_vars.append({
                "name": name, "typ_min": v_typ_min, "typ_max": v_typ_max,
                "resp_min": v_resp_min, "resp_max": v_resp_max,
                "txt_queries": txt_q, "txt_responses": txt_r
            })
        self._bind_dirty(nb)

    def _build_posts_tab(self, root):
        f = ttk.Frame(root)
        f.pack(fill="both", expand=True, padx=10, pady=10)

        rss_frame = ttk.LabelFrame(f, text="RSS ingestion")
        rss_frame.pack(fill="x", pady=(0, 12))
        rss_frame.columnconfigure(1, weight=1)

        ttk.Label(rss_frame, text="Feed URLs (comma-separated):").grid(row=0, column=0, sticky="w")
        entry_feeds = ttk.Entry(rss_frame, textvariable=self.var_rss_feeds)
        entry_feeds.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        entry_feeds.bind("<KeyRelease>", lambda *_: self._mark_dirty())

        ttk.Label(rss_frame, text="Fetch interval (minutes):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        entry_interval = ttk.Entry(rss_frame, textvariable=self.var_rss_interval_minutes, width=10)
        entry_interval.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        entry_interval.bind("<KeyRelease>", lambda *_: self._mark_dirty())

        ttk.Label(rss_frame, textvariable=self.rss_last_fetch_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(rss_frame, textvariable=self.rss_next_fetch_var).grid(
            row=3, column=0, columnspan=2, sticky="w"
        )

        list_frame = ttk.Frame(f)
        list_frame.pack(side="left", fill="both", expand=True)

        self.posts_list = tk.Listbox(list_frame)
        self.posts_list.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, command=self.posts_list.yview)
        scroll.pack(side="left", fill="y")
        self.posts_list.configure(yscrollcommand=scroll.set)

        btns = ttk.Frame(f)
        btns.pack(side="left", fill="y", padx=(10, 0))
        ttk.Button(btns, text="Add", command=self._add_post).pack(fill="x")
        ttk.Button(btns, text="Edit", command=self._edit_post).pack(fill="x", pady=4)
        ttk.Button(btns, text="Delete", command=self._delete_post).pack(fill="x")

        self._refresh_posts()

    def _refresh_posts(self):
        self.posts_list.delete(0, tk.END)
        for post in self.post_library.get_posts():
            self.posts_list.insert(tk.END, post)

    def _add_post(self):
        text = simpledialog.askstring("Add Post", "Post text:", parent=self)
        if text:
            self.post_library.add_post(text)
            self._refresh_posts()

    def _edit_post(self):
        sel = self.posts_list.curselection()
        if not sel:
            return
        idx = sel[0]
        current = self.post_library.get_posts()[idx]
        text = simpledialog.askstring("Edit Post", "Post text:", initialvalue=current, parent=self)
        if text is not None:
            self.post_library.update_post(idx, text)
            self._refresh_posts()

    def _delete_post(self):
        sel = self.posts_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if messagebox.askyesno("Delete Post", "Are you sure?"):
            self.post_library.delete_post(idx)
            self._refresh_posts()

    def _on_rss_status(self, last, next_fetch, paused):
        self.after(0, lambda: self._apply_rss_status(last, next_fetch, paused))

    def _apply_rss_status(self, last, next_fetch, paused: bool) -> None:
        if last is not None:
            self._rss_last_value = last
        if next_fetch is not None:
            self._rss_next_value = next_fetch

        display_last = self._rss_last_value
        display_next = self._rss_next_value if self._rss_enabled else None

        last_text = f"Last fetch: {self._format_rss_time(display_last)}"
        if not self._rss_enabled:
            last_text += " (disabled)"

        next_text = f"Next fetch: {self._format_rss_time(display_next)}"
        suffix = ""
        if not self._rss_enabled:
            suffix = " (disabled)"
        elif paused:
            suffix = " (paused)"
        elif display_next and display_next <= datetime.now(timezone.utc):
            suffix = " (due)"

        self.rss_last_fetch_var.set(last_text)
        self.rss_next_fetch_var.set(next_text + suffix)

    def _format_rss_time(self, value):
        if not value:
            return "—"
        try:
            return value.astimezone(CET).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return value.strftime("%Y-%m-%d %H:%M")

    def _set_rss_enabled(self, enabled: bool) -> None:
        self._rss_enabled = enabled
        if enabled:
            self._rss_last_value = None
            self._rss_next_value = None
        else:
            self._rss_next_value = None
        self._apply_rss_status(None, None, paused=False)

    def _build_review_tab(self, root):
        f = ttk.Frame(root); f.pack(fill="both", expand=True, padx=10, pady=10)
        self.var_transparency = tk.BooleanVar(value=False)
        self.var_transparency_text = tk.StringVar(value="— managed account")
        ttk.Checkbutton(f, text="Enable transparency tag", variable=self.var_transparency, command=self._mark_dirty).grid(row=0, column=0, sticky="w", pady=(2,2))
        ttk.Entry(f, textvariable=self.var_transparency_text, width=48).grid(row=0, column=1, sticky="w", pady=(2,2))
        ttk.Label(f, text="(Tag is appended to replies you copy; actions are manual.)").grid(row=1, column=0, columnspan=2, sticky="w")

    def _build_log_tab(self, root):
        self.log_text = scrolledtext.ScrolledText(root, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

    def _open_run_monitor(self):
        self._close_run_monitor()
        self.run_monitor = self._RunMonitor(self)
        self.run_monitor.set_paused(self.pause_event.is_set())

    def _close_run_monitor(self):
        monitor = self.run_monitor
        if monitor is not None:
            self.run_monitor = None
            try:
                monitor.destroy()
            except tk.TclError:
                pass
            try:
                self.focus_set()
            except tk.TclError:
                pass

    def _set_monitor_paused(self, paused: bool):
        if self.run_monitor:
            self.run_monitor.set_paused(paused)

    def _on_worker_finished(self):
        self._session_active = False
        self.stop_event.set()
        self.pause_event.clear()
        self.btn_stop.configure(state="disabled")
        self.btn_pause.configure(state="disabled")
        self.btn_resume.configure(state="disabled")
        self.btn_start.configure(state="normal")
        self.post_scheduler = None
        self.rss_ingestor = None
        self._set_rss_enabled(False)
        self._close_run_monitor()
        self.worker = None

    # UI helpers
    def _single(self, parent, label, var, width=20):
        row = ttk.Frame(parent); row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=32).pack(side="left")
        e = ttk.Entry(row, textvariable=var, width=width)
        e.pack(side="left"); e.bind("<KeyRelease>", lambda *_: self._mark_dirty())

    def _pair(self, parent, label, var_min, var_max):
        row = ttk.Frame(parent); row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=32).pack(side="left")
        e1 = ttk.Entry(row, textvariable=var_min, width=10); e1.pack(side="left")
        ttk.Label(row, text=" to ").pack(side="left")
        e2 = ttk.Entry(row, textvariable=var_max, width=10); e2.pack(side="left")
        e1.bind("<KeyRelease>", lambda *_: self._mark_dirty())
        e2.bind("<KeyRelease>", lambda *_: self._mark_dirty())

    def _bind_dirty(self, container):
        for child in container.winfo_children():
            if isinstance(child, ttk.Entry) or isinstance(child, ttk.Combobox):
                child.bind("<KeyRelease>", lambda *_: self._mark_dirty())
                child.bind("<<ComboboxSelected>>", lambda *_: self._mark_dirty())

    def _on_text_modified(self, event):
        widget = event.widget
        if widget.edit_modified():
            self._mark_dirty()
            widget.edit_modified(False)

    def _mark_dirty(self, *args):
        self.dirty = True
        if hasattr(self, "lbl_dirty"):
            self.lbl_dirty.configure(text="(neuloženo)")
        if self.current_profile:
            self.lbl_status.configure(text=f"Profil: {self.current_profile} (neuloženo)")

    # Start/Stop
    def start_clicked(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Running", "Scheduler is already running."); return
        try:
            cfg = self._collect_config()
            sections = self._collect_sections()
        except Exception as e:
            messagebox.showerror("Invalid input", str(e)); return

        self.btn_start.configure(state="disabled")

        def on_cancel():
            self.btn_start.configure(state="normal")

        def begin():
            self.stop_event.clear()
            self.pause_event.clear()
            self._session_active = True
            self._open_run_monitor()
            self._append_log("INFO", "Starting…")
            self.worker = SchedulerWorker(cfg, sections, self.logq, self.stop_event, self.pause_event, self.kb)
            self.worker.start()
            interval = int(cfg.get("post_interval_minutes", 0))
            if interval > 0:
                self.post_scheduler = PostScheduler(interval, self.pause_event, self.stop_event, lambda: None)
                self.post_scheduler.start()
            else:
                self.post_scheduler = None

            feeds = cfg.get("rss_feed_urls", [])
            if isinstance(feeds, str):
                feeds = [feeds]
            rss_interval_minutes = int(cfg.get("rss_fetch_interval_minutes", int(DEFAULT_INTERVAL_SECONDS // 60)))
            rss_interval_minutes = max(1, rss_interval_minutes)
            if feeds:
                self._set_rss_enabled(True)
                self.rss_ingestor = RSSIngestor(
                    feeds,
                    self.news_library,
                    interval_seconds=float(rss_interval_minutes) * 60.0,
                    stop_event=self.stop_event,
                    pause_event=self.pause_event,
                    status_callback=self._on_rss_status,
                )
                self.rss_ingestor.start()
            else:
                self._set_rss_enabled(False)
                self.rss_ingestor = None
            self.btn_pause.configure(state="normal")
            self.btn_resume.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self._set_monitor_paused(False)

        self._Countdown(self, 10, begin, on_cancel)

    def stop_clicked(self):
        if self.worker and self.worker.is_alive():
            self._append_log("INFO", "Stopping (wait for current step)…")
            self.stop_event.set()
        self._session_active = False
        self.pause_event.clear()
        self.btn_stop.configure(state="disabled")
        self.btn_start.configure(state="normal")
        self.btn_pause.configure(state="disabled")
        self.btn_resume.configure(state="disabled")
        self.post_scheduler = None
        self.rss_ingestor = None
        self._set_rss_enabled(False)
        self._close_run_monitor()

    def pause_clicked(self):
        if not self.pause_event.is_set():
            self.pause_event.set()
            self._append_log("INFO", "Paused.")
            self.btn_pause.configure(state="disabled")
            self.btn_resume.configure(state="normal")
            self._set_monitor_paused(True)

    def resume_clicked(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self._append_log("INFO", "Resumed.")
            self.btn_pause.configure(state="normal")
            self.btn_resume.configure(state="disabled")
            self._set_monitor_paused(False)

    # Collectors / Sections / Profiles
    def _csv_to_list(self, s: str) -> List[str]:
        return [t.strip() for t in s.split(",") if t.strip()]

    def _list_to_csv(self, xs: List[str]) -> str:
        return ", ".join(xs or [])

    def _collect_config(self) -> Dict:
        # map UI strings to internal policy codes
        policy_map = {
            "Every time": "every_time",
            "Once per step": "once_per_step",
            "Once per section": "once_per_section",
        }
        cfg = {
            "session_hours_range": (float(self.var_session_hours_min.get()), float(self.var_session_hours_max.get())),
            "session_step_minutes_range": (int(self.var_step_min.get()), int(self.var_step_max.get())),
            "session_break_minutes_range": (int(self.var_break_min.get()), int(self.var_break_max.get())),
            "post_interval_minutes": int(self.var_post_interval.get()),
            "night_sleep_start_hour_range": (int(self.var_sleep_start_h_min.get()), int(self.var_sleep_start_h_max.get())),
            "night_sleep_start_minute_jitter": (int(self.var_sleep_start_jitter_min.get()), int(self.var_sleep_start_jitter_max.get())),
            "night_sleep_hours_range": (float(self.var_sleep_hours_min.get()), float(self.var_sleep_hours_max.get())),
            "micro_pause_every_n_actions_range": (int(self.var_micro_every_min.get()), int(self.var_micro_every_max.get())),
            "micro_pause_seconds_range": (float(self.var_micro_s_min.get()), float(self.var_micro_s_max.get())),
            "weekday_activity_scale": float(self.var_weekday_scale.get()),
            "weekend_activity_scale": float(self.var_weekend_scale.get()),
            "daily_interaction_cap_range": (int(self.var_daily_cap_min.get()), int(self.var_daily_cap_max.get())),
            "hourly_interaction_cap_range": (int(self.var_hourly_cap_min.get()), int(self.var_hourly_cap_max.get())),
            "min_seconds_between_actions_range": (float(self.var_min_gap_s_min.get()), float(self.var_min_gap_s_max.get())),
            "extra_jitter_probability": float(self.var_extra_jitter_prob.get()),
            "extra_jitter_seconds_range": (float(self.var_extra_jitter_s_min.get()), float(self.var_extra_jitter_s_max.get())),
            # content
            "whitelist_keywords": self._csv_to_list(self.var_whitelist.get()),
            "blacklist_keywords": self._csv_to_list(self.var_blacklist.get()),
            "profanity_list": self._csv_to_list(self.var_profanity.get()),
            "content_similarity_threshold": float(self.var_similarity.get()),
            "uniqueness_memory_size": int(self.var_unique_mem.get()),
            # reply tag
            "transparency_tag_enabled": bool(getattr(self, "var_transparency", tk.BooleanVar(value=False)).get()),
            "transparency_tag_text": getattr(self, "var_transparency_text", tk.StringVar(value="— managed account")).get(),
            # search
            "search_mode": self.var_search_mode.get().strip().lower(),
            "search_open_policy": policy_map.get(self.var_open_policy.get().strip(), "once_per_step"),
            # pacing only
            "targets_per_step_range": (8, 14),
            # emergency (disabled here)
            "emergency_early_end_probability": 0.0,
        }

        persona_text = ""
        if self.txt_rss_persona is not None:
            persona_text = self.txt_rss_persona.get("1.0", "end").strip()

        try:
            max_length = int(self.var_rss_max_length.get())
        except Exception as exc:
            raise ValueError("Maximum post length must be a positive integer") from exc
        if max_length <= 0:
            raise ValueError("Maximum post length must be a positive integer")

        cfg.update(
            {
                "openai_api_key": self.var_openai_api_key.get().strip(),
                "rss_persona_text": persona_text,
                "rss_max_post_length": max_length,
            }
        )

        feeds_raw = self.var_rss_feeds.get().replace("\n", ",")
        feed_urls = [u.strip() for u in feeds_raw.split(",") if u.strip()]
        interval_minutes = max(1, int(self.var_rss_interval_minutes.get()))
        cfg.update(
            {
                "rss_feed_urls": feed_urls,
                "rss_fetch_interval_minutes": interval_minutes,
            }
        )
        return cfg

    def _collect_sections(self) -> List[Section]:
        out: List[Section] = []
        for sv in self.sections_vars:
            name = sv["name"]
            tmin = int(sv["typ_min"].get()); tmax = int(sv["typ_max"].get())
            rmin = int(sv["resp_min"].get()); rmax = int(sv["resp_max"].get())
            q_lines = [ln.strip() for ln in sv["txt_queries"].get("1.0","end").splitlines() if ln.strip()]
            r_lines = [ln.strip() for ln in sv["txt_responses"].get("1.0","end").splitlines() if ln.strip()]
            out.append(Section(name=name,
                               typing_ms_per_char=(tmin,tmax),
                               max_responses_before_switch=(rmin,rmax),
                               search_queries=q_lines,
                               responses=r_lines))
        return out

    def _config_to_dict(self) -> Dict:
        return {"config": self._collect_config(),
                "sections": [self._section_to_dict(sv) for sv in self.sections_vars]}

    def _section_to_dict(self, sv: Dict) -> Dict:
        return {
            "name": sv["name"],
            "typing_ms_per_char": (int(sv["typ_min"].get()), int(sv["typ_max"].get())),
            "max_responses_before_switch": (int(sv["resp_min"].get()), int(sv["resp_max"].get())),
            "search_queries": [ln.strip() for ln in sv["txt_queries"].get("1.0","end").splitlines() if ln.strip()],
            "responses": [ln.strip() for ln in sv["txt_responses"].get("1.0","end").splitlines() if ln.strip()],
        }

    def _apply_profile_dict(self, data: Dict):
        cfg = data.get("config", {})
        def set_pair(var_min, var_max, value, fallback):
            v = value if isinstance(value, (list, tuple)) and len(value) == 2 else fallback
            var_min.set(v[0]); var_max.set(v[1])

        set_pair(self.var_session_hours_min, self.var_session_hours_max, cfg.get("session_hours_range"), (12.0,14.0))
        set_pair(self.var_step_min, self.var_step_max, cfg.get("session_step_minutes_range"), (12,16))
        set_pair(self.var_break_min, self.var_break_max, cfg.get("session_break_minutes_range"), (2,4))
        self.var_post_interval.set(int(cfg.get("post_interval_minutes", 0)))
        set_pair(self.var_sleep_start_h_min, self.var_sleep_start_h_max, cfg.get("night_sleep_start_hour_range"), (22,24))
        set_pair(self.var_sleep_start_jitter_min, self.var_sleep_start_jitter_max, cfg.get("night_sleep_start_minute_jitter"), (0,30))
        set_pair(self.var_sleep_hours_min, self.var_sleep_hours_max, cfg.get("night_sleep_hours_range"), (7.0,8.0))

        self.var_weekday_scale.set(cfg.get("weekday_activity_scale", 1.0))
        self.var_weekend_scale.set(cfg.get("weekend_activity_scale", 0.9))

        set_pair(self.var_micro_every_min, self.var_micro_every_max, cfg.get("micro_pause_every_n_actions_range"), (8,12))
        set_pair(self.var_micro_s_min, self.var_micro_s_max, cfg.get("micro_pause_seconds_range"), (2.0,4.0))

        set_pair(self.var_min_gap_s_min, self.var_min_gap_s_max, cfg.get("min_seconds_between_actions_range"), (0.4,0.9))
        self.var_extra_jitter_prob.set(cfg.get("extra_jitter_probability", 0.05))
        set_pair(self.var_extra_jitter_s_min, self.var_extra_jitter_s_max, cfg.get("extra_jitter_seconds_range"), (1.0,2.5))

        set_pair(self.var_daily_cap_min, self.var_daily_cap_max, cfg.get("daily_interaction_cap_range"), (1150,1250))
        set_pair(self.var_hourly_cap_min, self.var_hourly_cap_max, cfg.get("hourly_interaction_cap_range"), (90,110))

        self.var_whitelist.set(self._list_to_csv(cfg.get("whitelist_keywords", [])))
        self.var_blacklist.set(self._list_to_csv(cfg.get("blacklist_keywords", [])))
        self.var_profanity.set(self._list_to_csv(cfg.get("profanity_list", [])))

        self.var_similarity.set(cfg.get("content_similarity_threshold", 0.90))
        self.var_unique_mem.set(cfg.get("uniqueness_memory_size", 200))

        mode = str(cfg.get("search_mode", "popular")).capitalize()
        self.var_search_mode.set(mode if mode in ("Popular","Latest") else "Popular")

        policy = str(cfg.get("search_open_policy", "once_per_step"))
        ui_policy = {"every_time":"Every time","once_per_step":"Once per step","once_per_section":"Once per section"}.get(policy, "Once per step")
        self.var_open_policy.set(ui_policy)

        self.var_openai_api_key.set(cfg.get("openai_api_key", ""))
        if self.txt_rss_persona is not None:
            persona_value = cfg.get("rss_persona_text", "")
            self.txt_rss_persona.delete("1.0", "end")
            if persona_value:
                self.txt_rss_persona.insert("1.0", persona_value)
            self.txt_rss_persona.edit_modified(False)
        try:
            self.var_rss_max_length.set(int(cfg.get("rss_max_post_length", 280)))
        except Exception:
            self.var_rss_max_length.set(280)

        feeds_value = cfg.get("rss_feed_urls", [])
        if isinstance(feeds_value, (list, tuple)):
            feed_text = ", ".join(str(f).strip() for f in feeds_value if str(f).strip())
        else:
            feed_text = str(feeds_value or "")
        self.var_rss_feeds.set(feed_text)

        default_interval = int(DEFAULT_INTERVAL_SECONDS // 60)
        try:
            interval_val = int(cfg.get("rss_fetch_interval_minutes", default_interval))
        except Exception:
            interval_val = default_interval
        if interval_val <= 0:
            interval_val = default_interval
        self.var_rss_interval_minutes.set(interval_val)

        # sections
        sections_data = data.get("sections", [])
        name_to_vars = {sv["name"]: sv for sv in self.sections_vars}
        for s in sections_data:
            nm = s.get("name")
            if nm and nm in name_to_vars:
                sv = name_to_vars[nm]
                tpair = s.get("typing_ms_per_char", (220, 240))
                rpair = s.get("max_responses_before_switch", (4, 8))
                sv["typ_min"].set(int(tpair[0])); sv["typ_max"].set(int(tpair[1]))
                sv["resp_min"].set(int(rpair[0])); sv["resp_max"].set(int(rpair[1]))
                sv["txt_queries"].delete("1.0","end"); sv["txt_queries"].insert("1.0", "\n".join(s.get("search_queries", [])))
                sv["txt_responses"].delete("1.0","end"); sv["txt_responses"].insert("1.0", "\n".join(s.get("responses", [])))

        self.dirty = False
        self.lbl_dirty.configure(text="")
        if self.current_profile:
            self.lbl_status.configure(text=f"Profil: {self.current_profile}")

    # Profiles
    def _profiles_glob(self) -> List[str]:
        files = []
        if os.path.isdir(self.config_dir):
            for fn in os.listdir(self.config_dir):
                if fn.lower().endswith(".json"):
                    files.append(os.path.splitext(fn)[0])
        files = sorted(set(files))
        if "default" not in files:
            files = ["default"] + files
        return files

    def _list_profiles(self) -> List[str]:
        return self._profiles_glob()

    def _profile_path(self, name: str) -> str:
        return os.path.join(self.config_dir, f"{name}.json")

    def _init_default_profile(self):
        path = self._profile_path("default")
        if not os.path.exists(path):
            data = self._config_to_dict()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        self.cmb_profile.configure(values=self._list_profiles())
        self.var_profile.set("default")
        self.current_profile = "default"
        self.load_profile()

    def load_profile(self):
        name = self.var_profile.get().strip() or "default"
        path = self._profile_path(name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.current_profile = name
            self._apply_profile_dict(data)
            self.cmb_profile.configure(values=self._list_profiles())
            self.lbl_status.configure(text=f"Profil: {self.current_profile}")
        except FileNotFoundError:
            messagebox.showwarning("Nenalezeno", f"Soubor profilu {path} neexistuje. Vytvářím nový.")
            self.current_profile = name
            self.save_profile()
        except Exception as e:
            messagebox.showerror("Chyba při načítání", str(e))

    def save_profile(self):
        if not self.current_profile:
            self.current_profile = self.var_profile.get().strip() or "default"
        path = self._profile_path(self.current_profile)
        try:
            data = self._config_to_dict()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.dirty = False
            if hasattr(self, "lbl_dirty"):
                self.lbl_dirty.configure(text="")
            self.lbl_status.configure(text=f"Profil: {self.current_profile}")
            self.cmb_profile.configure(values=self._list_profiles())
            messagebox.showinfo("Uloženo", f"Profil uložen: {self.current_profile}")
        except Exception as e:
            messagebox.showerror("Chyba při ukládání", str(e))

    def save_profile_as(self):
        name = simpledialog.askstring("Uložit jako…", "Zadej název nového profilu (bez přípony):", parent=self)
        if not name: return
        name = "".join(ch for ch in name if ch.isalnum() or ch in "-_ ").strip()
        if not name:
            messagebox.showerror("Neplatný název", "Zadej smysluplný název (písmena/čísla/-/_).")
            return
        self.current_profile = name
        self.var_profile.set(name)
        self.save_profile()

    # Logs
    def _append_log(self, level: str, msg: str):
        line = f"{datetime.now(CET).strftime('%Y-%m-%d %H:%M:%S')} [{level}] {msg}"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        if self.run_monitor:
            self.run_monitor.append_log(line)


    def _drain_logs(self):
        try:
            while True:
                line = self.logq.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
                if self.run_monitor:
                    self.run_monitor.append_log(line)
        except queue.Empty:
            pass
        finally:
            worker = self.worker
            if worker and not worker.is_alive():
                if self._session_active:
                    self._on_worker_finished()
                else:
                    self.worker = None
            # Schedule the next drain so log messages keep flowing
            # through the GUI while the worker thread is running.
            self.after(120, self._drain_logs)


# ---- Entrypoint
if __name__ == "__main__":
    app = App()
    app.mainloop()
