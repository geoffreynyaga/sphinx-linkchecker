"""Response caching for link checker.

Caches HTTP response status and metadata to avoid redundant checks.
Persists cache to JSON for reuse across runs.
"""

import json
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple


class ResponseCache:
    """In-memory + persistent cache for HTTP response metadata.

    Cache entries are valid for up to `max_age_seconds` (default 1 hour).
    Permanent failures (404, 410) are also cached and reused.
    Transient failures (429, 5xx) are NOT cached and will be re-checked.

    Attributes:
        cache_file: Path to JSON cache file.
        max_age_seconds: How long to keep cached entries (default: 3600 = 1 hour).
    """

    def __init__(self, cache_file: Optional[Path] = None, max_age_seconds: int = 3600):
        """Initialize the response cache.

        Args:
            cache_file: Path to persist cache as JSON. If None, only in-memory caching.
            max_age_seconds: Maximum age of cache entries before invalidation.
        """
        self.cache_file = cache_file
        self.max_age_seconds = max_age_seconds
        self._cache: Dict[str, Dict] = {}
        self._lock = threading.Lock()

        if cache_file and cache_file.exists():
            self._load_from_file()

    def _load_from_file(self) -> None:
        """Load cache from JSON file."""
        try:
            if self.cache_file and self.cache_file.exists():
                with open(self.cache_file) as f:
                    data = json.load(f)
                    self._cache = data.get("cache", {})
        except Exception:
            # If cache is corrupted, just start fresh
            self._cache = {}

    def _save_to_file(self) -> None:
        """Persist cache to JSON file."""
        if not self.cache_file:
            return
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump({"cache": self._cache}, f, indent=2)
        except Exception:
            # Non-fatal; if we can't persist, at least keep in-memory cache
            pass

    def get(self, url: str) -> Optional[Tuple[int, float]]:
        """Retrieve cached response status for URL, if fresh.

        Args:
            url: The URL to look up.

        Returns:
            (status_code, cached_age_seconds) if cached and fresh, else None.
        """
        with self._lock:
            entry = self._cache.get(url)
            if not entry:
                return None

            cached_time = entry.get("timestamp", 0)
            age = time.time() - cached_time
            status = entry.get("status")

            # Check if cache is still fresh
            if age > self.max_age_seconds:
                return None

            # Permanent failures (4xx except 429) are always valid
            # Transient failures (429, 5xx) are NOT cached
            if status and status != 429 and status < 500:
                return (status, age)

            # For transient failures (429, 5xx), always re-check
            if status and (status == 429 or status >= 500):
                return None

            return (status, age) if status else None

    def set(self, url: str, status_code: int) -> None:
        """Cache a successful response.

        Args:
            url: The URL that was checked.
            status_code: The HTTP status code returned.
        """
        with self._lock:
            self._cache[url] = {
                "status": status_code,
                "timestamp": time.time(),
            }

    def set_failure(self, url: str, status_code: int) -> None:
        """Cache a failed response (4xx, 5xx).

        Note: Transient failures (429, 5xx) are cached but will be
        re-checked since `get()` returns None for them.

        Args:
            url: The URL that was checked.
            status_code: The HTTP status code (4xx or 5xx).
        """
        with self._lock:
            self._cache[url] = {
                "status": status_code,
                "timestamp": time.time(),
            }

    def persist(self) -> None:
        """Flush in-memory cache to disk."""
        self._save_to_file()

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()
            self._save_to_file()
