#!/usr/bin/env python3
"""Cross-platform launcher for @jkudish/jev-mcp: loads ~/.jevmem/.env (then ./.env) and runs npx."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv(Path.home() / ".jevmem" / ".env")
    load_dotenv()
    # Windows needs shell=True so npx.cmd resolves; Unix does not.
    return subprocess.call(["npx", "-y", "@jkudish/jev-mcp"], shell=(os.name == "nt"))


if __name__ == "__main__":
    sys.exit(main())
