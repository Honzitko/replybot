"""Simple post scheduler with RSS import stub.

This module defines :func:`generate_post_from_rss` which provides an
extension point for integrating an external application or API that turns
an RSS feed item into text (and optionally media) for a social media post.
It also exposes a small :class:`PostWindow` Tkinter GUI demonstrating how
this function may be used to populate an editor from an RSS entry.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

try:  # Optional dependency used for the API stub
    import requests  # pragma: no cover - requests may be unavailable
except Exception:  # pragma: no cover - keep the module importable
    requests = None


def generate_post_from_rss(
    rss_item: Dict[str, Any],
    settings: Dict[str, Any],
) -> Tuple[str, Optional[str]]:
    """Return generated post text and optional media path for ``rss_item``.

    Parameters
    ----------
    rss_item:
        Mapping describing the RSS entry (e.g. ``title``, ``link``).
    settings:
        Application settings.  When ``rss_api_endpoint`` is provided a POST
        request will be made to that URL with ``rss_item`` as JSON.

    Returns
    -------
    tuple
        ``(text, media_path)`` where ``media_path`` is ``None`` when no media
        was produced or the API call failed.
    """

    endpoint = settings.get("rss_api_endpoint", "http://localhost:8000/generate")
    payload = {"rss_item": rss_item, "settings": settings}

    if requests is None:
        logging.warning("requests is not available; returning placeholder text")
        return rss_item.get("title", ""), None

    try:
        resp = requests.post(endpoint, json=payload, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get("text", ""), data.get("media_path")
    except Exception as exc:  # pragma: no cover - depends on external API
        logging.error("generate_post_from_rss failed: %s", exc)
        return rss_item.get("title", ""), None


# --- Example Tkinter posting window -------------------------------------------------
try:  # pragma: no cover - Tkinter may be unavailable in some environments
    import tkinter as tk
    from tkinter import ttk, simpledialog, messagebox
except Exception:  # pragma: no cover
    tk = None


class PostWindow(tk.Toplevel if tk else object):
    """Simple post editor window with an "Import from RSS" option.

    The window contains a text widget for the post content and a button that
    prompts the user for an RSS item and populates the editor by calling
    :func:`generate_post_from_rss`.
    """

    def __init__(self, master: Optional[tk.Misc] = None, settings: Optional[Dict[str, Any]] = None):
        if tk is None:  # pragma: no cover - safety when Tk is missing
            raise RuntimeError("Tkinter is required for PostWindow")
        super().__init__(master)
        self.settings = settings or {}
        self.title("Schedule Post")
        self.geometry("500x300")

        self.editor = tk.Text(self, wrap="word")
        self.editor.pack(fill="both", expand=True)

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Import from RSS", command=self.import_from_rss).pack(side="left")

    # ------------------------------------------------------------------
    def import_from_rss(self) -> None:
        """Ask for an RSS item and populate the editor via the generator."""

        # In a full implementation we would fetch a feed and present a proper
        # selection dialog.  This stub simply asks for a URL or title which is
        # wrapped into a minimal ``rss_item`` mapping.
        if tk is None:  # pragma: no cover - safety when Tk is missing
            return
        entry = simpledialog.askstring("Import from RSS", "Enter RSS item URL or text:")
        if not entry:
            return

        rss_item = {"title": entry, "link": entry}
        text, media = generate_post_from_rss(rss_item, self.settings)

        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", text)

        if media:
            messagebox.showinfo("RSS Import", f"Media downloaded to: {media}")


if __name__ == "__main__":  # pragma: no cover - manual demonstration helper
    if tk is None:
        raise SystemExit("Tkinter not available")
    root = tk.Tk()
    root.withdraw()  # Hide root window
    win = PostWindow(root, settings={})
    win.mainloop()
