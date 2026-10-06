#!/usr/bin/env python3
"""Cross-platform launcher for @jkudish/jev-mcp: loads ~/.jevmem/config.jsonc (api_key -> TYPESAFE_API_KEY) and runs npx."""
from __future__ import annotations

import os
import subprocess
import sys

from jevmem import user_config


def main() -> int:
    user_config.apply()
    # Windows needs shell=True so npx.cmd resolves; Unix does not.
    return subprocess.call(["npx", "-y", "@jkudish/jev-mcp"], shell=(os.name == "nt"))


if __name__ == "__main__":
    sys.exit(main())
