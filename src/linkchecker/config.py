import os
import ast
from typing import List, Tuple
from .colors import _print_lock

def load_config(conf_path: str) -> Tuple[List[str], List[str], float, int]:
    """
    Load linkcheck_ignore, exclude_patterns, sitemap_excludes, linkcheck_timeout,
    and linkcheck_retries from conf.py using AST.

    Why use AST parsing instead of executing/importing the file?
    1. Safety: conf.py is an executable Python script. Using AST allows us to extract
       data without running arbitrary code.
    2. Dependency Isolation: conf.py often imports Sphinx extensions or local modules.
       AST parsing works even if those dependencies are missing from the current
       environment, preventing ModuleNotFoundErrors.
    3. Performance: Parsing the syntax tree is faster than initializing a full Python
       execution context for a script that we only need a few list values from.

    Example:
        Input (conf.py):
            linkcheck_ignore = [
                'https://localhost:*',
                'https://internal.company.com/*'
            ]
            exclude_patterns = ['_build', '.sphinx']
            linkcheck_timeout = 15
            linkcheck_retries = 3

        Output:
            >>> ignore_urls, exclude_patterns, timeout, retries = load_config('conf.py')
            >>> print(ignore_urls)
            ['https://localhost:*', 'https://internal.company.com/*']
            >>> print(exclude_patterns)
            ['_build', '.sphinx']
            >>> print(timeout, retries)
            15.0 3
    """
    ignore_urls = []
    exclude_patterns = []
    timeout = 10.0  # default (reduced from 12s for faster checks)
    max_retries = 1  # default (reduced from 2 for faster failure handling)

    if not os.path.exists(conf_path):
        return ignore_urls, exclude_patterns, timeout, max_retries

    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue

            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue

                target_id = target.id

                # Handle list-type configs
                if target_id in ("linkcheck_ignore", "exclude_patterns", "sitemap_excludes"):
                    try:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, list):
                            if target_id == "linkcheck_ignore":
                                ignore_urls.extend(str(v) for v in value)
                            else:
                                exclude_patterns.extend(str(v) for v in value)
                    except (ValueError, SyntaxError):
                        # Skip values that aren't simple literals
                        continue

                # Handle numeric configs
                elif target_id == "linkcheck_timeout":
                    try:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, (int, float)):
                            timeout = float(value)
                    except (ValueError, SyntaxError):
                        continue

                elif target_id == "linkcheck_retries":
                    try:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, int):
                            max_retries = value
                    except (ValueError, SyntaxError):
                        continue

    except Exception as e:
        with _print_lock:
            print(f"Warning: Could not parse config from {conf_path}: {e}")

    return ignore_urls, exclude_patterns, timeout, max_retries
