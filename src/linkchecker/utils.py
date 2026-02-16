import os
import fnmatch
import threading
from typing import Dict, List, Tuple
from pathlib import Path


def normalize_html_path(path: str, build_dir: str) -> str:
    """Normalize an HTML path relative to the build directory.

    Args:
        path (str): The absolute or relative path to an HTML file.
        build_dir (str): The documentation build directory.

    Returns:
        str: The normalized relative path.
    """
    path = path.replace("\\", "/")
    build_dir = build_dir.replace("\\", "/").rstrip("/")
    prefix = f"{build_dir}/"
    if path.startswith(prefix):
        return path[len(prefix) :]
    return path

def format_file_list(url_map: Dict[str, List[Tuple[str, int]]], url: str, build_dir: str) -> str:
    """Format a list of files containing a specific URL.

    Args:
        url_map (Dict[str, List[Tuple[str, int]]]): Mapping of URLs to their locations.
        url (str): The URL to look up.
        build_dir (str): The documentation build directory.

    Returns:
        str: A formatted string containing the list of files, e.g., " [index.html; contact.html]".
    """
    locations = url_map.get(url, [])
    if not locations:
        return ""
    files = sorted({normalize_html_path(path, build_dir) for path, _ in locations})
    return " [" + "; ".join(files) + "]"

def should_ignore_url(url: str, ignore_patterns: List[str]) -> bool:
    """Check if a URL should be ignored based on configuration patterns.

    Args:
        url (str): The URL to check.
        ignore_patterns (List[str]): List of glob patterns or direct substrings to ignore.

    Returns:
        bool: True if the URL should be ignored, False otherwise.
    """
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(url, pattern) or (pattern in url):
            return True
    return False

def should_exclude_path(path: str, exclude_patterns: List[str]) -> bool:
    """Check if an HTML path should be excluded from scanning.

    Args:
        path (str): The relative path to an HTML file.
        exclude_patterns (List[str]): List of glob patterns or direct substrings to exclude.

    Returns:
        bool: True if the path should be excluded, False otherwise.
    """
    # Always exclude .sphinx/
    if ".sphinx/" in path:
        return True
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(path, pattern) or (pattern in path):
            return True
    return False

def find_line_number(file_path: str, url: str) -> int:
    """Find the first occurrence of the URL in the source file.

    Args:
        file_path (str): Path to the source file (.md, .rst, etc.).
        url (str): The URL to search for.

    Returns:
        int: The 1-indexed line number, or 0 if not found.
    """
    if not os.path.exists(file_path):
        return 0
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                if url in line:
                    return i
    except OSError:
        pass
    return 0

def find_source_info(html_rel_path: str, url: str) -> Tuple[str, int]:
    """Map an HTML path back to its source file and find the URL's line number.

    Heuristically translates paths like 'folder/index.html' to 'folder/index.md'
    or 'folder.html' to 'folder.rst'.

    Args:
        html_rel_path (str): The relative path to the built HTML file.
        url (str): The URL to find in the source file.

    Returns:
        Tuple[str, int]: A tuple of (source_file_path, line_number).
    """
    base = html_rel_path
    if base.endswith("index.html"):
        base = base[:-len("index.html")].rstrip("/")
    elif base.endswith(".html"):
        base = base[:-len(".html")]
    
    if not base or base == ".":
        possibles = ["index.md", "index.rst"]
    else:
        possibles = [
            f"{base}.md",
            f"{base}.rst",
            f"{base}/index.md",
            f"{base}/index.rst"
        ]
    
    for src in possibles:
        if os.path.exists(src):
            line = find_line_number(src, url)
            if line > 0:
                return src, line
    
    # Second pass: just return the file if it exists even if link search failed
    for src in possibles:
        if os.path.exists(src):
            return src, 0
            
    return html_rel_path, 0


def map_urls_to_files(url_map: Dict[str, List[Tuple[str, int]]], build_dir: str) -> Dict[str, List[str]]:
    """Transform the URL map into a mapping of URL to a list of normalized files.

    Args:
        url_map (Dict[str, List[Tuple[str, int]]]): The raw URL map from the crawler.
        build_dir (str): The documentation build directory.

    Returns:
        Dict[str, List[str]]: Mapping of URL to list of relative HTML paths.
    """
    url_to_files: Dict[str, List[str]] = {}
    for url, locations in url_map.items():
        files = sorted({normalize_html_path(path, build_dir) for path, _ in locations})
        url_to_files[url] = files
    return url_to_files


def map_files_to_urls(url_to_files: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Reverse a URL-to-files map to a file-to-URLs map.

    Args:
        url_to_files (Dict[str, List[str]]): Mapping of URL to list of files.

    Returns:
        Dict[str, List[str]]: Mapping of file path to list of URLs in that file.
    """
    file_to_urls: Dict[str, List[str]] = {}
    for url, files in url_to_files.items():
        for file_path in files:
            file_to_urls.setdefault(file_path, []).append(url)
    return file_to_urls
