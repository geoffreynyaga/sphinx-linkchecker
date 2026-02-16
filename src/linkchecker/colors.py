"""Terminal color utilities with Rich library support.

Provides simple color functions that automatically use Rich markup when available,
falling back to ANSI escape codes otherwise.

Example:
    >>> from linkchecker.colors import color_red, color_green, HAS_RICH
    >>> # Input: Plain text
    >>> message = "Link check failed"
    >>> colored = color_red(message)
    >>> 
    >>> # Output (if Rich installed):
    >>> print(colored)
    [red]Link check failed[/red]
    >>> 
    >>> # Output (if Rich not installed):
    >>> print(colored)
    \033[31mLink check failed\033[0m
"""

import threading
try:
    import rich
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

_print_lock = threading.Lock()

"""
Why not pass HAS_RICH as an argument?
While you could pass it (e.g., color_red(text, has_rich=HAS_RICH)), it's generally avoided in
Python for utility functions like these because it adds significant boilerplate to every
single log message. Using a module-level constant is the standard "Clean Code" approach
for this specific scenario.
"""

def color_red(text: str) -> str:
    if HAS_RICH: return f"[red]{text}[/red]"
    return f"\033[31m{text}\033[0m"

def color_green(text: str) -> str:
    if HAS_RICH: return f"[green]{text}[/green]"
    return f"\033[32m{text}\033[0m"

def color_blue(text: str) -> str:
    if HAS_RICH: return f"[blue]{text}[/blue]"
    return f"\033[34m{text}\033[0m"

def color_orange(text: str) -> str:
    if HAS_RICH: return f"[dark_orange]{text}[/dark_orange]"
    return f"\033[33m{text}\033[0m"

def color_yellow(text: str) -> str:
    if HAS_RICH: return f"[yellow]{text}[/yellow]"
    return f"\033[93m{text}\033[0m"
