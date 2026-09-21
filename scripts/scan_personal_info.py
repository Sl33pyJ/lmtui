#!/usr/bin/env python3
"""Pre-commit hook that blocks personal info from entering Git history.

Reads terms from ~/.config/precommit-personal/watchlist.txt (one per line).
Scans all files staged for commit. Case-insensitive matching. Fails the
commit if any term is found.
"""

# ------ Imports ------
import subprocess
import sys
from pathlib import Path


# ------ Configuration ------

WATCHLIST_PATH = Path.home() / ".config" / "precommit-personal" / "watchlist.txt"


# ------ Helpers ------

def load_watchlist() -> list[str]:
    """Read the watchlist file. Return an empty list if it doesn't exist."""
    if not WATCHLIST_PATH.exists():
        return []
    lines = WATCHLIST_PATH.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def get_staged_files() -> list[Path]:
    """Return paths of files staged for commit."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(p) for p in result.stdout.splitlines() if p]


def scan_file(path: Path, terms: list[str]) -> list[tuple[str, int]]:
    """Return (term, line_number) pairs for any watchlist terms found in path."""
    hits: list[tuple[str, int]] = []
    try:
        if b"\0" in path.read_bytes()[:8000]:
            return hits
        content = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return hits

    for lineno, line in enumerate(content.splitlines(), start=1):
        lower = line.lower()
        for term in terms:
            if term.lower() in lower:
                hits.append((term, lineno))
    return hits


# ------ Entry point ------

def main() -> int:
    terms = load_watchlist()
    if not terms:
        return 0

    files = get_staged_files()
    if not files:
        return 0

    any_hits = False
    for path in files:
        if not path.exists():
            continue
        hits = scan_file(path, terms)
        for term, lineno in hits:
            print(f"  {path}:{lineno}  contains: {term!r}", file=sys.stderr)
            any_hits = True

    if any_hits:
        print(file=sys.stderr)
        print("Personal-info scanner blocked this commit.", file=sys.stderr)
        print(
            f"Fix the offending lines, or edit {WATCHLIST_PATH} to remove "
            "false positives.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
