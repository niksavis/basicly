"""The dual write and the flip at the br seam (basicly-vkh0.19).

Steps 3 and 4 of the cutover in ``docs/design/work-tracker.md`` §5, and the three
things this bead has to show:

- **Every write reaches both stores, and a divergence fails the command.** Asserted by
  driving the whole measured write surface through :func:`basicly.br.run_br` against a
  stand-in br and reading the owned ledger back — not by asserting that a mirror
  function was called. The refusals get their own tests, because "fails rather than
  logs" is the half that is easy to satisfy vacuously.
- **The flip is confined to the seam, and `read_record`'s one absence contract survives
  it.** Every absence case is run against *both* stores through one parametrisation, so
  the claim "exactly as it does against the external binary" is a comparison rather
  than a description. :func:`test_no_module_outside_the_seam_reads_the_owned_store` is
  the tree guard for the "confined" half.
- **The shadow differential comes back clean AND conclusive.** With
  :func:`test_the_gate_query_discriminates_nothing_without_the_dual_write` as its
  control: the same population minus the ``gate report`` calls reproduces exactly what
  `basicly-vkh0.18` reported against the live tracker — clean, and inconclusive on the
  one query no import could ever have filled.

The stand-in br is the *reference* store, and it is genuinely independent of the
ledger: it holds its own records, and the differential's perturbation probe is what
proves that rather than this docstring. Nothing here spawns a process, sleeps, or reads
the host's tracker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from basicly import br, config, tracker_usage

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"

# The engine's own gate provider, spelled as the kit's vocabulary expects to read it
# back. A foreign provider on a required gate is disregarded, so a test that used one
# would derive `missing` on a record it had just recorded a pass for.
ENGINE_PROVIDER = "basicly-verify"
FOREIGN_PROVIDER = "some-ci"


# --- the stand-in br ----------------------------------------------------------


class _FakeBr:
    """A br stand-in that keeps its own records — the differential's reference store.

    Implements the six writes the engine makes and the one record read, in br's own
    argv and JSON shapes. It is *not* derived from the owned ledger in any way, which
    is the property `differential.audit_reference`'s probe checks and this class has to
    actually have for the run to be conclusive rather than merely clean.
    """

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.calls: list[list[str]] = []
        self._minted = 0

    def __call__(self, cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        args = list(cmd[1:])
        self.calls.append(args)
        if args[:1] == ["--version"]:
            return _proc(f"br {br.PINNED_VERSION}")
        handler = getattr(self, f"_{'_'.join(_surface_words(args))}", None)
        if handler is None:
            return _proc("", stderr=f"Error: unknown command {' '.join(args)}", returncode=2)
        return handler(args)

    # -- writes

    def _create(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self._minted += 1
        issue = f"seam-{self._minted:04d}"
        flags = _flags(args)
        # Typed the way br types them, observed on br 0.2.16: `priority` is an integer
        # and `labels` a list, while both arrive at the seam as one argv string.
        record: dict[str, Any] = {
            "id": issue,
            "title": args[1],
            "status": "open",
            "issue_type": flags.get("-t", "task"),
            "priority": int(flags.get("-p", 2)),
            "comments": [],
            "dependencies": [],
            "gates": [],
        }
        if labels := flags.get("-l"):
            record["labels"] = labels.split(",")
        if parent := flags.get("--parent"):
            record["dependencies"].append({"id": parent, "dependency_type": "parent-child"})
        self.records[issue] = record
        return _proc(json.dumps({"id": issue, "status": "open"}))

    def _update(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        record = self.records.get(args[1])
        if record is None:
            return _proc("", stderr="Error: issue not found", returncode=1)
        names = {"-t": "issue_type", "--external-ref": "external_ref"}
        for flag, value in _flags(args).items():
            record[names.get(flag, flag.lstrip("-"))] = value
        return _proc("")

    def _reopen(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """A write the engine does not currently make, and so does not mirror."""
        record = self.records.get(args[1])
        if record is None:
            return _proc("", stderr="Error: issue not found", returncode=1)
        record["status"] = "open"
        return _proc("")

    def _close(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        record = self.records.get(args[1])
        if record is None:
            return _proc("", stderr="Error: issue not found", returncode=1)
        record["status"] = "closed"
        return _proc("")

    def _comments_add(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        record = self.records.get(args[2])
        if record is None:
            return _proc("", stderr="Error: issue not found", returncode=1)
        record["comments"].append({"text": args[3]})
        return _proc("")

    def _dep_add(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        record = self.records.get(args[2])
        if record is None:
            return _proc("", stderr="Error: issue not found", returncode=1)
        record["dependencies"].append({
            "id": args[3],
            "dependency_type": _flags(args).get("-t", ""),
        })
        return _proc("")

    def _gate_report(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        flags = _flags(args)
        record = self.records.get(args[-1])
        if record is None:
            return _proc("", stderr="Error: issue not found", returncode=1)
        record["gates"].append({
            "gate": flags["--gate"],
            "provider": flags["--provider"],
            "passed": flags["--status"] == "pass",
        })
        return _proc("")

    # -- the reads

    def _blocked(self, _args: list[str]) -> subprocess.CompletedProcess[str]:
        return _proc("[]")

    def _show(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        record = self.records.get(args[1])
        if record is None:
            return _proc("", stderr="Error: issue not found", returncode=1)
        return _proc(json.dumps([{key: value for key, value in record.items() if key != "gates"}]))


def _proc(stdout: str, *, stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(["br"], returncode, stdout, stderr)


def _surface_words(args: list[str]) -> list[str]:
    """The one or two command words naming a subcommand, for the handler lookup."""
    words = [arg for arg in args if not arg.startswith("-")]
    if words and words[0] in {"comments", "dep", "gate"}:
        return words[:2]
    return words[:1]


def _flags(args: list[str]) -> dict[str, str]:
    """``{flag: value}`` for the space-separated flag pairs in *args*."""
    return {
        args[index]: args[index + 1]
        for index in range(len(args) - 1)
        if args[index].startswith("-") and not args[index + 1].startswith("-")
    }


# --- fixtures -----------------------------------------------------------------


def _repo(tmp_path: Path, mode: str) -> Path:
    """A checkout with the kit installed and ``[tracker] mode`` declared."""
    shutil.copytree(KIT_SOURCE, tmp_path / br.KIT_TRACKER_DIR, ignore=shutil.ignore_patterns("*"))
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, tmp_path / br.KIT_TRACKER_DIR / source.name)
    (tmp_path / br.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text(f'[tracker]\nmode = "{mode}"\n', encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake_br(monkeypatch: pytest.MonkeyPatch) -> _FakeBr:
    """A br on PATH, answering out of an in-process store instead of a database."""
    stand_in = _FakeBr()
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", {"/usr/bin/br"})
    monkeypatch.setattr(br.subprocess, "run", stand_in)
    return stand_in


def _ledger_events(repo: Path) -> list[Any]:
    kit = br.kit(repo)
    return kit.read_ledger(br.ledger_dir(repo))


def _kinds(repo: Path, record: str) -> list[str]:
    return [event.kind for event in _ledger_events(repo) if event.record == record]


# --- the mode declaration -----------------------------------------------------


def test_importing_config_installs_the_mode_reader() -> None:
    """The seam is put in the repo's declared mode by an import, not by a caller.

    `basicly.br` cannot import `basicly.config` — `config -> runner -> run_record -> br`
    already runs the other way — so the reader is installed from above. That inversion
    is invisible at both ends, which is exactly why it is asserted here: without it the
    seam silently answers ``external`` for a repo that declared ``owned``, and the first
    symptom would be reads coming from the wrong store.
    """
    assert br._mode_reader == [config.load_tracker_mode]


def test_a_repo_that_declares_nothing_is_external(tmp_path: Path) -> None:
    """The pre-cutover behaviour is what a consumer who never heard of this gets."""
    assert config.load_tracker_mode(tmp_path) == br.DEFAULT_TRACKER_MODE
    assert br.tracker_mode(tmp_path) == br.MODE_EXTERNAL


@pytest.mark.parametrize("mode", br.TRACKER_MODES)
def test_each_declared_rung_reaches_the_seam(tmp_path: Path, mode: str) -> None:
    """Every value the ladder has is readable end to end, not just the default."""
    (tmp_path / "basicly.toml").write_text(f'[tracker]\nmode = "{mode}"\n', encoding="utf-8")
    assert br.tracker_mode(tmp_path) == mode


def test_a_mode_outside_the_ladder_is_refused(tmp_path: Path) -> None:
    """A value the engine cannot honour is an error, never a silent default.

    The two behaviours differ in *which store answers a read*, so defaulting a
    misspelled ``owned`` back to ``external`` would leave the file stating one thing
    and the engine doing another — with no diff to review.
    """
    (tmp_path / "basicly.toml").write_text('[tracker]\nmode = "flipped"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="not one of external, dual, owned"):
        config.load_tracker_mode(tmp_path)


def test_the_ledger_is_one_per_repo_not_one_per_worktree(tmp_path: Path) -> None:
    """A lane's writes belong to the base checkout, or teardown deletes them.

    The same rule `tracker_usage.ledger_root` was given after the usage spool was
    written into worktrees and discarded at teardown (basicly-vkh0.8) — a ledger that
    did not follow the redirect would lose every write a lane made.
    """
    base = tmp_path / "base"
    (base / ".beads").mkdir(parents=True)
    worktree = tmp_path / "wt"
    (worktree / ".beads").mkdir(parents=True)
    (worktree / ".beads" / "redirect").write_text(str(base / ".beads"), encoding="utf-8")

    assert br.ledger_dir(worktree) == base / br.LEDGER_DIR
    assert br.ledger_dir(base) == base / br.LEDGER_DIR


def test_the_ledger_sits_beside_the_other_committed_ledger_artifacts() -> None:
    """One directory, taken off one constant, so a gate cannot be pointed elsewhere.

    `.scripts/kit_deployment.py` gates this directory's ignore rules and
    `.gitattributes` pins the log's bytes there; a second literal in this module could
    drift from either without anything noticing.
    """
    ledger = br.LEDGER_DIR
    assert ledger == tracker_usage.LEDGER_FILE.parent
    assert ledger == Path(".basicly") / "ledger"


# --- the dual write -----------------------------------------------------------


def test_external_mode_writes_nothing_to_the_owned_ledger(tmp_path: Path, fake_br: _FakeBr) -> None:
    """The control for every assertion below: the mirror is off until it is declared."""
    repo = _repo(tmp_path, br.MODE_EXTERNAL)
    br.run_br(repo, ["create", "a bead", "-t", "task", "-d", "body", "--json"])
    br.run_br(repo, ["comments", "add", "seam-0001", "[harness-policy] hello"])

    assert fake_br.records["seam-0001"]["comments"] == [{"text": "[harness-policy] hello"}]
    assert list((repo / br.LEDGER_DIR).glob("events-*.jsonl")) == []


def test_every_write_reaches_both_stores(tmp_path: Path, fake_br: _FakeBr) -> None:
    """The whole measured write surface, mirrored — asserted on the ledger's contents.

    One test over all six rather than six over one, because the criterion is about the
    *surface* being covered: a per-write test passes while the write nobody added a
    branch for silently does nothing.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    br.run_br(repo, ["create", "parent", "-t", "task", "-d", "body", "--json"])
    br.run_br(repo, ["create", "child", "-t", "task", "--parent", "seam-0001", "--json"])
    br.run_br(repo, ["update", "seam-0002", "--external-ref", "worktree:wt:harness/wt"])
    br.run_br(repo, ["comments", "add", "seam-0002", "[harness-policy] checkpoint=ship approved"])
    br.run_br(repo, ["dep", "add", "seam-0002", "seam-0001", "-t", "blocks"])
    br.run_br(
        repo,
        [
            "gate",
            "report",
            "--gate",
            "verify",
            "--provider",
            ENGINE_PROVIDER,
            "--status",
            "pass",
            "--note",
            "verify fast: ok",
            "seam-0002",
        ],
    )
    br.run_br(repo, ["close", "seam-0002", "--reason", "shipped by the harness loop"])

    kit = br.kit(repo)
    assert _kinds(repo, "seam-0001") == [kit.events.KIND_CREATED, kit.events.KIND_STATUS]
    assert _kinds(repo, "seam-0002") == [
        kit.events.KIND_CREATED,
        kit.events.KIND_STATUS,
        kit.migrate.KIND_EDGE,  # --parent
        kit.events.KIND_FIELD,  # --external-ref
        kit.events.KIND_COMMENT,
        kit.migrate.KIND_EDGE,  # dep add
        kit.KIND_GATE,
        kit.events.KIND_STATUS,  # close
    ]
    # ...and br still holds all of it, which is what "both stores" means.
    assert fake_br.records["seam-0002"]["status"] == "closed"
    assert fake_br.records["seam-0002"]["gates"] == [
        {"gate": "verify", "provider": ENGINE_PROVIDER, "passed": True}
    ]


@pytest.mark.usefixtures("fake_br")
def test_the_mirrored_record_matches_what_br_holds(tmp_path: Path) -> None:
    """The two stores agree field by field, read back through the seam's own reader."""
    repo = _repo(tmp_path, br.MODE_DUAL)
    br.run_br(
        repo,
        ["create", "a bead", "-t", "bug", "-p", "0", "-l", "phase-6,ready", "-d", "b", "--json"],
    )
    br.run_br(repo, ["update", "seam-0001", "--external-ref", "worktree:wt:harness/wt"])
    br.run_br(repo, ["comments", "add", "seam-0001", "[harness-policy] note"])
    br.run_br(repo, ["dep", "add", "seam-0001", "seam-0001", "-t", "blocks"])

    owned = br.owned_record(repo, "seam-0001")
    external = br.read_record(repo, "seam-0001")
    assert owned is not None and external is not None
    for key in (
        "id",
        "status",
        "issue_type",
        "external_ref",
        "comments",
        "dependencies",
        # The typed pair. A flag's value reaches the seam as one argv string, so a
        # mirror that stored it verbatim would hand `supervise` a `labels` of
        # `"phase-6,ready"` — which iterates as twelve one-character labels onto the
        # follow-up it creates, after the flip and nowhere before it.
        "priority",
        "labels",
    ):
        assert owned[key] == external[key], key
    assert owned["labels"] == ["phase-6", "ready"]
    assert owned["priority"] == 0


@pytest.mark.usefixtures("fake_br")
def test_a_read_records_nothing(tmp_path: Path) -> None:
    """A read changes neither store, so mirroring one would invent a divergence."""
    repo = _repo(tmp_path, br.MODE_DUAL)
    br.run_br(repo, ["create", "a bead", "-t", "task", "--json"])
    before = len(_ledger_events(repo))

    br.run_br(repo, ["show", "seam-0001", "--json"])
    br.run_br(repo, ["blocked", "--json"])

    assert len(_ledger_events(repo)) == before


@pytest.mark.usefixtures("fake_br")
def test_a_write_br_refused_is_not_mirrored(tmp_path: Path) -> None:
    """A rejected write changed neither store; recording it is what would diverge them."""
    repo = _repo(tmp_path, br.MODE_DUAL)
    proc = br.run_br(repo, ["close", "seam-9999", "--reason", "nope"], check=False)

    assert proc.returncode != 0
    assert _ledger_events(repo) == []


@pytest.mark.usefixtures("fake_br")
def test_a_write_with_no_translation_fails_the_command(tmp_path: Path) -> None:
    """A write outside the mirrored surface raises; it is never logged and skipped.

    The surface was frozen by measurement, so a br write nobody translated is a new
    dependency somebody took without deciding to — and the mirror is the last place it
    is still visible. ``reopen`` is `tracker_usage.WRITE_SUBCOMMANDS`' own example of a
    write the engine does not currently make.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    br.run_br(repo, ["create", "a bead", "-t", "task", "--json"])
    with pytest.raises(br.TrackerDivergenceError, match="no owned-ledger translation"):
        br.run_br(repo, ["reopen", "seam-0001"])


@pytest.mark.usefixtures("fake_br")
def test_an_update_flag_with_no_field_fails_the_command(tmp_path: Path) -> None:
    """Dropping the one field br just wrote is the divergence, so it must not be silent."""
    repo = _repo(tmp_path, br.MODE_DUAL)
    br.run_br(repo, ["create", "a bead", "-t", "task", "--json"])
    with pytest.raises(br.TrackerDivergenceError, match="--assignee"):
        br.run_br(repo, ["update", "seam-0001", "--assignee", "someone"])


@pytest.mark.usefixtures("fake_br")
def test_a_ledger_that_refuses_the_write_fails_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store's own refusal surfaces as a divergence rather than a warning.

    A lock it could not take, a line it could not append: br has already accepted the
    write, so the only honest answer is that the command failed.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    kit = br.kit(repo)

    def refuse(*_args: object, **_kwargs: object):
        raise kit.events.LockUnavailableError("another writer holds the ledger")

    monkeypatch.setattr(kit.events, "append", refuse)
    with pytest.raises(br.TrackerDivergenceError, match="landed on the external tracker"):
        br.run_br(repo, ["create", "a bead", "-t", "task", "--json"])


@pytest.mark.usefixtures("fake_br")
def test_a_missing_kit_fails_the_command_rather_than_degrading(tmp_path: Path) -> None:
    """Above ``external`` the engine has promised both stores hold the same facts."""
    repo = _repo(tmp_path, br.MODE_DUAL)
    shutil.rmtree(repo / br.KIT_TRACKER_DIR)
    with pytest.raises(br.TrackerDivergenceError, match="not installed at"):
        br.run_br(repo, ["create", "a bead", "-t", "task", "--json"])


@pytest.mark.usefixtures("fake_br")
def test_the_soft_entry_point_mirrors_too(tmp_path: Path) -> None:
    """`try_run_br` is where `gate report` and the run-record comments go.

    Half the write surface reaches br through the soft entry point, so a mirror wired
    only into `run_br` would leave the gate rows — the ones the third differential query
    exists for — out of the ledger entirely.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    br.run_br(repo, ["create", "a bead", "-t", "task", "--json"])
    br.try_run_br(
        repo,
        [
            "gate",
            "report",
            "--gate",
            "verify",
            "--provider",
            ENGINE_PROVIDER,
            "--status",
            "fail",
            "--note",
            "verify fast: ruff=fail",
            "seam-0001",
        ],
    )

    kit = br.kit(repo)
    gate = next(event for event in _ledger_events(repo) if event.kind == kit.KIND_GATE)
    assert gate.payload[kit.GATE_NAME_KEY] == "verify"
    assert gate.payload[kit.GATE_PASSED_KEY] is False


# --- the flip, and the absence contract it must not change --------------------


def test_the_flip_reads_the_record_out_of_the_owned_ledger(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """The seam answers from the ledger, proven by making the two stores disagree.

    br's copy is edited behind the seam's back so that a read served from br would
    return the other title. Nothing else in this file can distinguish the two stores,
    which is the point: agreement is what the rest of the suite asserts.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    br.run_br(repo, ["create", "the owned title", "-t", "task", "--json"])
    fake_br.records["seam-0001"]["title"] = "the external title"

    (repo / "basicly.toml").write_text(f'[tracker]\nmode = "{br.MODE_OWNED}"\n', encoding="utf-8")
    record = br.read_record(repo, "seam-0001")

    assert record is not None
    assert record["title"] == "the owned title"


def test_the_flip_still_writes_the_external_tracker(tmp_path: Path, fake_br: _FakeBr) -> None:
    """`owned` flips the *record read* and nothing else.

    Nine of the eleven subcommands the engine spawns — ``comments list``, ``gate list``,
    ``blocked``, ``list``, ``lint``, ``dep cycles`` — are read at their own call site with
    their own payload shape, so they still answer out of br. Stopping the writes would
    break every one of them. (``scheduler`` was the tenth until basicly-vkh0.20 put it
    behind ``br.read_ranking``.)
    """
    repo = _repo(tmp_path, br.MODE_OWNED)
    br.run_br(repo, ["create", "a bead", "-t", "task", "--json"])
    br.run_br(repo, ["comments", "add", "seam-0001", "[harness-policy] note"])

    assert fake_br.records["seam-0001"]["comments"] == [{"text": "[harness-policy] note"}]
    assert _kinds(repo, "seam-0001")[-1] == br.kit(repo).events.KIND_COMMENT


@pytest.mark.parametrize("mode", [br.MODE_EXTERNAL, br.MODE_OWNED])
@pytest.mark.usefixtures("fake_br")
def test_a_bead_no_store_holds_reads_as_none(tmp_path: Path, mode: str) -> None:
    """One absence contract, asserted against both stores.

    This is the criterion in its comparative form: the flip is only transparent if the
    owned store answers absence the same way the external binary does. The empty list is
    the natural in-process answer and it is exactly the case that split six call sites
    from five before `basicly-tcmy.14` made the choice once.
    """
    repo = _repo(tmp_path, mode)
    br.run_br(repo, ["create", "a bead", "-t", "task", "--json"])

    assert br.read_record(repo, "seam-9999") is None


@pytest.mark.parametrize("mode", [br.MODE_EXTERNAL, br.MODE_OWNED])
@pytest.mark.usefixtures("fake_br")
def test_require_record_raises_one_message_naming_the_bead(tmp_path: Path, mode: str) -> None:
    """The hard half of the contract, unchanged by the flip — same message, both stores."""
    repo = _repo(tmp_path, mode)
    with pytest.raises(RuntimeError, match="br show seam-9999 returned no issue record"):
        br.require_record(repo, "seam-9999")


def test_an_empty_ledger_reads_as_absence_not_as_a_failure(tmp_path: Path) -> None:
    """A repo flipped before its import is answered, not crashed — and answers None.

    Which is also why ``basicly.toml`` ships ``external``: this is what ``owned`` looks
    like against a ledger step 1 never filled, and it is indistinguishable from every
    bead having been deleted.
    """
    repo = _repo(tmp_path, br.MODE_OWNED)
    assert br.read_record(repo, "seam-0001") is None


@pytest.mark.usefixtures("fake_br")
def test_a_tombstoned_record_reads_as_absent_after_the_flip(tmp_path: Path) -> None:
    """The two stores spell one deletion differently; the seam makes them agree.

    br expresses a deletion by not returning the record; the owned log expresses it by
    keeping the record and flagging it, leaving the *status* untouched. A reader that
    served the tombstoned record would hand out work on a bead somebody deleted — the
    defect `differential.is_ready` names, arriving one layer earlier.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    br.run_br(repo, ["create", "a bead", "-t", "task", "--json"])
    kit = br.kit(repo)
    kit.events.append(
        br.ledger_dir(repo), [kit.events.Draft("seam-0001", kit.events.KIND_TOMBSTONE, {})]
    )

    (repo / "basicly.toml").write_text(f'[tracker]\nmode = "{br.MODE_OWNED}"\n', encoding="utf-8")
    assert br.read_record(repo, "seam-0001") is None


def test_no_module_outside_the_seam_reads_the_owned_store() -> None:
    """The "confined to the seam" half of the criterion, checked against the tree.

    Eleven call sites used to unwrap ``br show --json`` by hand; `basicly-tcmy.14`
    collapsed them so that this bead would be an edit to one file. A second module
    reaching into the ledger — or branching on the mode — re-acquires the scatter, and
    the next cutover step pays for it again.
    """
    root = REPO_ROOT / "src" / "basicly"
    reaching = {"br.owned_record(", "br.tracker_mode(", "br.ledger_dir(", "br.kit("}
    offenders = sorted(
        f"{path.name}: {name}"
        for path in sorted(root.glob("*.py"))
        if path.name != "br.py"
        for name in reaching
        if name in path.read_text(encoding="utf-8")
    )
    assert offenders == []


# --- the shadow differential, run against the store the dual write filled -----


def _population(repo: Path, *, with_gates: bool) -> None:
    """Drive a population through the seam that moves all three differential queries.

    Every write goes through :func:`basicly.br.run_br`, so the owned side of the
    comparison is built by the dual write itself rather than authored — which is what
    makes the run evidence about this bead's deliverable.
    """
    br.run_br(repo, ["create", "parent", "-t", "epic", "--json"])  # seam-0001
    br.run_br(repo, ["create", "child", "-t", "task", "--parent", "seam-0001", "--json"])
    br.run_br(repo, ["create", "bound", "-t", "task", "--json"])  # seam-0003
    br.run_br(repo, ["create", "blocker", "-t", "task", "--json"])  # seam-0004
    br.run_br(repo, ["create", "blocked", "-t", "task", "--json"])  # seam-0005

    approved = "[harness-policy] checkpoint=classify approved"
    br.run_br(repo, ["comments", "add", "seam-0001", approved])
    br.run_br(repo, ["update", "seam-0003", "--external-ref", "worktree:bound:harness/bound"])
    br.run_br(repo, ["dep", "add", "seam-0005", "seam-0004", "-t", "blocks"])
    br.run_br(repo, ["close", "seam-0002", "--reason", "shipped by the harness loop"])
    if with_gates:
        _report(repo, "seam-0003", "pass", ENGINE_PROVIDER)
        _report(repo, "seam-0004", "fail", ENGINE_PROVIDER)
        # A foreign result on a required gate is disregarded rather than dropped, so this
        # record answers `missing` while plainly carrying a pass — the asymmetry
        # `gate_verdict` exists for, and a fourth distinct answer for the query.
        _report(repo, "seam-0005", "pass", FOREIGN_PROVIDER)


def _report(repo: Path, issue: str, status: str, provider: str) -> None:
    br.run_br(
        repo,
        [
            "gate",
            "report",
            "--gate",
            "verify",
            "--provider",
            provider,
            "--status",
            status,
            "--note",
            f"verify fast: {status}",
            issue,
        ],
    )


def _reference(repo: Path, fake: _FakeBr) -> Any:
    """The reference side: `_FakeBr`'s records as views, ignoring the owned ledger.

    The ledger argument is accepted and unused on purpose — that is what the
    independence probe perturbs, and a source that read it would be refused.
    """
    kit = br.kit(repo)

    def views(_ledger_events: object) -> dict[str, Any]:
        return {
            issue: kit.RecordView(
                record=issue,
                status=record["status"],
                external_ref=record.get("external_ref", ""),
                comments=tuple(comment["text"] for comment in record["comments"]),
                dependencies=tuple(
                    kit.Edge(target=edge["id"], type=edge["dependency_type"])
                    for edge in record["dependencies"]
                ),
                gates=tuple(
                    kit.GateRow(row["gate"], row["provider"], row["passed"])
                    for row in record["gates"]
                ),
            )
            for issue, record in fake.records.items()
        }

    return kit.ReferenceSource(views=views)


def test_the_differential_is_clean_and_conclusive_after_the_flip(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """The criterion the flip is licensed by: the two stores agree, *and* it means something.

    `basicly-vkh0.18` could get as far as clean. It could not get to conclusive, because
    the gate query had no owned-side rows to compare — the export carries no gate field,
    so `migrate.py` had nothing to import. The dual write is the writer `KIND_GATE` and
    its reader were defined for, and this is the assertion that says so.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _population(repo, with_gates=True)
    (repo / "basicly.toml").write_text(f'[tracker]\nmode = "{br.MODE_OWNED}"\n', encoding="utf-8")

    kit = br.kit(repo)
    report = kit.run_differential(br.ledger_dir(repo), _reference(repo, fake_br))

    assert report.clean, report.summary()
    assert report.conclusive, report.summary()
    assert report.compared == len(fake_br.records) == 5


def test_the_gate_query_discriminates_nothing_without_the_dual_write(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """The control, and it is the finding `basicly-vkh0.18` reported on the live tracker.

    The same population minus the ``gate report`` calls: every record answers the gate
    query identically, so the run is *clean and inconclusive* — agreement reported as the
    absence of evidence. Without this pair, the test above would pass on a comparison
    that never discriminated anything.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _population(repo, with_gates=False)

    kit = br.kit(repo)
    report = kit.run_differential(br.ledger_dir(repo), _reference(repo, fake_br))

    assert report.clean, report.summary()
    assert not report.conclusive
    assert [item.subject for item in report.inconclusive] == [kit.QUERY_GATES]


def test_the_differential_reports_a_store_the_mirror_missed(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """The discrimination control for ``clean`` itself.

    A clean report is only worth reading if an unclean one is reachable, so one fact is
    written to br alone — the shape a mirror that logged its failures would leave behind.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _population(repo, with_gates=True)
    fake_br.records["seam-0004"]["status"] = "closed"

    kit = br.kit(repo)
    report = kit.run_differential(br.ledger_dir(repo), _reference(repo, fake_br))

    assert not report.clean
    assert {item.record for item in report.disagreements} >= {"seam-0004", "seam-0005"}
