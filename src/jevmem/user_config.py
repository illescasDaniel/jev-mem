"""Per-user settings: `~/.jevmem/config.jsonc` (JSON with comments), the place to say where the database lives and
which decision model to use, once for every project and every editor.

It lives outside every repository, so it is the one place for the API key too (there is no `.env` support). Each key becomes the matching `JEVMEM_*`
variable unless that variable is already set (a real environment variable): the environment wins. The scope is
deliberately not a key: it differs per project, so it stays in the MCP/hook config (`JEVMEM_SCOPE`)."""
from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import MutableMapping
from pathlib import Path

CONFIG_ENV = "JEVMEM_CONFIG"   # an explicit path: wins over the default location
_KEYS = {   # key -> (variable, accepted types)
    "db": ("JEVMEM_DB", (str,)),
    "base_url": ("JEVMEM_BASE_URL", (str,)),
    "model": ("JEVMEM_MODEL", (str,)),
    "timeout": ("JEVMEM_TIMEOUT", (int, float)),
    "embedder": ("JEVMEM_EMBEDDER", (str,)),
    "index": ("JEVMEM_INDEX", (str,)),
    "recall_mode": ("JEVMEM_RECALL_MODE", (str,)),
}
_KEY_FIELD = "api_key"   # -> JEVMEM_API_KEY with a base_url, else TYPESAFE_API_KEY (hosted Jev)

TEMPLATE = """\
// jevmem user settings (JSON with comments: // and /* */).
// Environment variables (JEVMEM_DB, JEVMEM_BASE_URL, TYPESAFE_API_KEY, ...) override anything here.
// "~" works in "db" on every OS. The scope (which project a note belongs to) is NOT set here: use JEVMEM_SCOPE.
{
    // Where the notes live. Default: ~/.jevmem/memory.db
    // "db": "~/.jevmem/memory.db",

    // Hosted Jev (TypeSafe) key. This file lives outside every repository, so the key goes right here (on Linux/macOS
    // jevmem warns unless the file is chmod 600). Not needed with a local model (see base_url below).
    // "api_key": "..."

    // A local or third-party decision model instead of hosted Jev (no Jev key needed; "api_key" is then only for
    // servers that want one):
    // "base_url": "http://localhost:11435",
    // "model": "jevk5:4b",
    // "timeout": 60
}
"""


class ConfigError(Exception):
    pass


def config_path(env: MutableMapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env[CONFIG_ENV]).expanduser() if env.get(CONFIG_ENV) else Path.home() / ".jevmem" / "config.jsonc"


def strip_comments(text: str) -> str:
    """Remove `//` and `/* */` comments (and trailing commas) outside strings, leaving plain JSON."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return _drop_trailing_commas("".join(out))


def _drop_trailing_commas(text: str) -> str:
    """`{"a": 1,}` is accepted (JSONC allows it, and it is what you get by activating a line of the template)."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "," and text[i + 1:].lstrip()[:1] in ("}", "]"):
            pass
        else:
            out.append(c)
        i += 1
    return "".join(out)


def read(path: Path, env: MutableMapping[str, str]) -> dict[str, str]:
    """The file as `JEVMEM_*` variables. Raises ConfigError for anything malformed."""
    try:
        data = json.loads(strip_comments(path.read_text(encoding="utf-8")))
    except OSError as e:
        raise ConfigError(f"{path}: cannot read: {e}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: not valid JSON ({e})") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: must be a JSON object")
    out: dict[str, str] = {}
    for key, value in data.items():
        if key == _KEY_FIELD:
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{path}: {key} must be a non-empty string")
            continue
        if key not in _KEYS:
            raise ConfigError(f"{path}: unknown key {key!r} (allowed: {', '.join(sorted([*_KEYS, _KEY_FIELD]))})")
        var, types = _KEYS[key]
        if not isinstance(value, types) or isinstance(value, bool):
            raise ConfigError(f"{path}: {key} must be {' or '.join(t.__name__ for t in types)}")
        out[var] = str(Path(value).expanduser()) if key == "db" and value.startswith("~") else str(value)
    if key := data.get(_KEY_FIELD):
        has_server = bool(out.get("JEVMEM_BASE_URL") or env.get("JEVMEM_BASE_URL"))
        out["JEVMEM_API_KEY" if has_server else "TYPESAFE_API_KEY"] = key
    if _KEY_FIELD in data and os.name == "posix" and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"jevmem: warning: {path} holds an API key and is readable by others; run chmod 600 on it "
              "(it holds the key)", file=sys.stderr)
    return out


def apply(env: MutableMapping[str, str] | None = None) -> Path | None:
    """Fill every `JEVMEM_*` variable the environment leaves unset or empty from the settings file. No file: no-op.
    Returns the file used. Idempotent."""
    env = os.environ if env is None else env
    path = config_path(env)
    if not path.is_file():
        return None
    for var, value in read(path, env).items():
        if not env.get(var):
            env[var] = value
    return path


def render(api_key: str | None = None, db: str | None = None) -> str:
    """The template with `api_key` and/or `db` switched on (JSON-escaped)."""
    text = TEMPLATE
    if api_key:
        text = text.replace('    // "api_key": "..."', f'    "api_key": {json.dumps(api_key)}')
    if db:
        text = text.replace('    // "db": "~/.jevmem/memory.db",', f'    "db": {json.dumps(db)},')
    return text


def init(force: bool = False, env: MutableMapping[str, str] | None = None, api_key: str | None = None,
         db: str | None = None) -> Path:
    path = config_path(env)
    if path.exists() and not force:
        raise ConfigError(f"{path} already exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(api_key, db), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path
