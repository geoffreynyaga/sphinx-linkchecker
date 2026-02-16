import json
from pathlib import Path
from typing import Dict, List, Tuple

def load_failed(cache_dir: Path) -> List[str]:
    """Load previously failed URLs from cache.

    Args:
        cache_dir: Path to cache directory (e.g., `.sphinx/linkcheck`).

    Returns:
        List of URLs that failed in the previous run.

    Example:
        >>> from pathlib import Path
        >>> cache = Path(".sphinx/linkcheck")
        >>> failed_urls = load_failed(cache)
        >>> print(failed_urls)
        ['https://example.com/broken', 'https://old-site.org/404']
    """
    failures_path = cache_dir / "failures.json"
    if not failures_path.exists():
        return []
    try:
        data = json.loads(failures_path.read_text(encoding="utf-8"))
    except OSError:
        return []
    return [item.get("url") for item in data.get("failures", []) if item.get("url")]

def write_failures(cache_dir: Path, failures: Dict[str, str], url_map: Dict[str, List[Tuple[str, int]]]):
    """Save failed URLs to cache for future --fails-only runs.

    Args:
        cache_dir: Path to cache directory.
        failures: Map of URL to error message.
        url_map: Map of URL to list of (file, line) locations.

    Example:
        >>> from pathlib import Path
        >>> cache = Path(".sphinx/linkcheck")
        >>> failures = {
        ...     "https://example.com/broken": "HTTP 404",
        ...     "https://timeout.org": "Connection timeout"
        ... }
        >>> url_map = {
        ...     "https://example.com/broken": [("index.html", 42)]
        ... }
        >>> write_failures(cache, failures, url_map)
        # Creates .sphinx/linkcheck/failures.json with:
        # {
        #   "failures": [
        #     {
        #       "error": "HTTP 404",
        #       "locations": [["index.html", 42]],
        #       "url": "https://example.com/broken"
        #     },
        #     ...
        #   ]
        # }
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    failures_path = cache_dir / "failures.json"
    payload = {
        "failures": [
            {
                "url": url,
                "error": error,
                "locations": url_map.get(url, []),
            }
            for url, error in failures.items()
        ]
    }
    failures_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
