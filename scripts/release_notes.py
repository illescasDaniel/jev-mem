"""Print the CHANGELOG.md section for a release tag (e.g. ``v1.0.0``)."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def section(changelog: str, version: str) -> str:
    """Return the body of the ``## [version]`` section, or raise ``LookupError``."""
    pattern = rf"^## \[?{re.escape(version)}\]?.*?\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, changelog, re.MULTILINE | re.DOTALL)
    if match is None or not match.group(1).strip():
        raise LookupError(f"No CHANGELOG.md entry for {version}")
    return match.group(1).strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_notes.py TAG", file=sys.stderr)
        return 2
    version = argv[1].removeprefix("v")
    try:
        sys.stdout.write(section(Path("CHANGELOG.md").read_text(encoding="utf-8"), version))
    except (LookupError, OSError) as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
