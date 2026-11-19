from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import claude_user_settings as cus
from basicly import cli

if TYPE_CHECKING:
    import pytest


def _settings(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def test_an_empty_home_gets_code_mode_and_no_advisor_by_default(tmp_path: Path) -> None:
    lines = cus.apply(_settings(tmp_path), advisor=cus.DEFAULT_ADVISOR)

    data = json.loads(_settings(tmp_path).read_text())
    assert data == {"env": {cus.CODE_MODE_VAR: "1"}}
    assert len(lines) == 1


def test_a_named_advisor_is_set_when_none_is_set(tmp_path: Path) -> None:
    lines = cus.apply(_settings(tmp_path), advisor="opus")

    assert json.loads(_settings(tmp_path).read_text())[cus.ADVISOR_KEY] == "opus"
    assert all(line.startswith("set") for line in lines)


def test_values_the_user_set_are_kept_including_code_mode_off(tmp_path: Path) -> None:
    path = _settings(tmp_path)
    path.parent.mkdir(parents=True)
    original = {
        "env": {cus.CODE_MODE_VAR: "0", "OTHER": "x"},
        "advisorModel": "fable",
        "model": "opus",
    }
    path.write_text(json.dumps(original))

    lines = cus.apply(path, advisor="sonnet")

    assert json.loads(path.read_text()) == original
    assert all(line.startswith("kept") for line in lines)


def test_other_keys_survive_and_an_empty_advisor_sets_none(tmp_path: Path) -> None:
    path = _settings(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"env": {"OTHER": "x"}, "theme": "auto"}))

    cus.apply(path, advisor="")

    data = json.loads(path.read_text())
    assert data == {"env": {"OTHER": "x", cus.CODE_MODE_VAR: "1"}, "theme": "auto"}


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    lines = cus.apply(_settings(tmp_path), advisor="opus", dry_run=True)

    assert not _settings(tmp_path).exists()
    assert all(line.startswith(("would set", "kept")) for line in lines)


def test_invalid_json_is_refused_and_left_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _settings(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    code = cli.main(["claude-user", "--home", str(tmp_path)])

    assert code == 1
    assert path.read_text() == "{not json"
    assert "not valid JSON" in capsys.readouterr().err


def test_an_env_that_is_not_an_object_is_refused_and_left_unchanged(tmp_path: Path) -> None:
    path = _settings(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"env": ["x"]}))

    code = cli.main(["claude-user", "--home", str(tmp_path)])

    assert code == 1
    assert json.loads(path.read_text()) == {"env": ["x"]}


def test_the_cli_writes_under_the_given_home(tmp_path: Path) -> None:
    code = cli.main(["claude-user", "--home", str(tmp_path), "--advisor", "sonnet"])

    assert code == 0
    assert json.loads(_settings(tmp_path).read_text())["advisorModel"] == "sonnet"


def test_the_path_follows_claude_config_dir_unless_a_home_is_given(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path / "h"), "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}

    assert cus.settings_path("", env) == tmp_path / "cfg" / "settings.json"
    assert cus.settings_path("", {"HOME": str(tmp_path / "h")}) == _settings(tmp_path / "h")
    assert cus.settings_path(str(tmp_path / "x"), env) == _settings(tmp_path / "x")
