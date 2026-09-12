from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from basicly import tracker_paths
from tests.kit_deployment_helpers import (
    CHECKPOINT_RULE,
    KIT_RELATIVE,
    LEDGER_RELATIVE,
    LOG_RULE,
    REPO_ROOT,
    SCRIPT,
    SNAPSHOT_RULE,
    drop_lines,
    events,
    gate,
    git,
    git_env,
    init,
    make_host,
    run_gate,
    snapshot,
    write_ledger,
)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return git_env(tmp_path)


@pytest.fixture
def host(tmp_path: Path, env: dict[str, str]) -> Path:
    return make_host(tmp_path / "host", env)


def test_a_normalising_checkout_leaves_a_real_log_byte_identical(
    tmp_path: Path, host: Path, env: dict[str, str]
) -> None:

    write_ledger(host / LEDGER_RELATIVE)
    git(host, env, "add", "-A")
    git(host, env, "commit", "-qm", "a ledger")

    work = tmp_path / "work"
    subprocess.run(
        ["git", "-c", "core.autocrlf=true", "clone", "-q", str(host), str(work)],
        check=True,
        capture_output=True,
        env=env,
    )

    logs = events.log_paths(host / LEDGER_RELATIVE)
    assert [path.name for path in logs] == ["events-0001.jsonl", "events-2026.jsonl"]
    for log in logs:
        checked_out = work / LEDGER_RELATIVE / log.name
        assert checked_out.read_bytes() == log.read_bytes(), f"{log.name} was rewritten"


def test_the_text_rule_is_what_survives_a_host_without_a_repo_wide_eol_rule(
    tmp_path: Path, env: dict[str, str]
) -> None:

    written = {}
    for name, attributes in (
        ("bare", "* text=auto\n"),
        ("ruled", f"* text=auto\n{LOG_RULE}\n"),
    ):
        source = tmp_path / name
        init(source, env)
        (source / ".gitattributes").write_text(attributes, encoding="utf-8")
        (source / "events-0001.jsonl").write_bytes(b'{"a":1}\n{"b":2}\n')
        git(source, env, "add", "-A")
        git(source, env, "commit", "-qm", "a log")
        work = tmp_path / f"{name}-work"
        subprocess.run(
            ["git", "-c", "core.autocrlf=true", "clone", "-q", str(source), str(work)],
            check=True,
            capture_output=True,
            env=env,
        )
        written[name] = (work / "events-0001.jsonl").read_bytes()

    assert written["bare"] == b'{"a":1}\r\n{"b":2}\r\n'
    assert written["ruled"] == b'{"a":1}\n{"b":2}\n'


def _ledger_status(repo: Path, env: dict[str, str]) -> set[str]:
    listing = git(repo, env, "status", "--porcelain", "--untracked-files=all").stdout
    prefix = LEDGER_RELATIVE.as_posix() + "/"
    return {line[3:] for line in listing.splitlines() if line[3:].startswith(prefix)}


def _ledger_staged(repo: Path, env: dict[str, str]) -> set[str]:
    git(repo, env, "add", "-A")
    staged = git(repo, env, "diff", "--cached", "--name-only").stdout
    prefix = LEDGER_RELATIVE.as_posix() + "/"
    return {line for line in staged.splitlines() if line.startswith(prefix)}


def test_git_offers_neither_derived_file_from_a_real_ledger(
    host: Path, env: dict[str, str]
) -> None:

    ledger = host / LEDGER_RELATIVE
    write_ledger(ledger)
    derived = {path.name for path in snapshot.derived_paths(ledger)}
    assert derived == {"snapshot.jsonl", "checkpoint-0001.jsonl"}

    offered = _ledger_status(host, env)
    staged = _ledger_staged(host, env)
    prefix = LEDGER_RELATIVE.as_posix() + "/"
    for name in derived:
        assert prefix + name not in offered
        assert prefix + name not in staged
    for log in events.log_paths(ledger):
        assert prefix + log.name in offered
        assert prefix + log.name in staged


def test_without_the_ignore_rules_git_offers_both_derived_files(
    host: Path, env: dict[str, str]
) -> None:
    drop_lines(host / ".gitignore", SNAPSHOT_RULE, CHECKPOINT_RULE)
    ledger = host / LEDGER_RELATIVE
    write_ledger(ledger)

    offered = _ledger_status(host, env)
    staged = _ledger_staged(host, env)
    prefix = LEDGER_RELATIVE.as_posix() + "/"
    for name in ("snapshot.jsonl", "checkpoint-0001.jsonl"):
        assert prefix + name in offered
        assert prefix + name in staged


def test_the_gate_passes_on_this_repository(tmp_path: Path) -> None:

    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=git_env(tmp_path),
    )
    assert completed.returncode == 0, completed.stderr
    assert LEDGER_RELATIVE.as_posix() in completed.stdout


def test_the_gate_names_the_text_rule_the_host_lacks(host: Path, env: dict[str, str]) -> None:
    assert run_gate(host, env).returncode == 0

    drop_lines(host / ".gitattributes", LOG_RULE)
    completed = run_gate(host, env)

    assert completed.returncode == 1
    assert LOG_RULE in completed.stderr
    assert ".gitattributes" in completed.stderr


def test_the_gate_names_both_ignore_rules_the_host_lacks(host: Path, env: dict[str, str]) -> None:
    drop_lines(host / ".gitignore", SNAPSHOT_RULE, CHECKPOINT_RULE)
    completed = run_gate(host, env)

    assert completed.returncode == 1
    assert SNAPSHOT_RULE in completed.stderr
    assert CHECKPOINT_RULE in completed.stderr
    assert ".gitignore" in completed.stderr


def test_a_rule_naming_only_the_initial_log_does_not_satisfy_the_gate(
    host: Path, env: dict[str, str]
) -> None:

    attributes = host / ".gitattributes"
    drop_lines(attributes, LOG_RULE)
    with attributes.open("a", encoding="utf-8") as handle:
        handle.write(f"{events.INITIAL_LOG_NAME} -text merge=union\n")

    completed = run_gate(host, env)

    assert completed.returncode == 1
    assert events.INITIAL_LOG_NAME not in completed.stderr
    assert "events-2026q1.jsonl" in completed.stderr


def test_the_gate_says_uncommit_when_a_derived_file_is_already_tracked(
    host: Path, env: dict[str, str]
) -> None:

    ledger = host / LEDGER_RELATIVE
    write_ledger(ledger)
    git(host, env, "add", "-f", (LEDGER_RELATIVE / "snapshot.jsonl").as_posix())
    git(host, env, "commit", "-qm", "a derived file that should not be here")

    completed = run_gate(host, env)

    assert completed.returncode == 1
    assert "already in the index" in completed.stderr
    assert f"git rm --cached {(LEDGER_RELATIVE / 'snapshot.jsonl').as_posix()}" in completed.stderr


def test_the_gate_reads_the_kits_constants_rather_than_a_second_spelling(
    host: Path, env: dict[str, str]
) -> None:

    kit = host / KIT_RELATIVE
    log_source = kit / "events.py"
    log_source.write_text(
        log_source.read_text(encoding="utf-8").replace(
            'LOG_GLOB = "events-*.jsonl"', 'LOG_GLOB = "ledger-*.jsonl"'
        ),
        encoding="utf-8",
    )
    derived_source = kit / "snapshot.py"
    derived_source.write_text(
        derived_source.read_text(encoding="utf-8").replace(
            'CHECKPOINT_PREFIX = "checkpoint-"', 'CHECKPOINT_PREFIX = "fold-"'
        ),
        encoding="utf-8",
    )

    completed = run_gate(host, env)

    assert completed.returncode == 1
    assert "ledger-*.jsonl -text merge=union" in completed.stderr
    assert ".basicly/ledger/fold-*.jsonl" in completed.stderr
    assert LOG_RULE not in completed.stderr
    assert CHECKPOINT_RULE not in completed.stderr


def test_the_gate_fails_when_the_host_has_no_kit(tmp_path: Path, env: dict[str, str]) -> None:
    root = tmp_path / "kitless"
    init(root, env)

    completed = run_gate(root, env)

    assert completed.returncode == 1
    assert KIT_RELATIVE.as_posix() in completed.stderr


def test_the_gate_is_declared_as_a_verify_check() -> None:

    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in config["verify"]["checks"]}

    assert "kit-deployment" in checks
    entry = checks["kit-deployment"]
    assert SCRIPT.relative_to(REPO_ROOT).as_posix() in entry["command"]
    assert entry["command"][:3] == ["uv", "run", "python"]
    assert set(entry["modes"]) == {"fast", "full"}


def test_the_default_ledger_is_the_directory_this_repo_actually_uses() -> None:

    assert tracker_paths.LEDGER_DIR_NAME == gate.LEDGER_DIR
    assert gate.KIT_DIR == KIT_RELATIVE


def test_samples_covers_a_pattern_with_and_without_a_wildcard() -> None:
    assert gate.samples("snapshot.jsonl") == ("snapshot.jsonl",)
    assert gate.samples("events-*.jsonl") == ("events-0001.jsonl", "events-2026q1.jsonl")
    assert len(gate.GLOB_FILLS) > 1
