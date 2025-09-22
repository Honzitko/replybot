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

import copy
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
from typing import List, Tuple, Optional, Dict, Set, Callable, Any

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
from urllib.parse import quote as url_quote
try:
    from pynput import keyboard as pynkeyboard
except Exception:  # pragma: no cover - optional dependency
    pynkeyboard = None
from keyboard_controller import KeyboardController, is_app_generated
from news_library import NewsLibrary
from post_library import PostLibrary, create_post_record, store_generated_draft
from post_scheduler import generate_post_from_rss
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

# ``Popular`` search results can surface stickied tweets, ads, or other
# elements that require a deeper initial scroll before reaching fresh posts.
# When the session opens a Popular search we overshoot the first batch of posts

# by sending a fixed burst of nine ``j`` presses.
POPULAR_INITIAL_J_COUNT = 9


POPULAR_SEARCH_MODES: Set[str] = {"popular", "top"}
LATEST_SEARCH_MODES: Set[str] = {"latest", "nejnovější", "nejnovejsi", "live"}


def normalize_search_mode(mode: str) -> str:
    return str(mode or "").strip().lower()


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
    Popular: https://x.com/search?q=<q>&src=typed_query&f=top
    Latest:  https://x.com/search?q=<q>&src=typed_query&f=live
    """

    q = url_quote(query or "")
    mode_normalized = normalize_search_mode(mode)
    url = f"https://x.com/search?q={q}&src=typed_query"

    if mode_normalized in LATEST_SEARCH_MODES:
        url += "&f=live"
    elif mode_normalized in POPULAR_SEARCH_MODES:
        url += "&f=top"

    return url

# ---- Section model
@dataclass
class Section:
    name: str
    typing_ms_per_char: Tuple[int, int] = (220, 240)
    max_responses_before_switch: Tuple[int, int] = (4, 8)
    search_queries: List[str] = field(default_factory=list)
    responses: List[str] = field(default_factory=list)
    search_mode: str = "popular"
    enabled: bool = True
    order: int = 0
    _query_cycle_index: int = field(default=0, init=False, repr=False)
    def pick_typing_speed(self) -> int: return random.randint(*self.typing_ms_per_char)
    def pick_max_responses(self) -> int: return random.randint(*self.max_responses_before_switch)
    def pick_query(self) -> Optional[str]:
        if not self.search_queries:
            return None
        idx = self._query_cycle_index % len(self.search_queries)
        query = self.search_queries[idx]
        self._query_cycle_index = (idx + 1) % len(self.search_queries)
        return query
    def pick_response(self) -> Optional[str]: return random.choice(self.responses) if self.responses else None

# ---- Defaults (edit in UI later)
DEFAULT_SECTIONS_SEED = [
    {
        "name": "Sharp & Direct",
        "enabled": True,
        "typing_ms_per_char": (220, 240),
        "max_responses_before_switch": (6, 12),
        "search_queries": [
            "morning performance",
            "industry trends",
            "team updates",
        ],
        "responses": [
            "Strong start to the day. Let’s build.",
            "Morning momentum sets the tone.",
            "Focused and ready to execute.",
        ],
    },
    {
        "name": "Professional & Brief",
        "enabled": True,
        "typing_ms_per_char": (220, 240),
        "max_responses_before_switch": (5, 9),
        "search_queries": [
            "client progress",
            "feature rollouts",
        ],
        "responses": [
            "Starting strong and staying consistent.",
            "Vision only matters with execution.",
            "Early action sets the pace.",
        ],
    },
    {
        "name": "Builder Mindset",
        "enabled": True,
        "typing_ms_per_char": (220, 240),
        "max_responses_before_switch": (5, 10),
        "search_queries": [
            "roadmap items",
            "dev insights",
        ],
        "responses": [
            "Every task is a brick in the wall.",
            "Opportunities don’t knock, they’re built.",
            "Build momentum early.",
        ],
    },
    {
        "name": "Networking & Collab",
        "enabled": True,
        "typing_ms_per_char": (220, 240),
        "max_responses_before_switch": (4, 8),
        "search_queries": [
            "partner announcements",
            "collab opportunities",
        ],
        "responses": [
            "Open to smart partnerships—let’s align.",
            "If you’re building, let’s connect.",
            "Partnerships create possibilities.",
        ],
    },
    {
        "name": "Motivation Lite",
        "enabled": True,
        "typing_ms_per_char": (220, 240),
        "max_responses_before_switch": (5, 9),
        "search_queries": [
            "team shoutouts",
        ],
        "responses": [
            "Make it count today.",
            "Results over opinions.",
            "Keep stacking small wins.",
        ],
    },
    {
        "name": "Execution Mode",
        "enabled": True,
        "typing_ms_per_char": (220, 240),
        "max_responses_before_switch": (6, 12),
        "search_queries": [
            "status check",
            "backlog review",
        ],
        "responses": [
            "The plan is simple: execute.",
            "Decide, then execute.",
            "Clarity creates confidence.",
        ],
    },
    {
        "name": "Weekend Chill (still pro)",
        "enabled": True,
        "typing_ms_per_char": (220, 260),
        "max_responses_before_switch": (3, 6),
        "search_queries": [
            "light topics",
            "community notes",
        ],
        "responses": [
            "Fresh start, same fire.",
            "Stay in motion.",
            "Good energy, good outcomes.",
        ],
    },
    {
        "name": "Insightful & Calm",
        "enabled": True,
        "typing_ms_per_char": (220, 260),
        "max_responses_before_switch": (4, 8),
        "search_queries": [
            "market notes",
            "customer wins",
        ],
        "responses": [
            "Focus on what compounds.",
            "Daily effort writes the future.",
            "Direction beats speed.",
        ],
    },
    {
        "name": "Fast & To the Point",
        "enabled": True,
        "typing_ms_per_char": (220, 240),
        "max_responses_before_switch": (7, 13),
        "search_queries": [
            "quick scans",
        ],
        "responses": [
            "Keep it moving.",
            "Outperform yesterday.",
            "Push the line forward.",
        ],
    },
    {
        "name": "Creator/Brand Voice",
        "enabled": True,
        "typing_ms_per_char": (220, 240),
        "max_responses_before_switch": (5, 10),
        "search_queries": [
            "brand mentions",
            "community threads",
        ],
        "responses": [
            "Let’s turn ideas into outcomes.",
            "Consistency is the advantage.",
            "Show up, level up.",
        ],
    },
]

# ---- Post scheduling


class PostDraftQueue:
    """Very small in-memory FIFO queue for composed posts.

    The queue stores full metadata dictionaries so operators can inspect the
    provenance of each draft before acting on it.  Items are defensive copies of
    the dictionaries returned by :mod:`post_library` helpers.
    """

    def __init__(self) -> None:
        self.posts: List[Dict[str, Any]] = []

    def push(self, draft: Dict[str, Any] | str) -> Optional[Dict[str, Any]]:
        """Store ``draft`` for later processing.

        ``draft`` may be a plain string (legacy behaviour) or a mapping with
        additional metadata.  Empty drafts are ignored and ``None`` is
        returned.
        """

        if isinstance(draft, dict):
            known_keys = {"id", "text", "source", "created_at", "rss_link", "status", "metadata"}
            extra = {k: draft[k] for k in draft.keys() - known_keys}
            metadata = draft.get("metadata")
            if extra:
                if not isinstance(metadata, dict):
                    metadata = {}
                else:
                    metadata = copy.deepcopy(metadata)
                metadata.setdefault("legacy", {}).update(extra)
            record = create_post_record(
                draft.get("text", ""),
                source=draft.get("source"),
                created_at=draft.get("created_at"),
                rss_link=draft.get("rss_link"),
                status=draft.get("status"),
                metadata=metadata,
                record_id=draft.get("id"),
            )
        else:
            record = create_post_record(str(draft or ""))

        if not record["text"]:
            return None

        snapshot = copy.deepcopy(record)
        self.posts.append(snapshot)
        return snapshot


class PostEditor(tk.Toplevel):
    """Simple dialog allowing the user to compose, review, and queue drafts."""

    def __init__(
        self,
        master: tk.Misc,
        queue: PostDraftQueue,
        *,
        initial_text: str = "",
        on_save: Optional[Callable[[str], None]] = None,
        title: str = "Post draft",
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.queue = queue
        self.resizable(True, True)
        self.on_save = on_save

        self.txt = scrolledtext.ScrolledText(self, width=60, height=10)
        self.txt.pack(fill="both", expand=True, padx=10, pady=10)
        if initial_text:
            self.txt.insert("1.0", initial_text)

        btn = ttk.Button(self, text="Save", command=self._save)
        btn.pack(pady=(0, 10))

        # Allow closing via window manager controls
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        try:
            self.txt.focus_set()
        except tk.TclError:
            pass

    def _save(self) -> None:
        content = self.txt.get("1.0", "end").strip()
        if content:
            if self.on_save is not None:
                self.on_save(content)
            else:
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
        indexed_sections = list(enumerate(sections))
        indexed_sections.sort(key=lambda item: (getattr(item[1], "order", item[0]), item[0]))
        self.sections = [sec for _idx, sec in indexed_sections]
        for idx, section in enumerate(self.sections):
            try:
                section.order = idx
            except Exception:
                pass
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
        self.search_open_policy = str(self.cfg.get("search_open_policy", "once_per_step")).lower()
        for section in self.sections:
            normalized = normalize_search_mode(getattr(section, "search_mode", "popular"))
            if normalized in LATEST_SEARCH_MODES:
                section.search_mode = "latest"
            elif normalized in POPULAR_SEARCH_MODES:
                section.search_mode = "popular"
            else:
                section.search_mode = "popular"
        # tracking to avoid opening many tabs
        self._opened_sections: Set[str] = set()
        self._opened_this_step: bool = False
        self._browser_opened = False
        self._popular_initial_scroll_pending = False

    def _log(self, level, msg):
        ts = datetime.now(CET).strftime("%Y-%m-%d %H:%M:%S")
        self.logq.put(f"{ts} [{level}] {msg}")

    def _is_popular_search_mode(self, mode: str) -> bool:
        return normalize_search_mode(mode) in POPULAR_SEARCH_MODES

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
        self._popular_initial_scroll_pending = False

    def _open_search(self, query: str, section: Section):
        self._popular_initial_scroll_pending = False
        section_name = getattr(section, "name", "section")
        raw_mode = getattr(section, "search_mode", "popular")
        mode_norm = normalize_search_mode(raw_mode)
        if mode_norm in LATEST_SEARCH_MODES:
            effective_mode = "latest"
        elif mode_norm in POPULAR_SEARCH_MODES:
            effective_mode = "popular"
        else:
            effective_mode = "popular"
        section.search_mode = effective_mode
        url = build_search_url(query, effective_mode)
        try:
            elapsed = ensure_connection(url, timeout=5)
            self._log("INFO", f"Connection verified in {elapsed:.2f}s")
        except Exception as e:
            self._log("ERROR", f"Connection check failed: {e}")
            return
        mode_label = "Latest" if effective_mode == "latest" else "Popular"
        self._log("INFO", f"Open search for {section_name} [{mode_label}]: {url}")
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

        if self._is_popular_search_mode(effective_mode):
            self._popular_initial_scroll_pending = True

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
        if getattr(self, "_popular_initial_scroll_pending", False):

            presses = POPULAR_INITIAL_J_COUNT

            self._popular_initial_scroll_pending = False
        else:
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
        self._log("INFO", f"Daily cap={self.daily_cap} | Hourly cap={self.hourly_cap} | Open policy={self.search_open_policy}")
        modes_summary = ", ".join(
            f"{section.name}→{'Latest' if section.search_mode == 'latest' else 'Popular'}"
            for section in self.sections
        )
        if modes_summary:
            self._log("INFO", f"Section search modes: {modes_summary}")
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

                processed = 0

                for section in self.sections:
                    if (self.stop_event.is_set() or
                            datetime.now(CET) >= step_deadline or
                            processed >= targets_goal):
                        break
                    self._wait_if_paused()
                    max_responses = max(1, section.pick_max_responses())
                    remaining_attempts = max_responses
                    mode_label = "Latest" if section.search_mode == "latest" else "Popular"
                    self._log("INFO", f"Section → {section.name} [{mode_label}] (limit {max_responses})")

                    current_query = section.pick_query() or "general discovery"
                    if self._should_open_search_now(section.name):
                        self._open_search(current_query, section)
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
        self.posts_tree: Optional[ttk.Treeview] = None
        self.news_tree: Optional[ttk.Treeview] = None
        self.run_monitor: Optional["App._RunMonitor"] = None
        self._session_active: bool = False
        self._news_items_by_key: Dict[str, Dict[str, Any]] = {}
        self.sections_vars: List[Dict[str, Any]] = []
        self.sections_notebook: Optional[ttk.Notebook] = None
        self.section_templates: List[Dict[str, Any]] = copy.deepcopy(DEFAULT_SECTIONS_SEED)
        self.section_delete_button: Optional[ttk.Button] = None

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
        self.tab_news = ttk.Frame(self.nb)
        self.tab_review = ttk.Frame(self.nb)
        self.tab_log = ttk.Frame(self.nb)

        self.nb.add(self.tab_settings, text="Nastavení")
        self.nb.add(self.tab_session, text="Session & Sleep")
        self.nb.add(self.tab_behavior, text="Humanization & Behavior")
        self.nb.add(self.tab_guardrails, text="Guardrails")
        self.nb.add(self.tab_sections, text="Sections (Queries/Responses)")
        self.nb.add(self.tab_posts, text="Posts")
        self.nb.add(self.tab_news, text="RSS Review")
        self.nb.add(self.tab_review, text="Review & Transparency")
        self.nb.add(self.tab_log, text="Logs")

        self._build_settings_tab(self.tab_settings)
        self._build_session_tab(self.tab_session)
        self._build_behavior_tab(self.tab_behavior)
        self._build_guardrails_tab(self.tab_guardrails)
        self._build_sections_tab(self.tab_sections)
        self._build_posts_tab(self.tab_posts)
        self._build_news_tab(self.tab_news)
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

        # Search open policy (search filter now per section)
        row3 = ttk.LabelFrame(f, text="Search")
        row3.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(12,0))

        self.var_open_policy = tk.StringVar(value="Once per step")
        ttk.Label(row3, text="Open policy:").grid(row=0, column=0, sticky="w", padx=8)
        cb2 = ttk.Combobox(row3, textvariable=self.var_open_policy, state="readonly",
                           values=["Every time","Once per step","Once per section"], width=18)
        cb2.grid(row=0, column=1, sticky="w", padx=(6,0))
        cb2.bind("<<ComboboxSelected>>", lambda *_: self._mark_dirty())

        ttk.Label(row3, text=(
            "Popular → &f=top; Latest → &f=live. Choose the search filter for each section in its tab. "
            "Open policy controls how often a tab is opened."
        )).grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(6,2))

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

    def _open_post_editor(self, event=None, draft: Optional[Dict[str, Any]] = None):
        """Open the :class:`PostEditor` dialog for a new or existing draft."""

        self._open_draft_editor(draft)

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
        container = ttk.Frame(root)
        container.pack(fill="both", expand=True, padx=6, pady=6)

        controls = ttk.Frame(container)
        controls.pack(fill="x", pady=(0, 6))

        add_menu = ttk.Menubutton(controls, text="Add section")
        add_menu_menu = tk.Menu(add_menu, tearoff=False)
        add_menu_menu.add_command(label="Blank section", command=self._add_blank_section)
        if DEFAULT_SECTIONS_SEED:
            add_menu_menu.add_separator()
            for seed in DEFAULT_SECTIONS_SEED:
                label = str(seed.get("name") or "Section")
                add_menu_menu.add_command(
                    label=f"Template: {label}",
                    command=lambda template=seed: self._add_section_from_seed(template),
                )
        add_menu["menu"] = add_menu_menu
        add_menu.pack(side="left")

        delete_btn = ttk.Button(controls, text="Delete current section", command=self._delete_current_section)
        delete_btn.pack(side="left", padx=(6, 0))
        self.section_delete_button = delete_btn

        self.sections_notebook = ttk.Notebook(container)
        self.sections_notebook.pack(fill="both", expand=True)
        self.sections_notebook.bind("<<NotebookTabChanged>>", self._update_section_controls)

        if not self.section_templates:
            self.section_templates = copy.deepcopy(DEFAULT_SECTIONS_SEED)

        for seed in self.section_templates:
            self._append_section_tab(seed, select=False, mark_dirty=False)

        self._sync_section_tab_order()
        self._update_section_controls()

    def _new_section_seed(self) -> Dict[str, Any]:
        return {
            "name": "",
            "enabled": True,
            "typing_ms_per_char": (220, 240),
            "max_responses_before_switch": (4, 8),
            "search_queries": [],
            "responses": [],
            "search_mode": "popular",
        }

    def _add_blank_section(self) -> None:
        self._add_section_from_seed(self._new_section_seed())

    def _add_section_from_seed(self, seed: Dict[str, Any]) -> None:
        if not isinstance(seed, dict):
            seed = {}
        base = copy.deepcopy(seed)
        if "name" not in base:
            base["name"] = ""
        if "typing_ms_per_char" not in base:
            base["typing_ms_per_char"] = (220, 240)
        if "max_responses_before_switch" not in base:
            base["max_responses_before_switch"] = (4, 8)
        if "search_queries" not in base:
            base["search_queries"] = []
        if "responses" not in base:
            base["responses"] = []
        if "search_mode" not in base:
            base["search_mode"] = "popular"
        if "enabled" not in base:
            base["enabled"] = True
        base.pop("order", None)

        self.section_templates.append(base)
        self._append_section_tab(base, select=True, mark_dirty=True)

    def _append_section_tab(self, seed: Dict[str, Any], *, select: bool = True, mark_dirty: bool = True) -> Optional[Dict[str, Any]]:
        notebook = getattr(self, "sections_notebook", None)
        if notebook is None:
            return None

        seed_name = str(seed.get("name", "") or "").strip()
        fallback_name = seed_name or f"Section {len(self.sections_vars) + 1}"

        typ_rng = seed.get("typing_ms_per_char", (220, 240))
        if not isinstance(typ_rng, (list, tuple)) or len(typ_rng) != 2:
            typ_rng = (220, 240)
        try:
            typ_min_val = int(typ_rng[0])
            typ_max_val = int(typ_rng[1])
        except Exception:
            typ_min_val, typ_max_val = 220, 240

        resp_rng = seed.get("max_responses_before_switch", (4, 8))
        if not isinstance(resp_rng, (list, tuple)) or len(resp_rng) != 2:
            resp_rng = (4, 8)
        try:
            resp_min_val = int(resp_rng[0])
            resp_max_val = int(resp_rng[1])
        except Exception:
            resp_min_val, resp_max_val = 4, 8

        raw_queries = seed.get("search_queries", [])
        if isinstance(raw_queries, str):
            queries = [ln.strip() for ln in raw_queries.splitlines() if ln.strip()]
        elif isinstance(raw_queries, (list, tuple)):
            queries = [str(ln).strip() for ln in raw_queries if str(ln).strip()]
        else:
            queries = []

        raw_responses = seed.get("responses", [])
        if isinstance(raw_responses, str):
            responses = [ln.strip() for ln in raw_responses.splitlines() if ln.strip()]
        elif isinstance(raw_responses, (list, tuple)):
            responses = [str(ln).strip() for ln in raw_responses if str(ln).strip()]
        else:
            responses = []

        enabled_default = bool(seed.get("enabled", True))

        order_default = seed.get("order")
        try:
            order_value = int(order_default)
        except Exception:
            order_value = len(self.sections_vars)
        order_var = tk.IntVar(value=order_value)

        tab = ttk.Frame(notebook)
        notebook.add(tab, text=self._section_tab_title(seed_name, fallback=fallback_name))

        sv: Dict[str, Any] = {
            "default_name": fallback_name,
            "default_enabled": enabled_default,
            "order_var": order_var,
            "tab": tab,
            "default_index": len(self.sections_vars),
            "seed_ref": seed,
        }

        v_typ_min = tk.IntVar(value=typ_min_val)
        v_typ_max = tk.IntVar(value=typ_max_val)
        v_resp_min = tk.IntVar(value=resp_min_val)
        v_resp_max = tk.IntVar(value=resp_max_val)
        name_var = tk.StringVar(value=seed_name)
        enabled_var = tk.BooleanVar(value=enabled_default)

        mode_seed = normalize_search_mode(seed.get("search_mode", "popular"))
        if mode_seed in LATEST_SEARCH_MODES:
            default_mode_ui = "Latest"
        else:
            default_mode_ui = "Popular"
        mode_var = tk.StringVar(value=default_mode_ui)

        sv.update(
            {
                "name_var": name_var,
                "enabled_var": enabled_var,
                "typ_min": v_typ_min,
                "typ_max": v_typ_max,
                "resp_min": v_resp_min,
                "resp_max": v_resp_max,
                "mode_var": mode_var,
                "default_mode": default_mode_ui,
            }
        )

        col = ttk.Frame(tab)
        col.pack(fill="both", expand=True, padx=10, pady=10)

        header = ttk.Frame(col)
        header.pack(fill="x", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Section name:").grid(row=0, column=0, sticky="w")
        entry_name = ttk.Entry(header, textvariable=name_var)
        entry_name.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        entry_name.bind("<KeyRelease>", lambda *_: self._mark_dirty())
        ttk.Checkbutton(header, text="Enabled", variable=enabled_var, command=self._mark_dirty).grid(
            row=0, column=2, sticky="w", padx=(12, 0)
        )
        ttk.Label(header, text="Search filter:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        mode_cb = ttk.Combobox(
            header,
            textvariable=mode_var,
            state="readonly",
            values=["Popular", "Latest"],
            width=12,
        )
        mode_cb.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        mode_cb.bind("<<ComboboxSelected>>", lambda *_: self._mark_dirty())

        controls = ttk.Frame(header)
        controls.grid(row=0, column=3, sticky="e", padx=(12, 0))
        ttk.Button(
            controls,
            text="↑",
            width=2,
            command=lambda sv=sv: self._move_section_order(sv, -1),
        ).pack(side="top")
        ttk.Button(
            controls,
            text="↓",
            width=2,
            command=lambda sv=sv: self._move_section_order(sv, 1),
        ).pack(side="top", pady=(2, 0))

        def update_tab_label(
            *_,
            notebook=notebook,
            current_tab=tab,
            var=name_var,
            default=fallback_name,
        ):
            notebook.tab(
                current_tab,
                text=self._section_tab_title(var.get(), fallback=default),
            )

        update_tab_label()
        name_var.trace_add("write", update_tab_label)

        self._pair(col, "Typing ms/char (min/max)", v_typ_min, v_typ_max)
        self._pair(col, "Max responses before switch (min/max)", v_resp_min, v_resp_max)

        ttk.Label(col, text="Search queries (one per line):").pack(anchor="w", pady=(8, 2))
        txt_q = scrolledtext.ScrolledText(col, height=6)
        if queries:
            txt_q.insert("1.0", "\n".join(queries))
        txt_q.pack(fill="both", expand=False)

        ttk.Label(col, text="Responses (one per line):").pack(anchor="w", pady=(8, 2))
        txt_r = scrolledtext.ScrolledText(col, height=8)
        if responses:
            txt_r.insert("1.0", "\n".join(responses))
        txt_r.pack(fill="both", expand=True)

        txt_q.bind("<<Modified>>", self._on_text_modified)
        txt_r.bind("<<Modified>>", self._on_text_modified)

        sv.update(
            {
                "txt_queries": txt_q,
                "txt_responses": txt_r,
            }
        )

        self.sections_vars.append(sv)
        self._ordered_section_vars()
        self._sync_section_tab_order()

        if select:
            try:
                notebook.select(tab)
                entry_name.focus_set()
            except tk.TclError:
                pass

        self._update_section_controls()
        if mark_dirty:
            self._mark_dirty()

        return sv

    def _delete_current_section(self) -> None:
        notebook = getattr(self, "sections_notebook", None)
        if notebook is None:
            return
        current = notebook.select()
        if not current:
            return
        try:
            tab_widget = notebook.nametowidget(current)
        except KeyError:
            tab_widget = None
        if tab_widget is None:
            return
        for sv in list(self.sections_vars):
            if sv.get("tab") is tab_widget:
                self._delete_section_sv(sv, mark_dirty=True)
                break

    def _delete_section_sv(self, sv: Dict[str, Any], *, mark_dirty: bool) -> None:
        notebook = getattr(self, "sections_notebook", None)
        tab = sv.get("tab")
        if notebook is not None and tab is not None:
            try:
                notebook.forget(tab)
            except tk.TclError:
                pass
            try:
                tab.destroy()
            except tk.TclError:
                pass

        if sv in self.sections_vars:
            self.sections_vars.remove(sv)

        seed_ref = sv.get("seed_ref")
        if seed_ref is not None:
            self.section_templates = [item for item in self.section_templates if item is not seed_ref]

        self._ordered_section_vars()
        self._sync_section_tab_order()

        if notebook is not None:
            tabs = notebook.tabs()
            if tabs:
                current = notebook.select()
                if current not in tabs:
                    notebook.select(tabs[0])

        self._update_section_controls()
        if mark_dirty:
            self._mark_dirty()

    def _update_section_controls(self, *_args) -> None:
        btn = getattr(self, "section_delete_button", None)
        notebook = getattr(self, "sections_notebook", None)
        if btn is None or notebook is None:
            return
        tabs = notebook.tabs() if notebook else []
        state = "normal" if tabs else "disabled"
        try:
            btn.configure(state=state)
        except tk.TclError:
            pass

    def _ensure_section_tab_count(self, desired: int) -> None:
        current = len(self.sections_vars)
        if desired < 0:
            desired = 0
        if current < desired:
            for _ in range(desired - current):
                seed = self._new_section_seed()
                self.section_templates.append(seed)
                self._append_section_tab(seed, select=False, mark_dirty=False)
        elif current > desired:
            ordered = self._ordered_section_vars()
            for sv in list(ordered[desired:]):
                self._delete_section_sv(sv, mark_dirty=False)


    def _build_news_tab(self, root):
        wrapper = ttk.Frame(root)
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        table_frame = ttk.Frame(wrapper)
        table_frame.pack(side="left", fill="both", expand=True)

        columns = ("status", "title", "summary", "source", "published")
        self.news_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self.news_tree.heading("status", text="Status")
        self.news_tree.heading("title", text="Title")
        self.news_tree.heading("summary", text="Summary")
        self.news_tree.heading("source", text="Source")
        self.news_tree.heading("published", text="Published")
        self.news_tree.column("status", width=100, stretch=False, anchor="w")
        self.news_tree.column("title", width=280, stretch=True, anchor="w")
        self.news_tree.column("summary", width=360, stretch=True, anchor="w")
        self.news_tree.column("source", width=160, stretch=False, anchor="w")
        self.news_tree.column("published", width=150, stretch=False, anchor="w")
        self.news_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.news_tree.yview)
        scroll.pack(side="left", fill="y")
        self.news_tree.configure(yscrollcommand=scroll.set)
        self.news_tree.bind("<Double-Button-1>", lambda *_: self._news_generate_draft())

        btns = ttk.Frame(wrapper)
        btns.pack(side="left", fill="y", padx=(10, 0))
        ttk.Button(btns, text="Generate draft", command=self._news_generate_draft).pack(fill="x")
        ttk.Button(btns, text="Mark processed", command=lambda: self._news_mark_status("processed")).pack(
            fill="x", pady=(8, 0)
        )
        ttk.Button(btns, text="Ignore", command=lambda: self._news_mark_status("ignored")).pack(
            fill="x", pady=(4, 0)
        )
        ttk.Button(btns, text="Reset status", command=lambda: self._news_mark_status("new")).pack(
            fill="x", pady=(12, 0)
        )
        ttk.Button(btns, text="Refresh", command=self._refresh_news_items).pack(fill="x", pady=(24, 0))

        self._refresh_news_items()

    def _refresh_news_items(self) -> None:
        tree = getattr(self, "news_tree", None)
        if tree is None:
            return

        previous_selection = tree.selection()
        for item_id in tree.get_children():
            tree.delete(item_id)

        try:
            items = self.news_library.get_items()
        except Exception as exc:
            logging.error("Failed to read RSS items: %s", exc, exc_info=True)
            items = []

        mapping: Dict[str, Dict[str, Any]] = {}
        for entry in items:
            if not isinstance(entry, dict):
                continue
            key = self._news_item_key(entry)
            mapping[key] = entry

            status_raw = str(entry.get("status", "new") or "").strip().lower()
            if status_raw not in getattr(self.news_library, "VALID_STATUSES", {"new", "processed", "ignored"}):
                status_raw = "new"
            status_display = status_raw.title() if status_raw else "New"

            title = str(entry.get("title") or entry.get("link") or "—")
            title = " ".join(title.split())
            if len(title) > 120:
                title = title[:117] + "…"

            summary = str(entry.get("summary") or "")
            summary = " ".join(summary.split())
            if len(summary) > 200:
                summary = summary[:197] + "…"

            source = str(entry.get("source") or "—").strip() or "—"
            if len(source) > 60:
                source = source[:57] + "…"

            published = self._format_news_timestamp(entry.get("published"))

            tree.insert(
                "",
                "end",
                iid=key,
                values=(status_display, title, summary, source, published),
            )

        self._news_items_by_key = mapping

        if previous_selection:
            for iid in previous_selection:
                if iid in mapping:
                    tree.selection_set(iid)
                    tree.focus(iid)
                    tree.see(iid)
                    break

    def _news_item_key(self, item: Dict[str, Any]) -> str:
        ident = str(item.get("id") or item.get("link") or item.get("title") or "")
        published = str(item.get("published") or "")
        return f"{ident}|{published}"

    def _get_selected_news_item(self) -> Optional[Dict[str, Any]]:
        tree = getattr(self, "news_tree", None)
        if tree is None:
            return None
        selection = tree.selection()
        if not selection:
            return None
        key = selection[0]
        return self._news_items_by_key.get(key)

    def _news_generate_draft(self) -> Optional[Dict[str, Any]]:
        item = self._get_selected_news_item()
        if not item:
            return None
        return self._generate_draft_from_news_item(item)

    def _generate_draft_from_news_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            settings = self._collect_config()
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return None

        text, _media_path = generate_post_from_rss(item, settings)
        record = self.ingest_generated_draft(text, rss_item=item, source="rss")

        if record:
            try:
                self.news_library.mark_processed(item)
            except Exception as exc:
                logging.error("Failed to update RSS item status: %s", exc, exc_info=True)

        self._refresh_news_items()
        return record

    def _news_mark_status(self, status: str) -> None:
        item = self._get_selected_news_item()
        if not item:
            return

        try:
            if status == "processed":
                self.news_library.mark_processed(item)
            elif status == "ignored":
                self.news_library.mark_ignored(item)
            else:
                self.news_library.reset_status(item)
        except ValueError as exc:
            messagebox.showerror("Invalid status", str(exc))
        except KeyError:
            messagebox.showwarning("Missing item", "The selected RSS entry is no longer available.")
        except Exception as exc:
            logging.error("Failed to update RSS item: %s", exc, exc_info=True)
        finally:
            self._refresh_news_items()

    def _format_news_timestamp(self, value: Any) -> str:
        if not value:
            return "—"
        try:
            dt = datetime.fromisoformat(str(value))
        except Exception:
            return str(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            return dt.astimezone(CET).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return dt.strftime("%Y-%m-%d %H:%M")

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

        columns = ("status", "source", "created", "preview")
        self.posts_tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self.posts_tree.heading("status", text="Status")
        self.posts_tree.heading("source", text="Source")
        self.posts_tree.heading("created", text="Created")
        self.posts_tree.heading("preview", text="Preview")
        self.posts_tree.column("status", width=90, stretch=False, anchor="w")
        self.posts_tree.column("source", width=180, stretch=False, anchor="w")
        self.posts_tree.column("created", width=140, stretch=False, anchor="w")
        self.posts_tree.column("preview", width=520, stretch=True, anchor="w")
        self.posts_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.posts_tree.yview)
        scroll.pack(side="left", fill="y")
        self.posts_tree.configure(yscrollcommand=scroll.set)
        self.posts_tree.bind("<Double-Button-1>", lambda *_: self._open_selected_draft())

        btns = ttk.Frame(f)
        btns.pack(side="left", fill="y", padx=(10, 0))
        ttk.Button(btns, text="Compose…", command=self._compose_post).pack(fill="x")
        ttk.Button(btns, text="Open draft", command=self._open_selected_draft).pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="Mark used", command=lambda: self._mark_selected_draft("used")).pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Archive", command=lambda: self._mark_selected_draft("archived")).pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="Delete", command=self._delete_post).pack(fill="x", pady=(12, 0))

        self._refresh_posts()

    def _refresh_posts(self):
        tree = getattr(self, "posts_tree", None)
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)

        for record in self.post_library.get_entries():
            preview = str(record.get("text", "") or "").replace("\n", " ")
            preview = " ".join(preview.split())
            if len(preview) > 160:
                preview = preview[:157] + "…"
            created = self._format_draft_created(record.get("created_at"))
            source = self._format_draft_source(record)
            status = str(record.get("status", "draft")).title()
            record_id = record.get("id")
            if not record_id:
                continue
            tree.insert("", "end", iid=str(record_id), values=(status, source, created, preview))

    def _open_draft_editor(self, record: Optional[Dict[str, Any]] = None) -> None:
        if record is None:
            def on_save(content: str) -> None:
                new_record = self.post_library.add_post(content, source="manual")
                if new_record:
                    self.post_queue.push(new_record)
                    self._refresh_posts()

            PostEditor(self, self.post_queue, on_save=on_save, title="Compose post")
            return

        def on_save(content: str) -> None:
            record_id = record.get("id")
            updated = self.post_library.update_post(record_id, content)
            target = updated or self.post_library.get_post_by_id(record_id)
            if target:
                self.post_queue.push(target)
            self._refresh_posts()

        PostEditor(
            self,
            self.post_queue,
            initial_text=record.get("text", ""),
            on_save=on_save,
            title="Review draft",
        )

    def _compose_post(self):
        self._open_draft_editor()

    def _open_selected_draft(self):
        record = self._get_selected_record()
        if not record:
            return
        self._open_draft_editor(record)

    def _mark_selected_draft(self, status: str) -> None:
        record = self._get_selected_record()
        if not record:
            return
        record_id = record.get("id")
        try:
            self.post_library.set_status(record_id, status)
        except ValueError as exc:
            messagebox.showerror("Invalid status", str(exc))
            return
        self._refresh_posts()

    def ingest_generated_draft(
        self,
        text: str,
        rss_item: Optional[Dict[str, Any]] = None,
        *,
        source: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Store a generated draft with provenance and refresh the UI."""

        record = store_generated_draft(
            self.post_library,
            self.post_queue,
            text,
            rss_item=rss_item,
            source=source,
        )
        if record:
            self._refresh_posts()
        return record

    def _delete_post(self):
        record = self._get_selected_record()
        if not record:
            return
        preview = " ".join(str(record.get("text", "") or "").split())
        preview = preview[:80] + ("…" if len(preview) > 80 else "")
        if messagebox.askyesno("Delete Post", f"Delete the selected draft?\n\n{preview}"):
            self.post_library.delete_post(record.get("id"))
            self._refresh_posts()

    def _get_selected_post_id(self) -> Optional[str]:
        tree = getattr(self, "posts_tree", None)
        if tree is None:
            return None
        selection = tree.selection()
        if not selection:
            return None
        return selection[0]

    def _get_selected_record(self) -> Optional[Dict[str, Any]]:
        record_id = self._get_selected_post_id()
        if not record_id:
            return None
        return self.post_library.get_post_by_id(record_id)

    def _format_draft_source(self, record: Dict[str, Any]) -> str:
        source = str(record.get("source") or "manual").strip()
        metadata = record.get("metadata") or {}
        rss_meta = metadata.get("rss") if isinstance(metadata, dict) else None
        if source.lower() in {"", "rss"} and isinstance(rss_meta, dict):
            feed = rss_meta.get("source") or rss_meta.get("title")
            link = rss_meta.get("link") or rss_meta.get("url")
            candidate = feed or link or "rss"
        else:
            candidate = source or "manual"
        candidate = candidate or "manual"
        candidate = candidate.strip() or "manual"
        if candidate.lower() == "manual":
            display = "Manual"
        else:
            display = candidate
        if len(display) > 40:
            display = display[:37] + "…"
        return display

    def _format_draft_created(self, created_at: Any) -> str:
        if not created_at:
            return "—"
        try:
            dt = datetime.fromisoformat(str(created_at))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(CET).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(created_at)

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

    def _section_tab_title(self, value: str, fallback: Optional[str] = None) -> str:
        text = str(value or "").strip()
        if not text and fallback:
            text = str(fallback)
        if not text:
            text = "Section"
        return text[:16] + ("…" if len(text) > 16 else "")

    def _section_order_value(self, sv: Dict[str, Any], fallback: int = 0) -> int:
        order_var = sv.get("order_var")
        if order_var is not None:
            try:
                return int(order_var.get())
            except Exception:
                pass
        try:
            return int(sv.get("default_index", fallback))
        except Exception:
            return fallback

    def _section_order_key(self, sv: Dict[str, Any]) -> Tuple[int, int]:
        order_value = self._section_order_value(sv, fallback=int(sv.get("default_index", 0)))
        default_index = int(sv.get("default_index", 0))
        return order_value, default_index

    def _ordered_section_vars(self) -> List[Dict[str, Any]]:
        sections = list(getattr(self, "sections_vars", []))
        if not sections:
            return sections
        ordered = sorted(sections, key=self._section_order_key)
        for idx, sv in enumerate(ordered):
            order_var = sv.get("order_var")
            if order_var is not None:
                try:
                    order_var.set(idx)
                except Exception:
                    pass
        self.sections_vars = ordered
        template_order: List[Dict[str, Any]] = []
        for sv in ordered:
            seed_ref = sv.get("seed_ref")
            if seed_ref is not None:
                template_order.append(seed_ref)
        if len(template_order) == len(self.section_templates):
            self.section_templates = template_order
        return ordered

    def _sync_section_tab_order(self) -> None:
        notebook = getattr(self, "sections_notebook", None)
        if notebook is None:
            return
        ordered = self._ordered_section_vars()
        for idx, sv in enumerate(ordered):
            tab = sv.get("tab")
            if tab is not None:
                notebook.insert(idx, tab)

    def _move_section_order(self, sv: Dict[str, Any], delta: int) -> None:
        ordered = self._ordered_section_vars()
        try:
            index = ordered.index(sv)
        except ValueError:
            return
        target = max(0, min(len(ordered) - 1, index + delta))
        if target == index:
            return
        current_var = sv.get("order_var")
        other_var = ordered[target].get("order_var")
        if current_var is None or other_var is None:
            return
        try:
            current_value = int(current_var.get())
        except Exception:
            current_value = index
        try:
            other_value = int(other_var.get())
        except Exception:
            other_value = target
        current_var.set(other_value)
        other_var.set(current_value)
        self._sync_section_tab_order()
        self._mark_dirty()

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
        for idx, sv in enumerate(self._ordered_section_vars()):
            enabled = bool(sv["enabled_var"].get())
            if not enabled:
                continue
            default_name = sv.get("default_name", "Section")
            raw_name = str(sv["name_var"].get()).strip()
            name = raw_name or default_name
            tmin = int(sv["typ_min"].get()); tmax = int(sv["typ_max"].get())
            rmin = int(sv["resp_min"].get()); rmax = int(sv["resp_max"].get())
            mode_var = sv.get("mode_var")
            mode_raw = mode_var.get() if mode_var is not None else "popular"
            mode_norm = normalize_search_mode(mode_raw)
            if mode_norm in LATEST_SEARCH_MODES:
                mode_value = "latest"
            elif mode_norm in POPULAR_SEARCH_MODES:
                mode_value = "popular"
            else:
                mode_value = "popular"
            q_lines = [ln.strip() for ln in sv["txt_queries"].get("1.0", "end").splitlines() if ln.strip()]
            r_lines = [ln.strip() for ln in sv["txt_responses"].get("1.0", "end").splitlines() if ln.strip()]
            out.append(Section(
                name=name,
                typing_ms_per_char=(tmin, tmax),
                max_responses_before_switch=(rmin, rmax),
                search_queries=q_lines,
                responses=r_lines,
                search_mode=mode_value,
                enabled=enabled,
                order=idx,
            ))
        return out

    def _config_to_dict(self) -> Dict:
        ordered = self._ordered_section_vars()
        return {
            "config": self._collect_config(),
            "sections": [self._section_to_dict(idx, sv) for idx, sv in enumerate(ordered)],
        }

    def _section_to_dict(self, idx: int, sv: Dict) -> Dict:
        default_name = sv.get("default_name", "Section")
        name = str(sv["name_var"].get()).strip() or default_name
        default_mode_ui = sv.get("default_mode", "Popular")
        fallback_norm = normalize_search_mode(default_mode_ui)
        fallback_mode = "latest" if fallback_norm in LATEST_SEARCH_MODES else "popular"
        mode_var = sv.get("mode_var")
        mode_raw = mode_var.get() if mode_var is not None else default_mode_ui
        mode_norm = normalize_search_mode(mode_raw)
        if mode_norm in LATEST_SEARCH_MODES:
            mode_value = "latest"
        elif mode_norm in POPULAR_SEARCH_MODES:
            mode_value = "popular"
        else:
            mode_value = fallback_mode
        return {
            "name": name,
            "enabled": bool(sv["enabled_var"].get()),
            "search_mode": mode_value,
            "typing_ms_per_char": (int(sv["typ_min"].get()), int(sv["typ_max"].get())),
            "max_responses_before_switch": (int(sv["resp_min"].get()), int(sv["resp_max"].get())),
            "search_queries": [
                ln.strip()
                for ln in sv["txt_queries"].get("1.0", "end").splitlines()
                if ln.strip()
            ],
            "responses": [
                ln.strip()
                for ln in sv["txt_responses"].get("1.0", "end").splitlines()
                if ln.strip()
            ],
            "order": self._section_order_value(sv, fallback=idx),
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
        sections_present = "sections" in data
        raw_sections = data.get("sections", [])
        ordered_cfg: List[Dict[str, Any]] = []
        if isinstance(raw_sections, list):
            temp: List[Tuple[int, int, Dict[str, Any]]] = []
            for idx, section_cfg in enumerate(raw_sections):
                if not isinstance(section_cfg, dict):
                    continue
                order_val = section_cfg.get("order")
                try:
                    order_key = int(order_val)
                except Exception:
                    order_key = idx
                temp.append((order_key, idx, section_cfg))
            temp.sort(key=lambda item: (item[0], item[1]))
            ordered_cfg = [item[2] for item in temp]

        if sections_present:
            self._ensure_section_tab_count(len(ordered_cfg))
        elif not self.sections_vars:
            if not self.section_templates:
                self.section_templates = copy.deepcopy(DEFAULT_SECTIONS_SEED)
            self._ensure_section_tab_count(len(self.section_templates))

        ordered_vars = self._ordered_section_vars()
        if ordered_cfg:
            while len(ordered_cfg) < len(ordered_vars):
                ordered_cfg.append({})
        else:
            ordered_cfg = [{} for _ in ordered_vars]

        for idx, sv in enumerate(ordered_vars):
            section_cfg = ordered_cfg[idx] if idx < len(ordered_cfg) else {}
            if not isinstance(section_cfg, dict):
                section_cfg = {}

            default_name = sv.get("default_name", "Section")
            default_enabled = bool(sv.get("default_enabled", True))

            order_val = section_cfg.get("order")
            if order_val is None:
                order_val = idx
            try:
                sv["order_var"].set(int(order_val))
            except Exception:
                sv["order_var"].set(idx)

            name_value = str(section_cfg.get("name") or "").strip()
            sv["name_var"].set(name_value or default_name)

            enabled_value = section_cfg.get("enabled")
            if enabled_value is None:
                enabled_value = default_enabled
            sv["enabled_var"].set(bool(enabled_value))

            default_mode_ui = sv.get("default_mode", "Popular")
            mode_value = section_cfg.get("search_mode", None)
            if mode_value is None:
                ui_mode = default_mode_ui
            else:
                mode_norm = normalize_search_mode(mode_value)
                if mode_norm in LATEST_SEARCH_MODES:
                    ui_mode = "Latest"
                elif mode_norm in POPULAR_SEARCH_MODES:
                    ui_mode = "Popular"
                else:
                    ui_mode = default_mode_ui
            mode_var = sv.get("mode_var")
            if mode_var is not None:
                mode_var.set(ui_mode)

            tpair = section_cfg.get("typing_ms_per_char")
            if isinstance(tpair, (list, tuple)) and len(tpair) == 2:
                sv["typ_min"].set(int(tpair[0])); sv["typ_max"].set(int(tpair[1]))

            rpair = section_cfg.get("max_responses_before_switch")
            if isinstance(rpair, (list, tuple)) and len(rpair) == 2:
                sv["resp_min"].set(int(rpair[0])); sv["resp_max"].set(int(rpair[1]))

            queries_value = section_cfg.get("search_queries", None)
            if isinstance(queries_value, str):
                q_lines = [ln.strip() for ln in queries_value.splitlines() if ln.strip()]
            elif isinstance(queries_value, (list, tuple)):
                q_lines = [str(ln).strip() for ln in queries_value if str(ln).strip()]
            else:
                q_lines = None
            if q_lines is not None:
                sv["txt_queries"].delete("1.0", "end")
                if q_lines:
                    sv["txt_queries"].insert("1.0", "\n".join(q_lines))
                sv["txt_queries"].edit_modified(False)

            responses_value = section_cfg.get("responses", None)
            if isinstance(responses_value, str):
                r_lines = [ln.strip() for ln in responses_value.splitlines() if ln.strip()]
            elif isinstance(responses_value, (list, tuple)):
                r_lines = [str(ln).strip() for ln in responses_value if str(ln).strip()]
            else:
                r_lines = None
            if r_lines is not None:
                sv["txt_responses"].delete("1.0", "end")
                if r_lines:
                    sv["txt_responses"].insert("1.0", "\n".join(r_lines))
                sv["txt_responses"].edit_modified(False)

        self._sync_section_tab_order()
        ordered_after = self._ordered_section_vars()
        new_templates: List[Dict[str, Any]] = []
        for idx, sv in enumerate(ordered_after):
            template_dict = copy.deepcopy(self._section_to_dict(idx, sv))
            new_templates.append(template_dict)
            sv["seed_ref"] = template_dict
        self.section_templates = new_templates
        self._update_section_controls()

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
