"""The per-user settings file `~/.jevmem/config.jsonc`."""
from pathlib import Path

import pytest

from jevmem import user_config
from jevmem.service import db_path

FILE = """\
// comment
{
    "db": "~/.jevmem/spacemaker.db", /* inline */
    "model": "jevk5:4b",
    "timeout": 30,
    "api_key": "secret-from-file"
}
"""


def write(tmp_path: Path, text: str) -> dict:
    p = tmp_path / "config.jsonc"
    p.write_text(text, encoding="utf-8")
    return {user_config.CONFIG_ENV: str(p)}


def test_settings_become_variables_and_tilde_is_expanded(tmp_path):
    env = write(tmp_path, FILE)
    user_config.apply(env)
    assert env["JEVMEM_DB"] == str(Path("~/.jevmem/spacemaker.db").expanduser())
    assert env["JEVMEM_MODEL"] == "jevk5:4b" and env["JEVMEM_TIMEOUT"] == "30"
    assert env["TYPESAFE_API_KEY"] == "secret-from-file" and "JEVMEM_API_KEY" not in env


def test_the_environment_wins_over_the_file(tmp_path):
    env = write(tmp_path, FILE)
    env["JEVMEM_MODEL"] = "other"
    env["JEVMEM_DB"] = ""          # empty counts as unset
    user_config.apply(env)
    assert env["JEVMEM_MODEL"] == "other" and env["JEVMEM_DB"].endswith("spacemaker.db")


def test_a_key_goes_to_the_server_variable_when_there_is_a_base_url(tmp_path):
    env = write(tmp_path, '{"base_url": "http://localhost:11435", "api_key": "k"}')
    user_config.apply(env)
    assert env["JEVMEM_API_KEY"] == "k" and "TYPESAFE_API_KEY" not in env


def test_no_file_is_a_no_op(tmp_path):
    env = {user_config.CONFIG_ENV: str(tmp_path / "missing.jsonc")}
    assert user_config.apply(env) is None and list(env) == [user_config.CONFIG_ENV]


@pytest.mark.parametrize("text, message", [
    ("{not json", "not valid JSON"),
    ("[]", "must be a JSON object"),
    ('{"scope": "project:x"}', "unknown key 'scope'"),
    ('{"timeout": "soon"}', "timeout must be"),
    ('{"api_key": ""}', "api_key must be"),
])
def test_malformed_files_are_rejected_with_the_path(tmp_path, text, message):
    env = write(tmp_path, text)
    with pytest.raises(user_config.ConfigError, match=message):
        user_config.apply(env)


def test_init_writes_a_template_that_parses_and_refuses_to_overwrite(tmp_path):
    env = {user_config.CONFIG_ENV: str(tmp_path / "sub" / "config.jsonc")}
    path = user_config.init(env=env)
    assert user_config.read(path, env) == {}
    with pytest.raises(user_config.ConfigError, match="already exists"):
        user_config.init(env=env)
    user_config.init(force=True, env=env)


def test_db_path_expands_a_tilde(monkeypatch):
    monkeypatch.setenv("JEVMEM_DB", "~/.jevmem/x.db")
    assert db_path() == str(Path("~/.jevmem/x.db").expanduser())


def test_trailing_commas_are_accepted_but_not_inside_strings(tmp_path):
    env = write(tmp_path, '{\n  "model": "a,}",\n  "timeout": 5,\n}')
    user_config.apply(env)
    assert env["JEVMEM_MODEL"] == "a,}" and env["JEVMEM_TIMEOUT"] == "5"


def test_init_can_write_the_key_and_db_json_escaped(tmp_path):
    env = {user_config.CONFIG_ENV: str(tmp_path / "config.jsonc")}
    path = user_config.init(env=env, api_key='k"ey\1', db="~/x.db")
    out = user_config.read(path, env)
    assert out["TYPESAFE_API_KEY"] == 'k"ey\1' and out["JEVMEM_DB"].endswith("x.db")


def test_cli_config_init_with_a_key_then_show_hides_it(tmp_path, monkeypatch, capsys):
    from jevmem import cli
    monkeypatch.setenv("JEVMEM_CONFIG", str(tmp_path / "c.jsonc"))
    cli.main(["config", "init", "--api-key", "sekret"])
    cli.main(["config", "show"])
    out = capsys.readouterr().out
    assert "wrote" in out and "TYPESAFE_API_KEY=***" in out and "sekret" not in out


def test_cli_config_init_without_a_key_says_what_is_missing(tmp_path, monkeypatch, capsys):
    from jevmem import cli
    monkeypatch.setenv("JEVMEM_CONFIG", str(tmp_path / "c.jsonc"))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(""))
    cli.main(["config", "init"])
    assert "no api_key set" in capsys.readouterr().out
