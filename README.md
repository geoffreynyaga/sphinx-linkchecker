# sphinx-linkchecker

```
██╗     ██╗███╗   ██╗██╗  ██╗ ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗███████╗██████╗
██║     ██║████╗  ██║██║ ██╔╝██╔════╝██║  ██║██╔════╝██╔════╝██║ ██╔╝██╔════╝██╔══██╗
██║     ██║██╔██╗ ██║█████╔╝ ██║     ███████║█████╗  ██║     █████╔╝ █████╗  ██████╔╝
██║     ██║██║╚██╗██║██╔═██╗ ██║     ██╔══██║██╔══╝  ██║     ██╔═██╗ ██╔══╝  ██╔══██╗
███████╗██║██║ ╚████║██║  ██╗╚██████╗██║  ██║███████╗╚██████╗██║  ██╗███████╗██║  ██║
╚══════╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
```

A fast, parallel link checker for Sphinx documentation projects.

## Features

- **Inline broken link reporting** - see failures immediately as they occur (no waiting for batched results)
- **Responsive parallel checking** - processes results as they complete, not blocked by slow URLs
- **Smart caching** - recheck only failed links with `--fails-only`
- **Beautiful output** with Rich library for terminal colors and hierarchical formatting
- **Source mapping** - traces broken links back to `.md`/`.rst` files with line numbers
- **Rate limiting** per host to avoid overwhelming servers
- **Intelligent retries** - connection timeouts fail fast (host is unreachable), while HTTP 429/5xx get exponential backoff
- **Config file support** - reads `linkcheck_timeout`, `linkcheck_retries`, `linkcheck_ignore` from `conf.py` via safe AST parsing

## Installation

```bash
pip install sphinx-linkchecker
```

## Basic Usage

```bash
# Check all links in your Sphinx build
linkcheck --build-dir _build/html

# Recheck only previously failed links
linkcheck --fails-only

# Limit to 100 URLs for quick testing
linkcheck --max-urls 100

# Use custom timeout and workers
linkcheck --timeout 15 --workers 10
```

## Configuration

The tool automatically reads settings from your Sphinx `conf.py`:

```python
# conf.py
linkcheck_timeout = 12          # Request timeout in seconds (default: 12)
linkcheck_retries = 2           # Max retries for HTTP 429/5xx errors (default: 2)
linkcheck_ignore = [
    'https://localhost:*',
    'https://internal.company.com/*'
]

exclude_patterns = ['_build', '.sphinx']
```

### Retry Behavior

- **Connection timeouts** (host unreachable) → fail fast immediately
- **HTTP 429** (rate limited) or **5xx** errors → retry with exponential backoff
- **HTTP 404** (not found) → fail immediately, no retry

CLI arguments override `conf.py` settings:

```bash
linkcheck --timeout 15 --max-retries 3
```

## Sample Output

```
╔══════════════════════════════════════════════════════════════════════════════╗
║ ██╗     ██╗███╗   ██╗██╗  ██╗ ██████╗██╗  ██╗███████╗ ██████╗██╗  ██╗███████╗██████╗  ║
║ ██║     ██║████╗  ██║██║ ██╔╝██╔════╝██║  ██║██╔════╝██╔════╝██║ ██╔╝██╔════╝██╔══██╗ ║
║ ██║     ██║██╔██╗ ██║█████╔╝ ██║     ███████║█████╗  ██║     █████╔╝ █████╗  ██████╔╝ ║
║ ██║     ██║██║╚██╗██║██╔═██╗ ██║     ██╔══██║██╔══╝  ██║     ██╔═██╗ ██╔══╝  ██╔══██╗ ║
║ ███████╗██║██║ ╚████║██║  ██╗╚██████╗██║  ██║███████╗╚██████╗██║  ██╗███████╗██║  ██║ ║
║ ╚══════╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ║
║                    Scanning documentation for broken links...                ║
╚══════════════════════════════════════════════════════════════════════════════╝

docs/
  index.html checking...
    ├── ✓ https://docs.python.org/3/                    OK
    └── ✓ https://github.com/sphinx-doc/sphinx          OK
    [2/2 ✓]

  guide/
    installation.html checking...
      ├── ✗ https://example.com/broken                  HTTP 404
      └── ⚠ https://slow-server.com/timeout            Connection timeout
      [0/2 ✗]

SUMMARY REPORT
────────────────────────────────────────────────────────────────────────────────
  ✓ Passed      ████████████████████░░░░░░░░  75% (150/200)
  ✗ Failed      ███░░░░░░░░░░░░░░░░░░░░░░░░░  15% (30/200)
  ⚠ Warnings    ██░░░░░░░░░░░░░░░░░░░░░░░░░░  10% (20/200)

BROKEN LINKS DETAILS
────────────────────────────────────────────────────────────────────────────────
❌ [1/30] guide/installation.md:42
   URL: https://example.com/broken
   Error: HTTP 404

EXECUTION SUMMARY
────────────────────────────────────────────────────────────────────────────────
  Duration          12.345s
  Files Scanned     45
  Links Processed   200
  Status            ⚠ ISSUES FOUND
```

## Development

### Build the Package

```bash
# Install build dependencies
pip install build

# Build distribution
python -m build
```

This creates:
- `dist/sphinx_linkchecker-*.whl` (wheel)
- `dist/sphinx-linkchecker-*.tar.gz` (source)

### Install for Development

```bash
# Install in editable mode
pip install -e .

# With optional dependencies
pip install -e ".[rich]"
```

### Run Tests

```bash
# Test the installed command
linkcheck --help

# Run on sample documentation
cd /path/to/sphinx/docs
make html
linkcheck --build-dir _build/html
```

## Command-Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--build-dir` | `_build` | Sphinx HTML build directory |
| `--cache-dir` | `.sphinx/linkcheck` | Cache directory for failed URLs |
| `--fails-only` | `False` | Only check previously failed URLs |
| `--timeout` | `12.0` | Request timeout in seconds |
| `--workers` | `6` | Number of parallel workers |
| `--per-host-delay` | `0.5` | Delay between requests to same host |
| `--max-retries` | `2` | Maximum retry attempts |
| `--max-urls` | `0` | Limit number of URLs (0 = unlimited) |
| `--conf` | `conf.py` | Path to Sphinx configuration file |

## How It Works

1. **Crawl**: Scans all HTML files in `--build-dir` for external links
2. **Filter**: Applies ignore patterns from `conf.py`
3. **Check**: Tests links in parallel with rate limiting
4. **Report**: Separates hard failures (404) from warnings (timeouts)
5. **Cache**: Saves failures to `.sphinx/linkcheck/failures.json`

## License

MIT

## Contributing

Contributions welcome! Please open an issue or PR on GitHub.
