import json
import os
from typing import List


class PostLibrary:
    """Manage stored posts persisted to a JSON file."""

    def __init__(self, path: str | None = None) -> None:
        base_dir = os.path.abspath(os.path.dirname(__file__))
        if path is None:
            base_dir = os.path.join(base_dir, "configs")
            os.makedirs(base_dir, exist_ok=True)
            path = os.path.join(base_dir, "posts.json")
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.posts: List[str] = []
        self.load()

    def load(self) -> List[str]:
        """Load posts from ``self.path``."""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self.posts = json.load(fh)
        except FileNotFoundError:
            self.posts = []
            self.save()
        except Exception:
            self.posts = []
        return self.posts

    def save(self) -> None:
        """Persist current posts to ``self.path``."""
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.posts, fh, ensure_ascii=False, indent=2)

    def get_posts(self) -> List[str]:
        return list(self.posts)

    def add_post(self, text: str) -> None:
        self.posts.append(text)
        self.save()

    def update_post(self, index: int, text: str) -> None:
        if 0 <= index < len(self.posts):
            self.posts[index] = text
            self.save()

    def delete_post(self, index: int) -> None:
        if 0 <= index < len(self.posts):
            del self.posts[index]
            self.save()
