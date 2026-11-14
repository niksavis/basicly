from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from basicly import __version__, state

ROOT = Path(__file__).resolve().parents[1]
KIT = Path(".basicly/core/kit/tracker")
LEDGER = ".basicly/ledger"


def _run(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        BASICLY_HOLDER="consumer",
        AI_AGENT="consumer",
        BASICLY_TRACKER_STATE=str(repo / "state"),
    )
    for key in ("CI", "BASICLY_TRACKER_EPHEMERAL", "BASICLY_TRACKER_SESSION"):
        env.pop(key, None)
    return subprocess.run(
        [sys.executable, *argv], cwd=repo, env=env, capture_output=True, text=True, check=False
    )


def _legacy_kit(repo: Path) -> None:
    archived = subprocess.run(
        ["git", "archive", "v0.45.0", KIT.as_posix()],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as archive:
        archive.extractall(repo, filter="data")


def _repository(tmp_path: Path, version: str, *, legacy: bool = True) -> tuple[Path, str]:
    repo = tmp_path / "consumer"
    repo.mkdir()
    if legacy:
        _legacy_kit(repo)
    else:
        shutil.copytree(ROOT / KIT, repo / KIT, ignore=shutil.ignore_patterns("__pycache__"))
    cli = str(repo / KIT / "cli.py")
    made = _run(
        repo,
        cli,
        "create",
        LEDGER,
        "--prefix",
        "consumer",
        "--title",
        "An existing consumer record",
        "--field",
        "issue_type=task",
        "--field",
        "size=small",
        "--description",
        "When an engine is upgraded, I want my existing writes to work, so I can keep my ledger.",
        "--acceptance",
        "When a note is added, it shall be readable.",
    )
    assert made.returncode == 0, made.stdout + made.stderr
    state.write_install_state(repo / ".basicly/state/install.json", version, {})
    return repo, json.loads(made.stdout)["record"]


def _logs(repo: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in (repo / LEDGER).glob("*.jsonl")}


@pytest.mark.parametrize("same_generation", [False, True])
def test_a_warm_process_reuses_only_identical_project_module_code(
    tmp_path: Path, same_generation: bool
) -> None:
    old_parent, new_parent = tmp_path / "old", tmp_path / "new"
    old_parent.mkdir()
    new_parent.mkdir()
    old, _record = _repository(old_parent, "0.45.0")
    new, _record = _repository(new_parent, __version__, legacy=same_generation)
    before = (_logs(old), _logs(new))
    driver = (
        "from pathlib import Path; from basicly import owned_store; import sys; "
        "owned_store.kit(Path.cwd(), 'commands'); "
        "owned_store.kit(Path(sys.argv[1]), 'commands')"
    )

    done = _run(old, "-c", driver, str(new))

    if same_generation:
        assert done.returncode == 0, done.stdout + done.stderr
    else:
        assert done.returncode != 0
        assert "fresh basicly process" in done.stderr
    assert (_logs(old), _logs(new)) == before


@pytest.mark.parametrize("operation", ["read", "write"])
def test_the_engine_refuses_an_orphan_archive_even_without_vault_configuration(
    tmp_path: Path, operation: str
) -> None:
    repo, record = _repository(tmp_path, __version__, legacy=False)
    ledger = repo / LEDGER
    archive = ledger.with_name("ledger-archive")
    ledger.rename(archive)
    old = tmp_path / "old-tool"
    _legacy_kit(old)
    made = _run(
        repo,
        str(old / KIT / "cli.py"),
        "create",
        LEDGER,
        "--prefix",
        "consumer",
        "--title",
        "Old writer after interruption",
    )
    assert made.returncode == 0, made.stdout + made.stderr
    before = _logs(repo)
    argv = (
        ("list",) if operation == "read" else ("write", "--", "comments", "add", record, "Refused")
    )

    done = _run(repo, "-m", "basicly.cli", "tracker", *argv)

    assert done.returncode == 1
    assert "archive" in done.stderr
    assert "Traceback" not in done.stderr
    assert _logs(repo) == before


@pytest.mark.parametrize("version", [__version__, "0.1.0", "99.0.0"])
def test_a_supported_legacy_kit_preserves_plain_ledger_writes(tmp_path: Path, version: str) -> None:
    repo, record = _repository(tmp_path, version)
    before = _logs(repo)

    noted = _run(
        repo, "-m", "basicly.cli", "tracker", "write", "--", "comments", "add", record, "Kept"
    )
    changed = _run(
        repo, "-m", "basicly.cli", "tracker", "write", "--", "update", record, "--priority", "1"
    )

    assert noted.returncode == 0, noted.stdout + noted.stderr
    assert changed.returncode == 0, changed.stdout + changed.stderr
    shown = _run(repo, str(repo / KIT / "cli.py"), "show", LEDGER, record)
    assert shown.returncode == 0, shown.stdout + shown.stderr
    result = json.loads(shown.stdout)
    assert result["fields"]["priority"] == 1
    assert "Kept" in result["comments"]
    assert _logs(repo) != before
    assert not (repo / ".basicly-tracker.toml").exists()


@pytest.mark.parametrize("verb", ["comment", "update", "create", "review", "confirm", "close"])
def test_an_old_kit_refuses_vault_writes_by_capability_without_local_events(
    tmp_path: Path, verb: str
) -> None:
    repo, record = _repository(tmp_path, __version__)
    config = repo / ".basicly-tracker.toml"
    config.write_text('vault = "file:///unreachable-generation-vault.git"\nprefix = "consumer"\n')
    before = _logs(repo)
    argv = {
        "comment": ["comments", "add", record, "Refused"],
        "update": ["update", record, "--priority", "1"],
        "create": ["create", "Refused", "-t", "task"],
        "review": ["review", record, "--evidence", "{}"],
        "confirm": ["confirm", record, "--evidence", "{}"],
        "close": ["close", record, "--reason", "Refused"],
    }[verb]

    refused = _run(repo, "-m", "basicly.cli", "tracker", "write", "--", *argv)

    assert refused.returncode == 1
    assert "kit" in refused.stderr and "older" in refused.stderr
    assert "basicly install" in refused.stderr
    assert "Traceback" not in refused.stderr
    assert "has no attribute" not in refused.stderr
    assert _logs(repo) == before
    assert config.read_text().startswith('vault = "file:///unreachable-generation-vault.git"')
    assert not (repo / "state").exists()


def test_a_capable_kit_is_admitted_despite_its_declared_version(tmp_path: Path) -> None:
    repo, record = _repository(tmp_path, "0.1.0", legacy=False)

    noted = _run(
        repo, "-m", "basicly.cli", "tracker", "write", "--", "comments", "add", record, "Kept"
    )

    assert noted.returncode == 0, noted.stdout + noted.stderr


@pytest.mark.parametrize("replacement", ["admission = None", "def admission(): return None"])
def test_an_invalid_vault_capability_never_selects_the_legacy_write_path(
    tmp_path: Path, replacement: str
) -> None:
    repo, record = _repository(tmp_path, __version__, legacy=False)
    source = repo / KIT / "vault_write.py"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n" + replacement + "\n", encoding="utf-8"
    )
    (repo / ".basicly-tracker.toml").write_text(
        'vault = "file:///unreachable-generation-vault.git"\nprefix = "consumer"\n',
        encoding="utf-8",
    )
    before = _logs(repo)

    refused = _run(
        repo, "-m", "basicly.cli", "tracker", "write", "--", "comments", "add", record, "Refused"
    )

    assert refused.returncode == 1
    assert "older" in refused.stderr and "basicly install" in refused.stderr
    assert _logs(repo) == before
