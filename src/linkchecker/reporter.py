import time
from typing import Dict, List, Tuple
from .colors import HAS_RICH, _print_lock
from .utils import normalize_html_path, find_source_info

if HAS_RICH:
    from rich.console import Console, Group
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.columns import Columns
    from rich.theme import Theme
    from rich.box import DOUBLE, ROUNDED, MINIMAL, HEAVY
    from rich.bar import Bar
    from rich.live import Live

    _theme = Theme({
        "info": "bold blue",
        "warning": "dark_orange",
        "error": "red",
        "success": "green",
        "dir": "bold #00d9ff",
        "file": "bold #b8c5d6",
        "link": "#e4e6eb",
        "status_ok": "bold green",
        "status_fail": "bold red",
        "status_checking": "bold orange1",
        "title": "bold #00d9ff",
        "subtitle": "#8892a0",
        "sum_pass": "bold #3ec843",
        "sum_fail": "bold #ff4757",
        "sum_warn": "bold #ffa500",
        "sum_total": "bold #00d9ff",
    })
    _console = Console(theme=_theme)
else:
    _console = None

def print_header(total_urls: int):
    """Print the tool header banner.

    Args:
        total_urls: Number of URLs to check.
    """
    with _print_lock:
        if HAS_RICH:
            banner = (
                "██╗     ██╗███╗   ██╗██╗  ██╗ ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗███████╗██████╗ \n"
                "██║     ██║████╗  ██║██║ ██╔╝██╔════╝██║  ██║██╔════╝██╔════╝██║ ██╔╝██╔════╝██╔══██╗\n"
                "██║     ██║██╔██╗ ██║█████╔╝ ██║     ███████║█████╗  ██║     █████╔╝ █████╗  ██████╔╝\n"
                "██║     ██║██║╚██╗██║██╔═██╗ ██║     ██╔══██║██╔══╝  ██║     ██╔═██╗ ██╔══╝  ██╔══██╗\n"
                "███████╗██║██║ ╚████║██║  ██╗╚██████╗██║  ██║███████╗╚██████╗██║  ██╗███████╗██║  ██║\n"
                "╚══════╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝\n"
            )
            header = Panel(
                Text.from_ansi(banner),
                box=DOUBLE,
                expand=False,
                border_style="title",
                subtitle="Scanning documentation for broken links...",
                subtitle_align="center",
                padding=(1, 2),
            )
            _console.print(header)
            _console.print("\nUSAGE:\n  sphinx-link-checker [OPTIONS] DOCS_PATH")
            _console.print("\nOPTIONS:\n  --output, -o TEXT              Output format: table, json, csv [default: table]")
            _console.print("  --timeout INTEGER              Request timeout in seconds [default: 10]")
            _console.print("  --external / --no-external     Check external links [default: no-external]")
            _console.print("  --log-level TEXT               DEBUG, INFO, WARNING [default: INFO]")
            _console.print("  --help                         Show this message and exit")
            _console.print()
        else:
            print(f"Starting linkcheck for {total_urls} URLs...")
            print("Checked links:")

def summarize(failures: Dict[str, str], url_map: Dict[str, List[Tuple[str, int]]], build_dir: str, start_time: float, files_scanned: int):
    """Print final summary report with broken links and statistics.

    Separates failures into hard errors (404s) and warnings (timeouts, rate limits).

    Args:
        failures: Map of URL to error message.
        url_map: Map of URL to (file, line) locations.
        build_dir: Sphinx build directory.
        start_time: When the check started (monotonic time).
        files_scanned: Number of HTML files scanned.
    """
    end_time = time.monotonic()
    duration = end_time - start_time
    total_links = len(url_map)
    broken_count = len(failures)

    infra_keywords = ["timeout", "connection", "ssl", "403", "500", "502", "503", "504", "reset"]
    warnings: Dict[str, str] = {}
    final_failures: Dict[str, str] = {}

    for url, err in failures.items():
        if any(kw in err.lower() for kw in infra_keywords):
            warnings[url] = err
        else:
            final_failures[url] = err

    warning_count = len(warnings)
    fail_count = len(final_failures)
    pass_count = total_links - broken_count

    if HAS_RICH:
        console = _console
        console.print()
        console.print(Text("SUMMARY REPORT", style="title"))
        console.print("─" * 80)

        def get_bar(count, total, color):
            percentage = (count / total * 100) if total > 0 else 0
            bar_width = 32
            filled = int(percentage / 100 * bar_width)
            bar_text = "█" * filled + "░" * (bar_width - filled)
            return f"│{bar_text} {percentage:>5.1f}%"

        console.print(f"  [success]✓ Passing[/]       {pass_count:>3}  {get_bar(pass_count, total_links, 'success')}")
        console.print(f"  [error]✗ Broken[/]        {fail_count:>3}  {get_bar(fail_count, total_links, 'error')}")
        console.print(f"  [warning]⚠ Warnings[/]      {warning_count:>3}  {get_bar(warning_count, total_links, 'warning')}")
        console.print("  " + "─" * 21)
        console.print(f"  Total           {total_links:>3}\n")

        if final_failures:
            console.print()
            console.print(Text("BROKEN LINKS DETAILS", style="sum_fail"))
            console.print("─" * 80)

            fail_items = []
            for url, err in final_failures.items():
                locations = url_map.get(url, [])
                for html_path, _ in locations:
                    rel_html = normalize_html_path(html_path, build_dir)
                    src_path, src_line = find_source_info(rel_html, url)
                    fail_items.append((src_path, src_line, url, err))

            total_fails = len(fail_items)
            for i, (src_path, src_line, url, err) in enumerate(fail_items, 1):
                console.print(f"[error]❌ [{i}/{total_fails}][/] [file]{src_path}:{src_line}[/]")
                console.print(f"    [subtitle]Link:[/] [link]{url}[/]")
                console.print(f"    [subtitle]Error:[/] [error]{err}[/]")
                console.print()

        if warnings:
            console.print()
            console.print(Text(f"WARNINGS", style="sum_warn"))
            console.print("─" * 80)

            warn_items = []
            for url, err in warnings.items():
                locations = url_map.get(url, [])
                for html_path, _ in locations:
                    rel_html = normalize_html_path(html_path, build_dir)
                    src_path, src_line = find_source_info(rel_html, url)
                    warn_items.append((src_path, src_line, url, err))

            total_warns = len(warn_items)
            for i, (src_path, src_line, url, err) in enumerate(warn_items, 1):
                console.print(f"[warning]⚠ [{i}/{total_warns}][/] [file]{src_path}:{src_line}[/]")
                console.print(f"    [subtitle]Link:[/] [link]{url}[/]")
                console.print(f"    [subtitle]Warning:[/] [warning]{err}[/]")
                console.print()

        console.print()
        console.print(Text("EXECUTION SUMMARY", style="title"))
        console.print("─" * 80)

        console.print(f"  [subtitle]Duration[/]          {duration:.3f}s")
        console.print(f"  [subtitle]Files Scanned[/]     {files_scanned}")
        console.print(f"  [subtitle]Links Processed[/]   {total_links}")

        status_text = "[warning]⚠ ISSUES FOUND[/]" if broken_count > 0 else "[success]PASS[/]"
        console.print(f"  [subtitle]Status[/]            {status_text}")
        if fail_count > 0:
            console.print("\n  [error]Exit Code: 1 (broken links detected)[/]")

        console.print()
    else:
        print("\nBroken links summary:")
        for url, error in failures.items():
            print(f"- {url} ({error})")
