from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.kit_deployment_helpers import (
    CLOCK,
    LEDGER_RELATIVE,
    LOG_RULE,
    drop_lines,
    events,
    gate,
    git,
    git_env,
    make_host,
    run_gate,
)

TEXT_ONLY_RULE = "events-*.jsonl -text"


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return git_env(tmp_path)


@pytest.fixture
def host(tmp_path: Path, env: dict[str, str]) -> Path:
    return make_host(tmp_path / "host", env)


def _append(ledger: Path, record: str) -> None:
    events.append(
        ledger,
        [events.Draft(record, events.KIND_CREATED, {"title": f"record {record}"})],
        actor="test",
        clock=lambda: CLOCK,
    )


def _two_appends(host: Path, env: dict[str, str]) -> Path:

    ledger = host / LEDGER_RELATIVE
    _append(ledger, "basicly-base")
    git(host, env, "add", "-A")
    git(host, env, "commit", "-qm", "base")
    for agent in ("a", "b"):
        git(host, env, "checkout", "-qb", f"agent-{agent}", "main")
        _append(ledger, f"basicly-{agent}")
        git(host, env, "add", "-A")
        git(host, env, "commit", "-qm", f"agent {agent} appends")
    return ledger


def _merge(host: Path, env: dict[str, str], onto: str, other: str) -> subprocess.CompletedProcess:
    git(host, env, "checkout", "-q", onto)
    return subprocess.run(
        ["git", "-C", str(host), "merge", "-q", "--no-edit", other],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _folded(ledger: Path) -> dict:
    found, quarantined = events.read_events(ledger)
    assert quarantined == []
    return events.fold(found).records


def _log_lines(ledger: Path) -> list[str]:
    return (ledger / events.INITIAL_LOG_NAME).read_text(encoding="utf-8").splitlines()


def test_two_branches_that_each_append_merge_clean_and_both_events_survive(
    host: Path, env: dict[str, str]
) -> None:
    ledger = _two_appends(host, env)

    merged = _merge(host, env, "agent-a", "agent-b")

    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert set(_folded(ledger)) == {"basicly-base", "basicly-a", "basicly-b"}
    assert len(_log_lines(ledger)) == 3


def test_the_fold_is_the_same_whichever_branch_merged_first(
    host: Path, env: dict[str, str]
) -> None:

    ledger = _two_appends(host, env)
    git(host, env, "branch", "mirror-a", "agent-a")
    git(host, env, "branch", "mirror-b", "agent-b")

    assert _merge(host, env, "agent-a", "agent-b").returncode == 0
    a_first = _log_lines(ledger)
    a_fold = _folded(ledger)
    assert _merge(host, env, "mirror-b", "mirror-a").returncode == 0
    b_first = _log_lines(ledger)
    b_fold = _folded(ledger)

    assert a_first != b_first
    assert sorted(a_first) == sorted(b_first)
    assert a_fold == b_fold


def test_the_landing_rebase_keeps_both_events_too(host: Path, env: dict[str, str]) -> None:
    ledger = _two_appends(host, env)

    git(host, env, "checkout", "-q", "agent-b")
    rebased = subprocess.run(
        ["git", "-C", str(host), "rebase", "-q", "agent-a"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert rebased.returncode == 0, rebased.stdout + rebased.stderr
    assert set(_folded(ledger)) == {"basicly-base", "basicly-a", "basicly-b"}


def test_the_same_history_conflicts_when_the_union_attribute_is_removed(
    host: Path, env: dict[str, str]
) -> None:
    attributes = host / ".gitattributes"
    drop_lines(attributes, LOG_RULE)
    with attributes.open("a", encoding="utf-8") as handle:
        handle.write(f"{TEXT_ONLY_RULE}\n")
    _two_appends(host, env)

    merged = _merge(host, env, "agent-a", "agent-b")

    assert merged.returncode != 0
    assert "CONFLICT (content)" in merged.stdout + merged.stderr


def test_the_union_does_not_reach_the_derived_files(host: Path) -> None:
    ledger = LEDGER_RELATIVE.as_posix()

    assert gate.attribute(host, f"{ledger}/events-0001.jsonl", "merge") == "union"
    assert gate.attribute(host, f"{ledger}/events-2026q1.jsonl", "merge") == "union"
    assert gate.attribute(host, f"{ledger}/snapshot.jsonl", "merge") == "unspecified"
    assert gate.attribute(host, f"{ledger}/checkpoint-0001.jsonl", "merge") == "unspecified"


def test_the_gate_names_the_union_attribute_the_host_lacks(host: Path, env: dict[str, str]) -> None:
    attributes = host / ".gitattributes"
    drop_lines(attributes, LOG_RULE)
    with attributes.open("a", encoding="utf-8") as handle:
        handle.write(f"{TEXT_ONLY_RULE}\n")

    completed = run_gate(host, env)

    assert completed.returncode == 1
    assert "git reports merge: unspecified" in completed.stderr
    assert "git reports text:" not in completed.stderr
    assert LOG_RULE in completed.stderr


def test_the_gate_refuses_a_union_declared_on_a_derived_file(
    host: Path, env: dict[str, str]
) -> None:
    with (host / ".gitattributes").open("a", encoding="utf-8") as handle:
        handle.write("snapshot.jsonl merge=union\n")

    completed = run_gate(host, env)

    assert completed.returncode == 1
    assert "merge: union on a derived file" in completed.stderr
    assert (LEDGER_RELATIVE / "snapshot.jsonl").as_posix() in completed.stderr
