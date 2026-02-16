#!/usr/bin/env python3
"""Simple link checker for built docs HTML.

Checks external http(s) links found in built HTML files and summarizes failures.
Caches failed URLs for quick recheck via --fails-only.
"""

import argparse
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
    map_files_to_urls,
)
from .config import load_config
from .checker import RateLimiter, check_url
from .crawler import find_links
from .cache import load_failed, write_failures
from .reporter import summarize, _console, print_header

if HAS_RICH:
    from rich.table import Table

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
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--per-host-delay", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--max-urls", type=int, default=0)
    parser.add_argument("--max-seconds", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--progress-seconds", type=int, default=10)
    parser.add_argument("--conf", default="conf.py")
    return parser.parse_args()

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

    def run(self, urls: List[str], url_map: Dict[str, List[Tuple[str, int]]], file_to_urls: Dict[str, List[str]], url_to_files: Dict[str, List[str]]):
        """Execute the link check for the provided URLs.

        Processes URLs in parallel and prints a hierarchical tree of results.
        Uses as_completed() to render files as soon as all their URLs finish,
        providing responsive UI feedback even when some URLs time out.

        Args:
            urls (List[str]): List of unique URLs to check.
            url_map (Dict[str, List[Tuple[str, int]]]): Map of URL to (file, line) locations.
            file_to_urls (Dict[str, List[str]]): Map of file path to list of URLs in that file.
            url_to_files (Dict[str, List[str]]): Map of URL to list of file paths (reverse mapping).

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
                f = executor.submit(check_url, url, self.args.timeout, rate_limiter, self.args.max_retries)
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

                finished_urls: Set[str] = set()
                checked_globally: Set[str] = set()
                file_results: Dict[str, Dict[str, Tuple[bool, str, float]]] = {
                    f: {} for f in file_to_urls.keys()
                }
                rendered_files: Set[str] = set()
                last_dir_parts: List[str] = []

                # Map each future to its URL for lookup after completion
                future_to_url = {f: u for u, f in url_to_future.items()}

                # Process results in completion order, not file order.
                # No timeout on as_completed() itself—individual f.result() calls handle timing.
                for completed_future in as_completed(url_to_future.values()):
                    url = future_to_url[completed_future]

                    # Get result from the completed future with per-worker timeout
                    try:
                        ok, message, elapsed = completed_future.result(timeout=worker_timeout)
                    except TimeoutError:
                        ok, message, elapsed = False, "Timeout waiting for worker", 0.0

                    if url not in finished_urls:
                        finished_urls.add(url)
                        self.results[url] = (ok, message)
                        if not ok:
                            self.failures[url] = message
                            # Report broken link immediately (inline) with source locations
                            source_files = url_to_files.get(url, [])
                            self.report_broken_link(url, source_files, message)

                    # Record result for the file(s) this URL belongs to
                    for file_path in url_to_files.get(url, []):
                        if file_path in file_results:
                            file_results[file_path][url] = (ok, message, elapsed)

                    # Check if any complete files are ready to render
                    for file_path in sorted(file_to_urls.keys()):
                        if file_path in rendered_files:
                            continue

                        f_urls = sorted(file_to_urls[file_path])
                        urls_in_file = [u for u in f_urls if u in url_to_future]

                        # Check if all URLs in this file have completed
                        if not all(u in file_results[file_path] for u in urls_in_file):
                            continue

                        # All URLs for this file are done—render it now
                        rendered_files.add(file_path)

                        parts = file_path.split('/')
                        dir_parts = parts[:-1]
                        file_name = parts[-1]

                        # Print directory headers if needed
                        common_depth = 0
                        for i in range(min(len(dir_parts), len(last_dir_parts))):
                            if dir_parts[i] == last_dir_parts[i]:
                                common_depth += 1
                            else:
                                break

                        for i in range(common_depth, len(dir_parts)):
                            indent = "  " * i
                            _console.print(f"{indent}[dir]{dir_parts[i]}/[/]")

                        last_dir_parts = dir_parts
                        file_indent = "  " * len(dir_parts)

                        # Print file header and all its results
                        _console.print(f"{file_indent}[file]{file_name}[/]")

                        f_total = len(urls_in_file)
                        f_ok = 0

                        for i, u in enumerate(urls_in_file):
                            ok, message, elapsed = file_results[file_path][u]
                            if ok:
                                f_ok += 1

                            is_skipped = (u in checked_globally)
                            checked_globally.add(u)

                            if ok:
                                color = "info" if is_skipped else "success"
                                icon = "✓"
                                lbl = "skipped" if is_skipped else "OK"
                            else:
                                color = "warning" if any(kw in message.lower() for kw in self.infra_keywords) else "error"
                                icon = "⚠" if color == "warning" else "✗"
                                lbl = message

                            link_indent = "  " * (len(dir_parts) + 1)
                            branch = "├──" if i < f_total - 1 else "└──"

                            if HAS_RICH:
                                t = Table(show_header=False, box=None, padding=0, expand=True)
                                t.add_column("link", ratio=5)
                                t.add_column("status", ratio=2, justify="right")
                                t.add_row(f"{link_indent}{branch} [{color}]{icon}[/] [link]{u}[/]", f"[{color}]{lbl}[/]")
                                _console.print(t)

                        stat_color = "success" if f_ok == f_total else "error"
                        icon_file = "✓" if f_ok == f_total else "✗"
                        _console.print(f"{file_indent}  [{stat_color}][{f_ok}/{f_total} {icon_file}][/]\n")

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

        summarize(self.failures, url_map, self.args.build_dir, global_start_time, len(file_to_urls))
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
            url_map = find_links(args.build_dir, exclude_patterns)
            urls = list(url_map.keys())
        else:
            url_map = {url: [] for url in urls}
    else:
        url_map = find_links(args.build_dir, exclude_patterns)

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

    url_to_files = map_urls_to_files(url_map, args.build_dir)
    file_to_urls = map_files_to_urls(url_to_files)

    print_header(len(urls))

    # Sort URLs by their first associated file path to improve tree grouping
    urls.sort(key=lambda u: url_to_files.get(u, [""])[0])

    runner = LinkCheckerRunner(args)
    aborted = runner.run(urls, url_map, file_to_urls, url_to_files)

    if runner.failures:
        write_failures(cache_dir, runner.failures, url_map)
        return 2 if aborted else 1

    if cache_dir.exists():
        import shutil
        shutil.rmtree(cache_dir, ignore_errors=True)

    return 2 if aborted else 0

if __name__ == "__main__":
    sys.exit(main())
