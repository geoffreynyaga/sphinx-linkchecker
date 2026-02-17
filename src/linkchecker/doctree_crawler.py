"""Doctree-based link extraction for maximum speed.

This module extracts links directly from Sphinx's pre-built doctrees
(pickled Python objects) instead of parsing HTML. This is 5-10x faster
since doctrees are already structured data with exact source metadata.
"""

import pickle
from pathlib import Path
from typing import Dict, List, Tuple, Set
from concurrent.futures import ThreadPoolExecutor


def _extract_links_from_node(node, source_file: str, links: Dict[str, List[Tuple[str, int]]]) -> None:
    """Recursively extract external HTTP(S) links from a doctree node.
    
    Args:
        node: Doctree node to process.
        source_file: Source file path for this doctree.
        links: Dictionary to populate with {url: [(file, line), ...]}.
    """
    # Handle reference nodes with external URLs
    if hasattr(node, 'attributes') and isinstance(node.attributes, dict):
        refuri = node.attributes.get('refuri', '')
        if refuri and (refuri.startswith('http://') or refuri.startswith('https://')):
            line = node.line if hasattr(node, 'line') else 1
            links.setdefault(refuri, []).append((source_file, line))
    
    # Recursively process children
    if hasattr(node, 'children'):
        for child in node.children:
            _extract_links_from_node(child, source_file, links)


def _process_doctree_file(doctree_path: Path, source_root: Path) -> Dict[str, List[Tuple[str, int]]]:
    """Process a single .doctree file and extract links.
    
    Args:
        doctree_path: Path to the .doctree file.
        source_root: Root directory of source files.
        
    Returns:
        Dictionary mapping URLs to (file, line) locations.
    """
    links: Dict[str, List[Tuple[str, int]]] = {}
    
    try:
        with open(doctree_path, 'rb') as f:
            doctree = pickle.load(f)
        
        # Extract source file from doctree metadata
        source_file = "unknown"
        if hasattr(doctree, 'attributes') and 'source' in doctree.attributes:
            source_file = doctree.attributes['source']
            # Make relative to source root
            try:
                source_file = str(Path(source_file).relative_to(source_root))
            except (ValueError, AttributeError):
                pass
        
        # Extract links from the doctree
        _extract_links_from_node(doctree, source_file, links)
        
    except (pickle.UnpicklingError, EOFError, AttributeError, OSError):
        # Skip corrupted or incompatible doctree files
        pass
    
    return links


def find_links_from_doctrees(build_dir: str, exclude_patterns: List[str]) -> Dict[str, List[Tuple[str, int]]]:
    """Extract links from Sphinx doctree files.
    
    Args:
        build_dir: Path to Sphinx build directory (e.g., '_build').
        exclude_patterns: Patterns to skip (not used for doctrees, kept for API compatibility).
        
    Returns:
        Dictionary mapping URLs to list of (file, line) locations.
        
    Raises:
        FileNotFoundError: If doctrees directory doesn't exist.
    """
    build_path = Path(build_dir)
    
    # Try common doctree locations
    doctree_dirs = [
        build_path / '.doctrees',
        build_path.parent / '.sphinx' / '.doctrees',
        build_path.parent / '.doctrees',
    ]
    
    doctree_dir = None
    for d in doctree_dirs:
        if d.exists() and d.is_dir():
            doctree_dir = d
            break
    
    if not doctree_dir:
        raise FileNotFoundError(f"No doctrees found in {build_dir} or common locations")
    
    # Find all .doctree files
    doctree_files = list(doctree_dir.rglob("*.doctree"))
    
    if not doctree_files:
        raise FileNotFoundError(f"No .doctree files found in {doctree_dir}")
    
    # Determine source root (parent of build dir, typically)
    source_root = build_path.parent
    
    # Process doctrees in parallel for maximum speed
    url_map: Dict[str, List[Tuple[str, int]]] = {}
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(
            lambda p: _process_doctree_file(p, source_root),
            doctree_files
        )
    
    # Merge results
    for local_map in results:
        for url, locations in local_map.items():
            url_map.setdefault(url, []).extend(locations)
    
    return url_map


def extract_links_with_fallback(build_dir: str, exclude_patterns: List[str]) -> Tuple[Dict[str, List[Tuple[str, int]]], bool]:
    """Extract links from doctrees with HTML fallback.
    
    Tries doctree extraction first (5-10x faster), falls back to HTML parsing
    if doctrees are unavailable.
    
    Args:
        build_dir: Path to Sphinx build directory.
        exclude_patterns: Patterns to skip for HTML fallback.
        
    Returns:
        Tuple of (url_map, used_doctrees):
            - url_map: Dictionary mapping URLs to (file, line) locations
            - used_doctrees: True if doctrees were used, False if HTML fallback
    """
    try:
        url_map = find_links_from_doctrees(build_dir, exclude_patterns)
        return url_map, True
    except (FileNotFoundError, OSError):
        # Fall back to HTML parsing
        from .crawler import find_links
        url_map = find_links(build_dir, exclude_patterns)
        return url_map, False
