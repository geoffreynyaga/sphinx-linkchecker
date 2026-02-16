from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Tuple
from .utils import should_exclude_path

class LinkParser(HTMLParser):
    """Parse HTML to extract external HTTP/HTTPS links.

    Example:
        Input (Sphinx-generated HTML):
            <a href="https://docs.python.org">Python Docs</a>
            <img src="https://example.com/logo.png">

        Output:
            >>> url_map = {}
            >>> parser = LinkParser("index.html", url_map)
            >>> parser.feed('<a href="https://docs.python.org">Link</a>')
            >>> print(url_map)
            {'https://docs.python.org': [('index.html', 1)]}
    """
    def __init__(self, html_path: str, url_map: Dict[str, List[Tuple[str, int]]]):
        super().__init__()
        self.html_path = html_path
        self.url_map = url_map

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key not in ("href", "src") or not value:
                continue
            if value.startswith("http://") or value.startswith("https://"):
                line, _ = self.getpos()
                self.url_map.setdefault(value, []).append((self.html_path, line))

def find_links(build_dir: str, exclude_patterns: List[str]) -> Dict[str, List[Tuple[str, int]]]:
    """Scan Sphinx HTML build directory for external links.

    Args:
        build_dir: Path to Sphinx HTML output (e.g., '_build/html').
        exclude_patterns: Patterns to skip (e.g., ['_static', '.sphinx']).

    Returns:
        Map of URL to list of (file, line) locations.

    Example:
        Input (Sphinx build directory):
            _build/html/
            ├── index.html  (contains https://python.org)
            └── guide.html  (contains https://python.org, https://github.com)

        Output:
            >>> url_map = find_links('_build/html', [])
            >>> print(url_map)
            {
                'https://python.org': [('index.html', 42), ('guide.html', 15)],
                'https://github.com': [('guide.html', 23)]
            }
    """
    url_map: Dict[str, List[Tuple[str, int]]] = {}
    for html_path in Path(build_dir).rglob("*.html"):
        rel_path = str(html_path.relative_to(build_dir))
        if should_exclude_path(rel_path, exclude_patterns):
            continue
        try:
            content = html_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        parser = LinkParser(rel_path, url_map)
        parser.feed(content)
    return url_map
