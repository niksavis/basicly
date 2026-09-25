from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from basicly import cli, config, owned_store, owned_write

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / owned_store.KIT_TRACKER_DIR
KIT_CLI = owned_store.KIT_TRACKER_DIR / "cli.py"
LEDGER = owned_store.LEDGER_DIR.as_posix()
SHAPE = ("--acceptance", "- [ ] it exists", "--requirements", "It exists.")


def engine_repo(tmp_path: Path, tracker_table: str) -> Path:

    target = tmp_path / owned_store.KIT_TRACKER_DIR
    target.mkdir(parents=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, target / source.name)
    (tmp_path / owned_store.LEDGER_DIR).mkdir(parents=True)
    (tmp_path / config.CONFIG_FILE).write_text(
        f'[tracker]\nmode = "owned"\n{tracker_table}', encoding="utf-8"
    )
    return tmp_path


def kit(repo: Path, *argv: str) -> dict[str, Any]:

    done = subprocess.run(
        [sys.executable, KIT_CLI.as_posix(), *argv],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(done.stdout)


def prefix_row(repo: Path) -> dict[str, Any]:

    rows = {row["name"]: row for row in kit(repo, "config", LEDGER)["settings"]}
    return rows["prefix"]


def engine_create(repo: Path) -> str:
    return owned_write.create(repo, ["create", "a root", "-t", "task", *SHAPE])


def kit_create(repo: Path) -> str:
    return str(kit(repo, "create", LEDGER, "--title", "t", *SHAPE)["record"])


def toml_tracker(repo: Path) -> dict[str, Any]:
    return tomllib.loads((repo / config.CONFIG_FILE).read_text(encoding="utf-8"))["tracker"]


def test_engine_create_and_kit_create_mint_the_same_prefix_after_one_config_set(
    tmp_path: Path,
) -> None:

    repo = engine_repo(tmp_path, "")

    assert kit(repo, "config", LEDGER, "set", "prefix", "acme")["set"]["value"] == "acme"

    assert engine_create(repo).startswith("acme-")
    assert kit_create(repo).startswith("acme-")


def test_config_in_an_engine_repo_shows_one_prefix_with_its_home_after_install(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    repo = engine_repo(tmp_path, 'prefix = "acme"\n')

    cli._setup_tracker(repo)

    assert f"to {owned_store.PREFIX_HOME}, its one home" in capsys.readouterr().out
    assert prefix_row(repo) == {
        "name": "prefix",
        "value": "acme",
        "source": "ledger file",
        "home": "template.json in the ledger",
    }
    assert toml_tracker(repo) == {"mode": "owned"}
    assert config.legacy_tracker_prefix(repo) is None
    assert owned_store.tracker_prefix(repo) == "acme"


def test_an_old_repo_mints_under_its_basicly_toml_prefix_before_and_after_install(
    tmp_path: Path,
) -> None:

    repo = engine_repo(tmp_path, 'prefix = "old"\n')

    assert engine_create(repo).startswith("old-")
    cli._setup_tracker(repo)

    assert engine_create(repo).startswith("old-")
    assert kit_create(repo).startswith("old-")


def test_differing_values_are_refused_by_name_and_install_moves_neither(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    repo = engine_repo(tmp_path, 'prefix = "old"\n')
    kit(repo, "config", LEDGER, "set", "prefix", "new")

    with pytest.raises(ValueError, match="two differing values") as refused:
        engine_create(repo)
    cli._setup_tracker(repo)

    for named in ("'new'", "'old'", owned_store.PREFIX_HOME, config.LEGACY_PREFIX_KEY):
        assert named in str(refused.value)
    assert "the id prefix stays where it is" in capsys.readouterr().err
    assert toml_tracker(repo)["prefix"] == "old"
    assert prefix_row(repo)["value"] == "new"
    assert not any((repo / owned_store.LEDGER_DIR).glob("*.jsonl"))


def test_a_dry_run_install_names_the_move_and_makes_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    repo = engine_repo(tmp_path, 'prefix = "acme"\n')

    cli._setup_tracker(repo, dry_run=True)

    assert "Would move [tracker] prefix in basicly.toml = 'acme'" in capsys.readouterr().out
    assert toml_tracker(repo)["prefix"] == "acme"
    assert prefix_row(repo)["source"] == "default"


def test_a_new_repo_is_pointed_at_the_ledger_template_not_basicly_toml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    repo = tmp_path / "acme"
    repo.mkdir()

    cli._setup_tracker(repo)

    out = capsys.readouterr().out
    assert owned_store.set_prefix_command("acme") in out
    assert config.CONFIG_FILE not in out
    assert "[tracker]\nprefix" not in config.DEFAULT_CONFIG_TOML.replace("# ", "")


def test_a_tracker_layout_install_cannot_edit_keeps_both_equal_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    repo = engine_repo(tmp_path, "")
    layout = '[tracker]  # held by hand\nmode = "owned"\nprefix = "acme"\n'
    (repo / config.CONFIG_FILE).write_text(layout, encoding="utf-8")

    cli._setup_tracker(repo)

    assert "delete prefix from [tracker] by hand" in capsys.readouterr().err
    assert (repo / config.CONFIG_FILE).read_text(encoding="utf-8") == layout
    assert prefix_row(repo)["value"] == "acme"
    assert engine_create(repo).startswith("acme-")
