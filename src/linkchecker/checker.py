import threading
import time
from typing import Dict, Tuple, Optional
from urllib.parse import urlparse
import requests
from .colors import color_blue, _print_lock
from .domain_failures import FailedDomainTracker
from .response_cache import ResponseCache

_session_local = threading.local()

def get_session() -> requests.Session:
    """Get or create a thread-local requests session.

    Returns:
        requests.Session: A session object with a custom User-Agent.
    """
    session = getattr(_session_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "sphinx-linkchecker"})
        _session_local.session = session
    return session

class RateLimiter:
    """Manages per-host rate limiting with per-host locking for better parallelism.

    Uses per-host locks to avoid blocking unrelated hosts when rate-limiting
    a busy host. This improves throughput when checking URLs across many domains.

    Attributes:
        per_host_delay (float): Seconds to wait between requests to the same host.
    """
    def __init__(self, per_host_delay: float):
        """Initialize the rate limiter.

        Args:
            per_host_delay (float): Delay in seconds.
        """
        self.per_host_delay = per_host_delay
        self._global_lock = threading.Lock()  # Only for accessing _host_locks and _next_time dicts
        self._host_locks: Dict[str, threading.Lock] = {}
        self._next_time: Dict[str, float] = {}

    def wait(self, host: str) -> None:
        """Wait if necessary before making a request to the given host.

        Uses per-host locking to avoid blocking other hosts.

        Args:
            host (str): The hostname (netloc) to limit.
        """
        if self.per_host_delay <= 0:
            return

        # Get or create a lock for this specific host
        with self._global_lock:
            if host not in self._host_locks:
                self._host_locks[host] = threading.Lock()
            host_lock = self._host_locks[host]

        # Now acquire the per-host lock (doesn't block other hosts)
        with host_lock:
            now = time.monotonic()
            next_allowed = max(self._next_time.get(host, 0.0), now)
            self._next_time[host] = next_allowed + self.per_host_delay
            sleep_for = max(0.0, next_allowed - now)

        # Sleep outside the lock
        if sleep_for > 0:
            time.sleep(sleep_for)

def _is_retryable_status(status_code: int) -> bool:
    """Check if an HTTP status code is worth retrying.

    Args:
        status_code (int): HTTP response code.

    Returns:
        bool: True if status is 429 or 5xx.
    """
    return status_code in (429, 500, 502, 503, 504)

def _retry_after_seconds(response: requests.Response, attempt: int) -> float:
    """Determine how long to wait before retrying based on headers or exponential backoff.

    Args:
        response (requests.Response): The failed response.
        attempt (int): The current retry attempt number.

    Returns:
        float: Seconds to wait.

    Example:
        >>> import requests
        >>> from unittest.mock import Mock
        >>> # Server sends Retry-After header
        >>> response = Mock(spec=requests.Response)
        >>> response.headers = {"Retry-After": "5"}
        >>> _retry_after_seconds(response, attempt=0)
        5.0
        >>> # No header, use exponential backoff
        >>> response.headers = {}
        >>> _retry_after_seconds(response, attempt=0)  # 2^0 = 1
        1.0
        >>> _retry_after_seconds(response, attempt=2)  # 2^2 = 4
        4.0
        >>> _retry_after_seconds(response, attempt=10)  # Capped at 30
        30.0
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        val = float(retry_after)
    else:
        val = 1.0 * (2 ** attempt)
    return min(val, 30.0)

def _is_connection_timeout(exc: requests.RequestException) -> bool:
    """Detect if exception is a connection timeout (not transient).

    Connection timeouts indicate the host is unreachable/offline.
    These should fail-fast (no retries), since retrying won't help.

    Args:
        exc: RequestException to inspect.

    Returns:
        bool: True if this is a connection timeout.
    """
    exc_str = str(exc).lower()
    return 'connect timeout' in exc_str or ('connection' in exc_str and 'refused' in exc_str)

def check_url(
    url: str,
    timeout: float,
    rate_limiter: RateLimiter,
    max_retries: int,
    domain_tracker: Optional[FailedDomainTracker] = None,
    response_cache: Optional[ResponseCache] = None,
) -> Tuple[bool, str, float]:
    """Check a single URL for validity.

    Attempts a HEAD request first, falling back to GET if HEAD fails with >= 400 or 405.
    Implements retries for HTTP 429/5xx errors but fails fast on connection timeouts.
    Connection timeouts indicate unreachable hosts and don't benefit from retries.

    If a response_cache is provided, checks cache first and may skip HTTP request entirely
    for fresh cached entries.

    Args:
        url (str): The absolute URL to check.
        timeout (float): Request timeout in seconds.
        rate_limiter (RateLimiter): The rate limiter instance.
        max_retries (int): Max retries for HTTP 429/5xx (not for connection timeouts).
        domain_tracker (Optional[FailedDomainTracker]): Domain failure tracker.
        response_cache (Optional[ResponseCache]): Response cache to skip redundant checks.

    Returns:
        Tuple[bool, str, float]: (success_bool, message, elapsed_time).

    Example:
        >>> from linkchecker.checker import RateLimiter, check_url
        >>> limiter = RateLimiter(per_host_delay=0.5)
        >>> success, message, elapsed = check_url(
        ...     "https://docs.python.org/3/",
        ...     timeout=10.0,
        ...     rate_limiter=limiter,
        ...     max_retries=2
        ... )
        >>> if success:
        ...     print(f"✓ Link OK: {message} ({elapsed:.2f}s)")
        ... else:
        ...     print(f"✗ Link failed: {message}")
        ✓ Link OK: HTTP 200 (0.45s)
    """
    host = urlparse(url).netloc

    # Skip if domain is already marked as failed
    if domain_tracker and domain_tracker.is_domain_failed(url):
        return True, "skipped (domain failed)", 0.0

    # Check response cache first
    if response_cache:
        cached = response_cache.get(url)
        if cached:
            status_code, age = cached
            if status_code < 400:
                return True, f"HTTP {status_code} (cached, {age:.0f}s old)", 0.0
            elif status_code == 404 or status_code == 410:
                # Permanent failures are valid cache hits
                return False, f"HTTP {status_code} (cached)", 0.0
            # Don't return for transient failures (429, 5xx) — always re-check

    session = get_session()
    start_time = time.monotonic()
    for attempt in range(max_retries + 1):
        try:
            rate_limiter.wait(host)
            response = session.head(url, allow_redirects=True, timeout=timeout)
            if response.status_code == 405 or response.status_code >= 400:
                response = session.get(url, allow_redirects=True, timeout=timeout)

            elapsed = time.monotonic() - start_time
            if response.status_code >= 400:
                if _is_retryable_status(response.status_code) and attempt < max_retries:
                    sleep_time = _retry_after_seconds(response, attempt)
                    if sleep_time > 2:
                        with _print_lock:
                            print(color_blue(f"  - {url} (rate limited, retrying in {sleep_time:.1f}s...)"))
                    time.sleep(sleep_time)
                    continue
                suffix = " (rate limited)" if response.status_code == 429 else ""
                if domain_tracker:
                    domain_tracker.mark_failure(url, f"HTTP {response.status_code}")
                # Cache permanent failures (404, 410, etc.) but NOT transient (429, 5xx)
                if response_cache and not _is_retryable_status(response.status_code):
                    response_cache.set_failure(url, response.status_code)
                return False, f"HTTP {response.status_code}{suffix}", elapsed
            # Success
            if domain_tracker:
                domain_tracker.mark_success(url)
            if response_cache:
                response_cache.set(url, response.status_code)
            return True, f"HTTP {response.status_code}", elapsed
        except requests.RequestException as exc:
            # Fail fast on connection timeouts—host is unreachable, retries won't help.
            if _is_connection_timeout(exc):
                elapsed = time.monotonic() - start_time
                if domain_tracker:
                    domain_tracker.mark_failure(url, "connection timeout")
                return False, str(exc).splitlines()[0], elapsed
            # Other transient errors (read timeout, DNS failure) may benefit from retries.
            if attempt < max_retries:
                sleep_time = min(1.0 * (2 ** attempt), 30.0)
                time.sleep(sleep_time)
                continue
            elapsed = time.monotonic() - start_time
            if domain_tracker:
                domain_tracker.mark_failure(url, str(exc))
            return False, str(exc).splitlines()[0], elapsed

    # Defensive fallback; loop always returns above.
    return False, "Unknown error", time.monotonic() - start_time
