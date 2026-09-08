"""The event log merges by union: two branches that each append keep both events.

The tracker kit exists so that a backlog two people or two agents touch in parallel does not
conflict in git the way an edited-in-place record does. Append-only is the design that earns
that, and every reader was written for a union merge — content-derived ids fold a duplicated
hunk once, the fold is order-independent — while git was never told to perform one. With
``events-*.jsonl -text`` alone, two branches that each append one event both change the
log's last hunk and the default driver reports ``CONFLICT (content)``; the control in this
module is that history, and the finding is the same history merging clean under
``merge=union`` (basicly-aabirfj).

Every assertion runs git. The hosts come from ``kit_deployment_helpers`` and carry this
repo's own ``.gitattributes``, so the rule under test is the one the repository ships, and
the negative controls are made by replacing that exact line.
"""

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

# The rule this repository carried before the union was declared: the negative control.
TEXT_ONLY_RULE = "events-*.jsonl -text"


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """The hermetic git environment every call in this module runs under."""
    return git_env(tmp_path)


@pytest.fixture
def host(tmp_path: Path, env: dict[str, str]) -> Path:
    """A consumer-shaped host repository with the kit and this repo's rule files."""
    return make_host(tmp_path / "host", env)


def _append(ledger: Path, record: str) -> None:
    """Append one ``created`` event through the kit, the way a lane's tracker write does."""
    events.append(
        ledger,
        [events.Draft(record, events.KIND_CREATED, {"title": f"record {record}"})],
        actor="test",
        clock=lambda: CLOCK,
    )


def _two_appends(host: Path, env: dict[str, str]) -> Path:
    """Build the history: a base log, then ``agent-a`` and ``agent-b`` each append one event.

    Both branches fork from the same base commit, so the two appends land on the same last
    hunk of the same file — the shape a parallel tracker write produces.
    """
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
    """Merge ``other`` into ``onto``, never raising: the exit code is the measurement."""
    git(host, env, "checkout", "-q", onto)
    return subprocess.run(
        ["git", "-C", str(host), "merge", "-q", "--no-edit", other],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _folded(ledger: Path) -> dict:
    """Fold the log as the kit reads it, asserting nothing was quarantined on the way."""
    found, quarantined = events.read_events(ledger)
    assert quarantined == []
    return events.fold(found).records


def _log_lines(ledger: Path) -> list[str]:
    return (ledger / events.INITIAL_LOG_NAME).read_text(encoding="utf-8").splitlines()


# --- the merge (third and fourth acceptance criteria) ---------------------------


def test_two_branches_that_each_append_merge_clean_and_both_events_survive(
    host: Path, env: dict[str, str]
) -> None:
    """The kit's central promise, measured: a parallel append is not a conflict."""
    ledger = _two_appends(host, env)

    merged = _merge(host, env, "agent-a", "agent-b")

    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert set(_folded(ledger)) == {"basicly-base", "basicly-a", "basicly-b"}
    assert len(_log_lines(ledger)) == 3


def test_the_fold_is_the_same_whichever_branch_merged_first(
    host: Path, env: dict[str, str]
) -> None:
    """Union concatenates in side-order; that order is not a fact about the backlog.

    The two merge directions are asserted to produce *different* files first, so the equal
    folds are a finding about the fold and not about two identical inputs.
    """
    ledger = _two_appends(host, env)
    # The second direction must start from the pre-merge tips, or it fast-forwards onto the
    # first merge and compares a file with itself.
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
    """``merge.py`` rebases a lane onto its base before the ``--no-ff`` merge; same driver."""
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
    """The control: the rule this repository shipped before, on the identical history."""
    attributes = host / ".gitattributes"
    drop_lines(attributes, LOG_RULE)
    with attributes.open("a", encoding="utf-8") as handle:
        handle.write(f"{TEXT_ONLY_RULE}\n")
    _two_appends(host, env)

    merged = _merge(host, env, "agent-a", "agent-b")

    assert merged.returncode != 0
    assert "CONFLICT (content)" in merged.stdout + merged.stderr


# --- the glob stays narrow (second acceptance criterion) ------------------------


def test_the_union_does_not_reach_the_derived_files(host: Path) -> None:
    """``snapshot.jsonl`` and a checkpoint are rewritten, not appended; union would corrupt them."""
    ledger = LEDGER_RELATIVE.as_posix()

    assert gate.attribute(host, f"{ledger}/events-0001.jsonl", "merge") == "union"
    assert gate.attribute(host, f"{ledger}/events-2026q1.jsonl", "merge") == "union"
    assert gate.attribute(host, f"{ledger}/snapshot.jsonl", "merge") == "unspecified"
    assert gate.attribute(host, f"{ledger}/checkpoint-0001.jsonl", "merge") == "unspecified"


# --- the gate (fifth acceptance criterion) -------------------------------------


def test_the_gate_names_the_union_attribute_the_host_lacks(host: Path, env: dict[str, str]) -> None:
    """A host carrying ``-text`` alone fails on the merge attribute, and only on that."""
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
    """A union rule wide enough to reach a derived file is a finding, not a stricter host."""
    with (host / ".gitattributes").open("a", encoding="utf-8") as handle:
        handle.write("snapshot.jsonl merge=union\n")

    completed = run_gate(host, env)

    assert completed.returncode == 1
    assert "merge: union on a derived file" in completed.stderr
    assert (LEDGER_RELATIVE / "snapshot.jsonl").as_posix() in completed.stderr
