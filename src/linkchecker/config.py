import os
import ast
from typing import List, Tuple
from .colors import _print_lock

def load_config(conf_path: str) -> Tuple[List[str], List[str]]:
    """
    Load linkcheck_ignore, exclude_patterns, and sitemap_excludes from conf.py using AST.

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

        Output:
            >>> ignore_urls, exclude_patterns = load_config('conf.py')
            >>> print(ignore_urls)
            ['https://localhost:*', 'https://internal.company.com/*']
            >>> print(exclude_patterns)
            ['_build', '.sphinx']
    """
    ignore_urls = []
    exclude_patterns = []
    
    if not os.path.exists(conf_path):
        return ignore_urls, exclude_patterns

    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        tree = ast.parse(content)
        
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
                
            for target in node.targets:
                if not (isinstance(target, ast.Name) and target.id in (
                    "linkcheck_ignore", "exclude_patterns", "sitemap_excludes"
                )):
                    continue
                
                try:
                    value = ast.literal_eval(node.value)
                    if isinstance(value, list):
                        if target.id == "linkcheck_ignore":
                            ignore_urls.extend(str(v) for v in value)
                        else:
                            exclude_patterns.extend(str(v) for v in value)
                except (ValueError, SyntaxError):
                    # Skip values that aren't simple literals
                    continue
                    
    except Exception as e:
        with _print_lock:
            print(f"Warning: Could not parse config from {conf_path}: {e}")

    return ignore_urls, exclude_patterns
