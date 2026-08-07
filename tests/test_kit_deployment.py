"""Tests for the tracker kit's deployment requirements and the gate that enforces them.

The kit states two requirements on its *host* repository and can satisfy neither itself
(basicly-vkh0.21): ``events-*.jsonl`` declared ``-text``, and the ledger's ignore rules
covering ``snapshot.DERIVED_PATTERNS``. Both were prose in a docstring, which is the one
place a gate cannot read.

Every assertion here is made by **running git**, never by reading ``.gitattributes`` or
``.gitignore`` as text. Matching globs against those files by hand would reimplement git's
precedence — later lines win, a negation may re-include, a nested ignore file may override
— and would be wrong in exactly the cases the rules exist for. So the log requirement is
checked by committing a real log and cloning it under ``core.autocrlf=true``, and the
derived-file requirement by asking ``git status`` about a ledger the kit actually wrote.

**What the measurement said, stated because it is not what the docstring implies.**
``events.py`` says a normalising checkout "rewrites the ledger in place" without the
``-text`` rule. Measured on git 2.43.0, that is true of a host whose only rule is
``* text=auto`` — an LF-only log comes back CRLF. It was *already* false of this repo,
whose ``* text=auto eol=lf`` pinned the working-tree ending before this bead existed. So
the rule is not repairing live corruption here. It makes byte-exactness a property of the
log's own rule rather than a side effect of a repo-wide ``eol`` setting somebody may
change, and it is what carries the requirement to a consumer whose ``*`` rule is bare.
``test_the_text_rule_is_what_survives_a_host_without_a_repo_wide_eol_rule`` is where that
distinction is asserted rather than argued.

Every host is built from **this repo's own rule files**, copied, and the negative controls
are made by deleting the exact line under test. ``_drop_lines`` fails when the line it was
asked to remove is not there, so deleting a rule from ``.gitattributes`` or ``.gitignore``
breaks these tests at the fixture rather than quietly leaving them asserting nothing.

The gate is driven as a subprocess throughout. It loads the host's kit by path, and
``snapshot.py`` caches ``events`` under a fixed ``sys.modules`` name — so a test that
edits a kit constant would be served the previous test's copy in-process, and the drift
test is precisely the one that must not be.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from basicly import tracker_surface

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "kit_deployment.py"
KIT_RELATIVE = Path(".basicly") / "core" / "kit" / "tracker"
LEDGER_RELATIVE = Path(".basicly") / "ledger"

TEXT_RULE = "events-*.jsonl -text"
SNAPSHOT_RULE = ".basicly/ledger/snapshot.jsonl"
CHECKPOINT_RULE = ".basicly/ledger/checkpoint-*.jsonl"

# Injected rather than read, per this repo's platform-hermetic rule: the kit's only wall
# clock is this argument, and a ledger written from the host's clock is a different file
# on every run.
CLOCK = 1_000_000_000.0


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "kit_deployment")
snapshot = _load(REPO_ROOT / KIT_RELATIVE / "snapshot.py", "kit_deployment_test_snapshot")
events = snapshot.events


# --- git, made a property of the test rather than of the machine ---------------


def _git_env(tmp_path: Path) -> dict[str, str]:
    """An environment where git reads no global or system config.

    A developer's ``core.autocrlf`` or ``core.excludesFile`` would otherwise be an input:
    a global exclude file could make a negative control pass, which is the direction that
    turns a broken gate green. Both variables point at a file that is never created —
    git tolerates a missing config path, and this is portable in a way ``os.devnull`` is
    not.
    """
    absent = str(tmp_path / "no-such-gitconfig")
    return {**os.environ, "GIT_CONFIG_GLOBAL": absent, "GIT_CONFIG_SYSTEM": absent}


def _git(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in ``repo``, raising on failure so a broken fixture is never a silent pass."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def _init(root: Path, env: dict[str, str]) -> None:
    """Make ``root`` a git repository able to commit without touching the host's identity."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True, env=env
    )
    _git(root, env, "config", "user.email", "test@example.invalid")
    _git(root, env, "config", "user.name", "kit deployment test")
    _git(root, env, "config", "commit.gpgsign", "false")
    _git(root, env, "config", "core.autocrlf", "false")


def _drop_lines(path: Path, *lines: str) -> None:
    """Remove exact lines from a rules file, failing when one is not there to remove.

    The failure is the point: this is how a deleted rule in the real ``.gitattributes`` or
    ``.gitignore`` breaks the negative controls loudly instead of leaving them asserting
    nothing about a host that never had the rule.
    """
    wanted = {line.strip() for line in lines}
    kept = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() in wanted:
            seen.add(line.strip())
        else:
            kept.append(line)
    missing = sorted(wanted - seen)
    assert not missing, f"{path.name} does not carry {missing} — nothing to remove"
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


# --- a host repository, shaped the way a consumer's is -------------------------


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """The hermetic git environment every call in this module runs under."""
    return _git_env(tmp_path)


@pytest.fixture
def host(tmp_path: Path, env: dict[str, str]) -> Path:
    """A git repository with the kit installed and this repo's own rule files.

    The kit is copied rather than referenced: ``basicly install`` puts it inside the repo
    it manages, so a host that reached back into this checkout would be an arrangement no
    consumer has.
    """
    root = tmp_path / "host"
    shutil.copytree(REPO_ROOT / KIT_RELATIVE, root / KIT_RELATIVE)
    for name in (".gitattributes", ".gitignore"):
        shutil.copy2(REPO_ROOT / name, root / name)
    _init(root, env)
    return root


def _write_ledger(directory: Path) -> None:
    """Write a real ledger: two logs across a rotation, a checkpoint, and a snapshot.

    Driven through the kit's own API rather than by writing files with the expected names,
    so the derived set under test is whatever the kit really produces.
    """
    events.append(
        directory,
        [events.Draft("basicly-aa11", events.KIND_CREATED, {"title": "a record"})],
        actor="test",
        clock=lambda: CLOCK,
    )
    snapshot.rotate(directory, "2026")
    events.append(
        directory,
        [events.Draft("basicly-bb22", events.KIND_CREATED, {"title": "another record"})],
        actor="test",
        clock=lambda: CLOCK,
    )
    snapshot.rebuild(directory)


def _run_gate(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the gate against ``repo``, never raising: a non-zero exit is the answer."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# --- the log's bytes (first acceptance criterion) ------------------------------


def test_a_normalising_checkout_leaves_a_real_log_byte_identical(
    tmp_path: Path, host: Path, env: dict[str, str]
) -> None:
    """A log written by the kit survives an ``autocrlf`` checkout of this repo's rules.

    The round trip is the assertion: commit the ledger, clone it with the host normalising
    line endings, and compare bytes. Both logs are compared, so a rule that only reached
    the initial name would fail on the rotated one.
    """
    _write_ledger(host / LEDGER_RELATIVE)
    _git(host, env, "add", "-A")
    _git(host, env, "commit", "-qm", "a ledger")

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
    """The ``-text`` rule alone preserves the log on a host whose only rule normalises.

    Two hosts, one log, one difference. ``* text=auto`` is the minimal normalising host and
    what a consumer that installed the kit without this repo's ``eol=lf`` has; it returns
    the log CRLF. The same host carrying ``events-*.jsonl -text`` returns it unchanged.

    This is the control this repo's own ``.gitattributes`` cannot provide: its ``eol=lf``
    already pinned the working-tree ending, so removing ``-text`` from it changes nothing,
    and a test built that way would assert a rule that was doing no work.
    """
    written = {}
    for name, attributes in (
        ("bare", "* text=auto\n"),
        ("ruled", f"* text=auto\n{TEXT_RULE}\n"),
    ):
        source = tmp_path / name
        _init(source, env)
        (source / ".gitattributes").write_text(attributes, encoding="utf-8")
        (source / "events-0001.jsonl").write_bytes(b'{"a":1}\n{"b":2}\n')
        _git(source, env, "add", "-A")
        _git(source, env, "commit", "-qm", "a log")
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


# --- the derived files (second acceptance criterion) ---------------------------


def _ledger_status(repo: Path, env: dict[str, str]) -> set[str]:
    """Every ledger path git offers as untracked, read from ``git status``."""
    listing = _git(repo, env, "status", "--porcelain", "--untracked-files=all").stdout
    prefix = LEDGER_RELATIVE.as_posix() + "/"
    return {line[3:] for line in listing.splitlines() if line[3:].startswith(prefix)}


def _ledger_staged(repo: Path, env: dict[str, str]) -> set[str]:
    """Every ledger path that reaches the index when everything stageable is staged."""
    _git(repo, env, "add", "-A")
    staged = _git(repo, env, "diff", "--cached", "--name-only").stdout
    prefix = LEDGER_RELATIVE.as_posix() + "/"
    return {line for line in staged.splitlines() if line.startswith(prefix)}


def test_git_offers_neither_derived_file_from_a_real_ledger(
    host: Path, env: dict[str, str]
) -> None:
    """Neither derived file is untracked-and-offerable, and neither can be staged.

    The logs are the control in the same assertion: an ignore rule wide enough to swallow
    the truth would pass a "the snapshot is absent" check on its own, and this repo's kit
    documents that failure — deleting the truth to save a cache.
    """
    ledger = host / LEDGER_RELATIVE
    _write_ledger(ledger)
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
    """The control: the same ledger in the same repo, with the two rules removed."""
    _drop_lines(host / ".gitignore", SNAPSHOT_RULE, CHECKPOINT_RULE)
    ledger = host / LEDGER_RELATIVE
    _write_ledger(ledger)

    offered = _ledger_status(host, env)
    staged = _ledger_staged(host, env)
    prefix = LEDGER_RELATIVE.as_posix() + "/"
    for name in ("snapshot.jsonl", "checkpoint-0001.jsonl"):
        assert prefix + name in offered
        assert prefix + name in staged


# --- the gate (third acceptance criterion) -------------------------------------


def test_the_gate_passes_on_this_repository(tmp_path: Path) -> None:
    """This repository satisfies both requirements, checked the way a consumer would.

    No ``--repo``, so the default is exercised too: a gate that only answered for a path
    it was handed would pass every test above and be unwired in ``basicly.toml``.
    """
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=_git_env(tmp_path),
    )
    assert completed.returncode == 0, completed.stderr
    assert LEDGER_RELATIVE.as_posix() in completed.stdout


def test_the_gate_names_the_text_rule_the_host_lacks(host: Path, env: dict[str, str]) -> None:
    """A host without the ``-text`` declaration fails, and is told the rule and the file."""
    assert _run_gate(host, env).returncode == 0

    _drop_lines(host / ".gitattributes", TEXT_RULE)
    completed = _run_gate(host, env)

    assert completed.returncode == 1
    assert TEXT_RULE in completed.stderr
    assert ".gitattributes" in completed.stderr


def test_the_gate_names_both_ignore_rules_the_host_lacks(host: Path, env: dict[str, str]) -> None:
    """A host without the derived-file rules fails, naming each pattern separately."""
    _drop_lines(host / ".gitignore", SNAPSHOT_RULE, CHECKPOINT_RULE)
    completed = _run_gate(host, env)

    assert completed.returncode == 1
    assert SNAPSHOT_RULE in completed.stderr
    assert CHECKPOINT_RULE in completed.stderr
    assert ".gitignore" in completed.stderr


def test_a_rule_naming_only_the_initial_log_does_not_satisfy_the_gate(
    host: Path, env: dict[str, str]
) -> None:
    """The obvious literal — the initial log's own name — is caught as the partial rule it is.

    ``events-0001.jsonl`` is the file a host actually has when someone reaches for a
    concrete name, and a gate checking one sample would accept it and then be wrong from
    the first rotation. This is why ``GLOB_FILLS`` carries two entries.
    """
    attributes = host / ".gitattributes"
    _drop_lines(attributes, TEXT_RULE)
    with attributes.open("a", encoding="utf-8") as handle:
        handle.write(f"{events.INITIAL_LOG_NAME} -text\n")

    completed = _run_gate(host, env)

    assert completed.returncode == 1
    assert events.INITIAL_LOG_NAME not in completed.stderr
    assert "events-2026q1.jsonl" in completed.stderr


def test_the_gate_says_uncommit_when_a_derived_file_is_already_tracked(
    host: Path, env: dict[str, str]
) -> None:
    """An ignore rule does not un-commit a file, and the gate does not pretend it does.

    The remedy has to differ from the missing-rule one, or a host that already committed a
    snapshot is told to add a rule it has and the message is a dead end.
    """
    ledger = host / LEDGER_RELATIVE
    _write_ledger(ledger)
    _git(host, env, "add", "-f", (LEDGER_RELATIVE / "snapshot.jsonl").as_posix())
    _git(host, env, "commit", "-qm", "a derived file that should not be here")

    completed = _run_gate(host, env)

    assert completed.returncode == 1
    assert "already in the index" in completed.stderr
    assert f"git rm --cached {(LEDGER_RELATIVE / 'snapshot.jsonl').as_posix()}" in completed.stderr


def test_the_gate_reads_the_kits_constants_rather_than_a_second_spelling(
    host: Path, env: dict[str, str]
) -> None:
    """Move the kit's two constants and the gate demands the moved rules, not the old ones.

    This is the acceptance criterion's "derived from or checked against ``LOG_GLOB`` rather
    than spelled a second time". A gate carrying its own copy of ``events-*.jsonl`` would
    keep passing this host — the rules for the old names are still in place — which is
    exactly the drift ``events.py`` documents as the defect this design keeps paying for.
    """
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

    completed = _run_gate(host, env)

    assert completed.returncode == 1
    assert "ledger-*.jsonl -text" in completed.stderr
    assert ".basicly/ledger/fold-*.jsonl" in completed.stderr
    assert TEXT_RULE not in completed.stderr
    assert CHECKPOINT_RULE not in completed.stderr


def test_the_gate_fails_when_the_host_has_no_kit(tmp_path: Path, env: dict[str, str]) -> None:
    """No kit is a failure, never a vacuous pass — the fail-open shape this repo distrusts."""
    root = tmp_path / "kitless"
    _init(root, env)

    completed = _run_gate(root, env)

    assert completed.returncode == 1
    assert KIT_RELATIVE.as_posix() in completed.stderr


# --- the wiring, and the one path that is a literal ----------------------------


def test_the_gate_is_declared_as_a_verify_check() -> None:
    """The gate is wired to something that runs it, not merely committed.

    An instrument built and never connected is this repo's named defect class, and it is
    the reason the kit's requirements went unenforced in the first place.
    """
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in config["verify"]["checks"]}

    assert "kit-deployment" in checks
    entry = checks["kit-deployment"]
    assert SCRIPT.relative_to(REPO_ROOT).as_posix() in entry["command"]
    # A bare `python` on windows-latest is a system interpreter, not the project's.
    assert entry["command"][:3] == ["uv", "run", "python"]
    assert set(entry["modes"]) == {"fast", "full"}


def test_the_default_ledger_is_the_directory_this_repo_actually_uses() -> None:
    """The gate's one host-layout literal is tied to the committed artifact beside it.

    The kit names no path — it takes its directory as an argument — so ``LEDGER_DIR`` is a
    fact about this repo, and ``tracker_surface.INVENTORY_FILE`` is the other file in the
    same directory. Moving one without the other is what this catches.
    """
    assert tracker_surface.INVENTORY_FILE.parent == gate.LEDGER_DIR
    assert gate.KIT_DIR == KIT_RELATIVE


def test_samples_covers_a_pattern_with_and_without_a_wildcard() -> None:
    """A literal pattern is its own sample; a glob yields one name per fill."""
    assert gate.samples("snapshot.jsonl") == ("snapshot.jsonl",)
    assert gate.samples("events-*.jsonl") == ("events-0001.jsonl", "events-2026q1.jsonl")
    assert len(gate.GLOB_FILLS) > 1
