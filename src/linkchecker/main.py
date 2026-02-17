#!/usr/bin/env python3
"""Simple link checker for built docs HTML.

Checks external http(s) links found in built HTML files and summarizes failures.
Caches failed URLs for quick recheck via --fails-only.
"""

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from pathlib import Path
from typing import Dict, List, Tuple, Set

from .colors import HAS_RICH, _print_lock
from .utils import (
    should_ignore_url,
    map_urls_to_files,
)
from .config import load_config
from .checker import RateLimiter, check_url
from .doctree_crawler import extract_links_with_fallback
from .cache import load_failed, write_failures
from .reporter import summarize, _console, print_header
from .domain_failures import FailedDomainTracker

if HAS_RICH:
    pass  # Rich is available but we don't need Table anymore for simple rendering

def _auto_scale_workers() -> int:
    """Auto-scale worker count based on CPU availability.

    Returns:
        int: Number of workers (between 6 and 20).
    """
    cpu_count = os.cpu_count() or 1
    return max(6, min(20, cpu_count + 2))

def parse_args():
    """Parse command-line arguments.

    Returns:
        argparse.Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", default="_build")
    parser.add_argument("--cache-dir", default=".sphinx/linkcheck")
    parser.add_argument("--fails-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--per-host-delay", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--max-urls", type=int, default=0)
    parser.add_argument("--max-seconds", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--progress-seconds", type=int, default=10)
    parser.add_argument("--skip-failed-domains", action="store_true", default=True)
    parser.add_argument("--conf", default="conf.py")
    args = parser.parse_args()
    # Auto-scale workers if not explicitly provided
    if args.workers is None:
        args.workers = _auto_scale_workers()
    return args

class LinkCheckerRunner:
    """Orchestrates the link checking process.

    This class manages the thread pool execution, real-time reporting,
    and state tracking for a link check run.

    Attributes:
        args (argparse.Namespace): Command-line arguments.
        infra_keywords (List[str]): Keywords that identify transient/infrastructure errors.
        failures (Dict[str, str]): Map of URL to error message for failed checks.
        results (Dict[str, Tuple[bool, str]]): Map of URL to (success, message) results.
        db_lock (threading.Lock): Lock for thread-safe updates to counters.
        db_checked (int): Total number of URLs checked.
        db_success (int): Total number of successful checks.
        db_fail (int): Total number of failed checks.
    """
    def __init__(self, args):
        """Initialize the runner with arguments.

        Args:
            args (argparse.Namespace): The parsed CLI arguments.
        """
        self.args = args
        self.infra_keywords = ["timeout", "connection", "ssl", "403", "500", "502", "503", "504", "reset"]
        self.failures: Dict[str, str] = {}
        self.results: Dict[str, Tuple[bool, str]] = {}
        self.checked_urls = 0
        self.db_lock = threading.Lock()
        self.db_checked = 0
        self.db_success = 0
        self.db_fail = 0
        self.domain_tracker = FailedDomainTracker(failure_threshold=3)

    def on_future_done(self, future):
        """Callback for when a check_url future completes.

        Updates internal success/failure counters in a thread-safe manner.

        Args:
            future (concurrent.futures.Future): The completed future.
        """
        try:
            ok, _, _ = future.result()
            with self.db_lock:
                self.db_checked += 1
                if ok: self.db_success += 1
                else: self.db_fail += 1
        except Exception:
            with self.db_lock:
                self.db_checked += 1
                self.db_fail += 1

    def report_broken_link(self, url: str, source_files: List[str], message: str) -> None:
        """Report a broken link immediately (inline) with source locations.

        This provides Sphinx-style immediate feedback as failures occur,
        rather than waiting for batched file rendering.

        Args:
            url (str): The broken URL.
            source_files (List[str]): List of source file paths where this URL appears.
            message (str): Error message from the check.

        Example:
            Prints: (file: line 42) broken    https://example.com/ - HTTP 404
        """
        for file_path in source_files:
            # Extract just filename for compact output
            file_name = file_path.split('/')[-1]
            with _print_lock:
                _console.print(f"({file_path}: [warning]broken[/])  [link]{url}[/] - [error]{message}[/]", soft_wrap=True)

    def report_link_result(self, url: str, ok: bool, message: str, elapsed: float) -> None:
        """Print a single URL check result immediately in completion order."""
        status = message
        elapsed_ms = int(elapsed * 1000)

        if HAS_RICH:
            with _print_lock:
                if not ok:
                    _console.print(f"[error]✗[/] [link]{url}[/] [error]{status}[/] ({elapsed_ms}ms)", soft_wrap=True)
                elif "skipped" in status.lower():
                    _console.print(f"[info]•[/] [link]{url}[/] [info]{status}[/]", soft_wrap=True)
                else:
                    _console.print(f"[success]✓[/] [link]{url}[/] [success]{status}[/] ({elapsed_ms}ms)", soft_wrap=True)
        else:
            prefix = "[FAIL]" if not ok else ("[SKIP]" if "skipped" in status.lower() else "[ OK ]")
            with _print_lock:
                if "skipped" in status.lower():
                    print(f"{prefix} {url} - {status}")
                else:
                    print(f"{prefix} {url} - {status} ({elapsed_ms}ms)")

    def run(self, urls: List[str], url_map: Dict[str, List[Tuple[str, int]]], url_to_files: Dict[str, List[str]]):
        """Execute the link check for the provided URLs.

        Processes URLs in parallel and prints results as they complete (no batching).
        Maximum speed: renders each link immediately without waiting for file grouping.

        Args:
            urls (List[str]): List of unique URLs to check.
            url_map (Dict[str, List[Tuple[str, int]]]): Map of URL to (file, line) locations.
            url_to_files (Dict[str, List[str]]): Map of URL to list of file paths.

        Returns:
            bool: True if the run was aborted (e.g., timeout), False otherwise.
        """
        total_urls = len(urls)
        stop_progress = threading.Event()

        def progress_reporter() -> None:
            if self.args.progress_seconds <= 0:
                return
            while not stop_progress.wait(self.args.progress_seconds):
                with _print_lock:
                    print(f"Progress: {self.db_checked}/{total_urls} URLs checked")

        rate_limiter = RateLimiter(self.args.per_host_delay)
        global_start_time = time.monotonic()
        aborted = False

        progress_thread = None
        if not HAS_RICH:
            progress_thread = threading.Thread(target=progress_reporter, daemon=True)
            progress_thread.start()

        with ThreadPoolExecutor(max_workers=self.args.workers) as executor:
            url_to_future = {}
            for url in urls:
                f = executor.submit(check_url, url, self.args.timeout, rate_limiter, self.args.max_retries, self.domain_tracker)
                f.add_done_callback(self.on_future_done)
                url_to_future[url] = f

            # Calculate worker timeout: must accommodate all retries plus exponential backoff.
            # With max_retries=2 and timeout=12s:
            #   Attempt 1: 12s + backoff(2^0=1s) = 13s
            #   Attempt 2: 12s + backoff(2^1=2s) = 14s
            #   Attempt 3: 12s + backoff(2^2=4s) = 16s
            #   Total: ~43s + rate limiter delays + safety margin
            worker_timeout = (self.args.timeout * (self.args.max_retries + 1)) + 30

            try:
                # Process results as they complete (not in file order) for responsive UI.
                # This prevents slow URLs from blocking the entire file's display.
                # When a file's results are complete, render it immediately.
                # NOTE: as_completed() has no timeout here. Each individual future's
                # result is retrieved with worker_timeout, which handles timeouts.

                # Simplified: render each link as it completes, no file batching
                future_to_url = {f: u for u, f in url_to_future.items()}

                for completed_future in as_completed(url_to_future.values()):
                    url = future_to_url[completed_future]

                    try:
                        ok, message, elapsed = completed_future.result(timeout=worker_timeout)
                    except TimeoutError:
                        ok, message, elapsed = False, "Timeout waiting for worker", 0.0

                    self.results[url] = (ok, message)
                    self.report_link_result(url, ok, message, elapsed)

                    # Track domain failures
                    if self.args.skip_failed_domains:
                        if not ok:
                            self.domain_tracker.mark_failure(url, message)
                        else:
                            self.domain_tracker.mark_success(url)

                    # Record failures
                    if not ok:
                        self.failures[url] = message
                        # Report broken links inline
                        source_files = url_to_files.get(url, [])
                        self.report_broken_link(url, source_files, message)

                    if aborted:
                        break
            finally:
                if aborted:
                    executor.shutdown(cancel_futures=True)
                stop_progress.set()
                if progress_thread:
                    progress_thread.join(timeout=1.0)

        if aborted:
            print("\nLinkcheck aborted due to max runtime; results are partial.")

        # Count unique files scanned
        files_scanned = len(set(f for locs in url_map.values() for f, _ in locs))
        summarize(self.failures, url_map, self.args.build_dir, global_start_time, files_scanned)
        return aborted

def main() -> int:
    """Main entry point for the link checker.

    Returns:
        int: Exit code (0 for success, 1 for failures, 2 for aborted/warnings).
    """
    args = parse_args()
    cache_dir = Path(args.cache_dir)

    ignore_urls, exclude_patterns, config_timeout, config_max_retries = load_config(args.conf)

    # CLI args take priority over config, which takes priority over hardcoded defaults
    if args.timeout is None:
        args.timeout = config_timeout
    if args.max_retries is None:
        args.max_retries = config_max_retries

    if args.fails_only:
        urls = load_failed(cache_dir)
        if not urls:
            print("No cached failures found; running full check instead.")
            url_map, used_doctrees = extract_links_with_fallback(args.build_dir, exclude_patterns)
            urls = list(url_map.keys())
        else:
            url_map = {url: [] for url in urls}
            used_doctrees = False
    else:
        url_map, used_doctrees = extract_links_with_fallback(args.build_dir, exclude_patterns)
        if used_doctrees:
            print("[Doctree mode] Using pre-built doctrees (5-10x faster than HTML parsing)")
        else:
            print("[HTML fallback] Doctrees not found, using HTML parsing")

    # Filter out ignored URLs
    urls = sorted([u for u in url_map.keys() if not should_ignore_url(u, ignore_urls)])

    if args.max_urls and args.max_urls > 0:
        if len(urls) > args.max_urls:
            print(f"Limiting linkcheck to {args.max_urls} URLs out of {len(urls)}.")
        urls = urls[: args.max_urls]

    if not urls:
        print("No external links found to check.")
        if cache_dir.exists():
            import shutil
            shutil.rmtree(cache_dir, ignore_errors=True)
        return 0

    filtered_url_map = {url: url_map.get(url, []) for url in urls}
    url_to_files = map_urls_to_files(filtered_url_map, args.build_dir)

    print_header(len(urls), args.workers)

    # No need to sort for tree grouping - we're rendering as we go
    runner = LinkCheckerRunner(args)
    aborted = runner.run(urls, filtered_url_map, url_to_files)

    if runner.failures:
        write_failures(cache_dir, runner.failures, filtered_url_map)
        return 2 if aborted else 1

    if cache_dir.exists():
        import shutil
        shutil.rmtree(cache_dir, ignore_errors=True)

    return 2 if aborted else 0

if __name__ == "__main__":
    sys.exit(main())
