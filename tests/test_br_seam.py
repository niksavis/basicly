"""The dual write and the flip at the br seam (basicly-vkh0.19).

Steps 3 and 4 of the cutover in ``docs/requirements/work-tracker.md`` §5, and the three
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

The last section is step 2's own driver (`basicly-vkh0.18`), added later: every test
above hands the kit a reference the test authored, while `br.shadow_differential`
builds one by spawning br — and that builder is the part a production run can get
wrong.

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

from basicly import br, policy, run_record
from basicly.config import PolicyConfig

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
        """Every requested id, br's own way: many ids per spawn, gates on none of them.

        The gate rows are withheld deliberately rather than for brevity — ``br show``
        carries no gate field, so a reader that took them from here would be reading a
        fact this surface does not hold and the third query would look answered.
        """
        wanted = [arg for arg in args[1:] if not arg.startswith("-")]
        found = [self.records[issue] for issue in wanted if issue in self.records]
        if not found:
            return _proc("", stderr="Error: issue not found", returncode=1)
        return _proc(
            json.dumps([
                {key: value for key, value in record.items() if key != "gates"} for record in found
            ])
        )

    def _list(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """The population, in br's envelope — closed records only when ``-a`` is passed.

        The filter is implemented rather than ignored because it is the thing the
        reference has to get right: without ``-a`` br answers for open records only, and
        a reference silently missing every closed bead is the population-hiding shape
        this repo has already paid for once.
        """
        issues = [
            record
            for record in self.records.values()
            if "-a" in args or record["status"] != "closed"
        ]
        return _proc(json.dumps({"issues": issues, "total": len(issues), "has_more": False}))

    def _gate_list(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """One record's gate rows — the query no export can answer."""
        record = self.records.get(args[2])
        if record is None:
            return _proc("", stderr="Error: issue not found", returncode=1)
        return _proc(json.dumps({"issue_id": args[2], "results": record["gates"]}))


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


# --- the dual write -----------------------------------------------------------
#
# Which rung a repo declares, where its ledger lives and how the kit is reached are
# `tests/test_owned_store.py`'s — this file starts from a mode already resolved and
# asserts what the seam then *does* with it.


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
    """`owned` flips the *record read* and the markers, and nothing else.

    The subcommands still read at their own call site with their own payload shape —
    ``gate list``, ``blocked``, ``list``, ``lint``, ``dep cycles``, and the two
    ``comments list`` spawns basicly-s5li left behind in `decompose` and `supervise` —
    still answer out of br. Stopping the writes would break every one of them.
    (``scheduler`` was on that list until basicly-vkh0.20 put it behind
    ``br.read_ranking``.)

    A raw ``comments add`` through the funnel is deliberately still both-stores: the
    seam is what stops spawning, not the funnel underneath it, which is what keeps
    those two remaining callers reading their own writes.
    """
    repo = _repo(tmp_path, br.MODE_OWNED)
    br.run_br(repo, ["create", "a bead", "-t", "task", "--json"])
    br.run_br(repo, ["comments", "add", "seam-0001", "[harness-info] a call site's own"])

    assert fake_br.records["seam-0001"]["comments"] == [
        {"text": "[harness-info] a call site's own"}
    ]
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


# --- step 5: the harness markers, carried without br (basicly-s5li) -----------
#
# The criterion is a *negative* about br plus a *positive* about the ledger, and both
# halves need saying: the engine records a checkpoint approval, a gate record, a grant,
# a rework counter and a dispatch record, reads every one of them back, and does it with
# br absent from PATH. So the fixture below does not merely un-install br — it makes a
# spawn fail the test, because "br was absent and the code silently degraded to writing
# nothing" would satisfy a weaker assertion and is exactly the failure mode this seam
# could have.


@pytest.fixture
def no_br(monkeypatch: pytest.MonkeyPatch) -> None:
    """No br on PATH, and a spawn is a failure rather than a fallback."""
    monkeypatch.setattr(br, "which", lambda: None)

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(br.subprocess, "run", refuse)


def _run(seed: str, *, tokens: int) -> run_record.RunRecord:
    """One dispatch record, keyed on *seed* so two dispatches can be told apart."""
    return run_record.RunRecord(
        agent="claude",
        outcome="EXECUTED",
        returncode=0,
        duration_s=1.0,
        command=("claude", "-p", "<prompt>"),
        timestamp="2026-08-07T00:00:00+00:00",
        tokens=tokens,
        prompt_sha256=seed * 64,
        phase="build",
    )


def _owned_repo(tmp_path: Path, *records: str) -> Path:
    """A flipped checkout whose ledger already holds *records*, open.

    Seeded through the kit rather than through `br create`, because a repo that has to
    spawn br to acquire its own records could not be the subject of a test about br
    being absent.
    """
    repo = _repo(tmp_path, br.MODE_OWNED)
    kit = br.kit(repo)
    kit.events.append(
        br.ledger_dir(repo),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in records
        ],
    )
    return repo


@pytest.mark.usefixtures("no_br")
def test_the_engine_carries_every_marker_family_with_br_absent(tmp_path: Path) -> None:
    """The acceptance criterion, driven through the engine's own API rather than the seam.

    Each family is written and then read back by the function the loop actually calls —
    `approve_checkpoint`/`checkpoint_approved`, `spend_gate_override`/`gate_override_spent`
    and `record_unreliable_gate`/`unreliable_gate_events`, `issue_grant_guarded`/
    `active_grant`, `record_rework`/`rework_charged`, `record_marker`/`tracker_history`.
    Going through `policy` and `run_record` rather than through `br.add_comment` is the
    point: what has to survive the flip is the engine, and a seam-level round trip would
    pass while a caller still spawned br at its own call site.

    Two beads rather than one, because the dispatch record is read back through the
    *whole-tracker* query: keyed wrong, one bead's history reads as every bead's and the
    per-bead assertion below would not notice.
    """
    repo = _owned_repo(tmp_path, "seam-0001", "seam-0002")
    config = PolicyConfig(required_gates=("verify",), max_rework=2, autonomy="L3")

    policy.approve_checkpoint(repo, "seam-0001", "ship")
    assert policy.spend_gate_override(repo, "seam-0001", "verify") is True
    policy.record_unreliable_gate(repo, "seam-0001", "verify", "passed unchanged")
    grant = policy.issue_grant_guarded(repo, "seam-0001", "L3", 8_000_000, config, interactive=True)
    charged = policy.record_rework(repo, "seam-0001", "verify")
    ident = run_record.record_marker(repo, "seam-0001", _run("a", tokens=1234))
    other = run_record.record_marker(repo, "seam-0002", _run("c", tokens=99))

    assert policy.checkpoint_approved(repo, "seam-0001", "ship") is True
    assert policy.gate_override_spent(repo, "seam-0001", "verify") is True
    assert policy.unreliable_gate_events(repo, "seam-0001", "verify") == 1
    assert grant.status == "approved"
    active = policy.active_grant(repo, "seam-0001")
    assert active is not None
    assert (active.level, active.token_budget) == ("L3", 8_000_000)
    assert charged == 1
    assert policy.rework_charged(repo, "seam-0001", "verify") == 1
    assert ident is not None and other is not None
    history = run_record.tracker_history(repo)
    assert [entry["tokens"] for entry in history["seam-0001"]] == [1234]
    assert [entry["tokens"] for entry in history["seam-0002"]] == [99]
    # The families do not bleed into each other: seam-0002 carries only its dispatch.
    assert policy.checkpoint_approved(repo, "seam-0002", "ship") is False


@pytest.mark.usefixtures("no_br")
def test_a_second_dispatch_record_is_told_from_the_first_without_br(tmp_path: Path) -> None:
    """The dispatch record's idempotency read is the seam's, not br's.

    `record_marker` derives its id from the prompt and phase and then asks the tracker
    which ids are already recorded, so a re-dispatch is a *second* entry rather than a
    duplicate of the first. That read used to be a `comments list` spawn; if it came back
    empty after the flip the two dispatches would collapse into one id and the attempt
    count — what rework is charged against — would silently understate itself.
    """
    repo = _owned_repo(tmp_path, "seam-0001")
    record = _run("b", tokens=10)

    first = run_record.record_marker(repo, "seam-0001", record)
    second = run_record.record_marker(repo, "seam-0001", record)

    assert first != second
    assert len(run_record.tracker_history(repo)["seam-0001"]) == 2


@pytest.mark.usefixtures("no_br")
def test_the_marker_stamp_survives_the_flip_so_a_wait_stays_measurable(tmp_path: Path) -> None:
    """The wait clock reads the *tracker's* stamp, and both stores have to supply one.

    br stamps a comment ``created_at``; the owned ledger stamps the event ``ts``. The
    seam renders one into the other, and this is the assertion that it is a real,
    parseable stamp rather than an empty string — an unparseable start is recorded as no
    wait at all (`policy.record_wait`), so the whole human-wait rollup would go quietly
    to zero without ever failing.
    """
    repo = _owned_repo(tmp_path, "seam-0001")

    wait_id = policy.record_wait_request(repo, "seam-0001", "ship")
    assert wait_id is not None
    event = policy.record_checkpoint_wait(repo, "seam-0001", "ship", by="human", delegated=False)

    assert event is not None
    assert event.wait_id == wait_id
    assert event.requested_at  # the ledger's own stamp, not the reader's clock
    assert policy.wait_events(repo, "seam-0001")[0].wait_id == wait_id


def test_the_flip_stops_spawning_br_for_a_marker(tmp_path: Path, fake_br: _FakeBr) -> None:
    """The negative half, asserted against a br that *is* available.

    `no_br` proves the engine works without br; this proves it does not use br when br is
    there — which is the claim that makes the 45% of tracker traffic measured on this
    repo actually go away rather than merely become optional.
    """
    repo = _owned_repo(tmp_path, "seam-0001")

    br.add_comment(repo, "seam-0001", "[harness-policy] checkpoint=ship approved")

    assert [call for call in fake_br.calls if call[:2] == ["comments", "add"]] == []
    assert [row["text"] for row in br.read_comments(repo, "seam-0001")] == [
        "[harness-policy] checkpoint=ship approved"
    ]


def test_a_marker_write_is_still_refused_inside_a_read_only_section(tmp_path: Path) -> None:
    """The read-only guard survives the flip, and is checked at the seam rather than below.

    Its two recorded incidents were both tracker writes a pre-flight gate should not have
    made, and neither store can delete a comment once recorded — so a flip that moved the
    write out from under `run_br` would have removed the guard along with the spawn. This
    is the assertion that it did not: nothing here installs a br at all, so the only place
    left to refuse is :func:`basicly.br.add_comment` itself.
    """
    repo = _owned_repo(tmp_path, "seam-0001")

    with br.read_only("a pre-flight gate"), pytest.raises(br.TrackerWriteRefusedError) as excinfo:
        br.add_comment(repo, "seam-0001", "[harness-policy] recorded from a gate")

    assert "a pre-flight gate" in str(excinfo.value)
    assert br.read_comments(repo, "seam-0001") == []


def test_the_soft_marker_write_is_refused_too(tmp_path: Path) -> None:
    """Soft means "tolerates a store that cannot answer", never "tolerates the ban".

    The dispatch record and the spend rollup both write through the soft entry point, and
    both run inside the loop's gates — a refusal swallowed into ``False`` there would read
    as "the tracker was busy" and the gate's promise would be broken silently.
    """
    repo = _owned_repo(tmp_path, "seam-0001")

    with br.read_only("a pre-flight gate"), pytest.raises(br.TrackerWriteRefusedError):
        br.try_add_comment(repo, "seam-0001", "[harness-run] id=x phase=build")


@pytest.mark.usefixtures("no_br")
def test_a_tombstoned_records_markers_read_as_absent(tmp_path: Path) -> None:
    """Same rule as :func:`basicly.br.owned_record`, at the marker read.

    A deleted bead's rework counter must not still be charging: the two stores spell
    absence differently and the seam is where they are made to agree, once.
    """
    repo = _owned_repo(tmp_path, "seam-0001")
    br.add_comment(repo, "seam-0001", "[harness-policy] rework gate=verify")
    kit = br.kit(repo)
    kit.events.append(
        br.ledger_dir(repo), [kit.events.Draft("seam-0001", kit.events.KIND_TOMBSTONE, {})]
    )

    assert br.read_comments(repo, "seam-0001") == []
    assert br.all_comment_texts(repo) == {}


@pytest.mark.usefixtures("no_br")
def test_a_counter_refuses_to_read_a_store_that_cannot_answer(tmp_path: Path) -> None:
    """A tracker that will not load must not read as "no markers recorded".

    Every family behind :func:`basicly.br.read_comments` is a counter or a refusal, so the
    fail-open direction is the dangerous one: an unreadable store answering ``[]`` reads
    as zero rework attempts charged and nothing blocking, and the loop advances past the
    gate the marker existed to hold. The soft reader is the one allowed to answer empty,
    and it is asserted here beside the hard one so the split is a comparison.
    """
    repo = _repo(tmp_path, br.MODE_OWNED)
    for source in (repo / br.KIT_TRACKER_DIR).glob("*.py"):
        source.unlink()

    with pytest.raises(RuntimeError):
        br.read_comments(repo, "seam-0001")
    assert br.try_read_comments(repo, "seam-0001") == []
    assert br.all_comment_texts(repo) == {}


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


# --- the reference built from the live tracker (basicly-vkh0.18) --------------
#
# Every test above hands the kit a reference the test itself authored. These drive
# `br.shadow_differential`, which builds one by spawning br — the driver §5's step 2
# was missing, and the only part of the run that can be got wrong in production.


def test_the_shadow_differential_reads_the_live_tracker_for_its_reference(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """The bead's deliverable, asserted through what only a live read can produce.

    ``conclusive`` is the load-bearing half. The gate query is answerable from `br gate
    list` and from nowhere else — no export carries a gate field — so a reference that
    read a snapshot, or that took its rows from ``br show``, would leave every record
    answering the gate query identically and the run would come back *inconclusive*.
    It is therefore not possible to satisfy this assertion without having spawned the
    surface the export cannot replace.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _population(repo, with_gates=True)

    report = br.shadow_differential(repo)

    assert report.refusals == []
    assert report.clean, report.summary()
    assert report.conclusive, report.summary()
    assert report.compared == len(fake_br.records) == 5


def test_the_live_reference_answers_for_the_closed_records_too(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """The population is the whole tracker, not the open part of it.

    `br list` reports open records only unless asked otherwise, and the population here
    contains one closed record (``seam-0002``, closed by the loop's own ship step). A
    reference that inherited that default would answer for four of five and the report
    would say so, rather than the omission passing as agreement.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _population(repo, with_gates=True)

    report = br.shadow_differential(repo)

    assert fake_br.records["seam-0002"]["status"] == "closed"
    assert report.unanswered == []
    assert report.compared == 5


def test_the_live_reference_re_reads_the_tracker_rather_than_memoising(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """The independence probe only proves something against a source that re-reads.

    `audit_reference` perturbs the owned ledger and asks the reference again; a source
    that cached its first answer would return it unchanged and clear the probe by being
    the *same* answer rather than an independent one. That is the one hole the kit
    documents its audit as unable to close, so it is closed here instead — by the source
    genuinely spawning br a second time.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _population(repo, with_gates=True)
    fake_br.calls.clear()

    br.shadow_differential(repo)

    assert [call[0] for call in fake_br.calls].count("list") == 2


@pytest.mark.usefixtures("fake_br")
def test_a_reference_derived_from_the_ledger_is_refused_not_reported_clean(
    tmp_path: Path,
) -> None:
    """The discrimination control for the live source: perturbation catches a derivative.

    Same ledger, same three queries, and a reference that folds the *owned* events
    instead of reading br. It agrees with the owned side on every record — that is what
    two derivatives of one snapshot do — and the report still refuses it. The pair with
    the live run above is the evidence that the probe is doing work rather than passing
    everything: one source moves when the ledger is perturbed and one does not.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _population(repo, with_gates=True)

    kit = br.kit(repo)
    derived = kit.ReferenceSource(views=kit.views_from_events)
    report = kit.run_differential(br.ledger_dir(repo), derived)

    assert report.disagreements == []
    assert [refusal.rule for refusal in report.refusals] == [kit.RULE_DERIVED_FROM_LEDGER]
    assert not report.clean, report.summary()


def test_the_shadow_read_writes_nothing_to_either_store(tmp_path: Path, fake_br: _FakeBr) -> None:
    """Shadow mode is defined as read-only, so the run is held to it by the write guard.

    Asserted on both stores: no br surface outside the classified reads is spawned, and
    the owned ledger holds exactly the events the population wrote before the run.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _population(repo, with_gates=True)
    before = len(_ledger_events(repo))
    fake_br.calls.clear()

    br.shadow_differential(repo)

    surfaces = {" ".join(_surface_words(call)) for call in fake_br.calls}
    assert surfaces <= {"list", "show", "gate list"}
    assert len(_ledger_events(repo)) == before


def test_the_scoped_run_forwards_the_engines_vocabulary_to_the_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_br: _FakeBr
) -> None:
    """The boundary must not swallow the names the caller configured (basicly-c357).

    `cli` is the layer allowed to read the engine's configured gate names, and it hands
    them to the scoped run rather than the raw one now. If the forwarding broke, the
    comparison would silently fall back to the kit's defaults — which mirror the engine's
    today, so it would look identical and diverge the first time a repo configures its own
    required gates. That is the failure this asserts against, not the scoping.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _population(repo, with_gates=True)
    fake_br.calls.clear()
    seen: dict[str, object] = {}

    # Bound before the patch, or `capture` calls itself: the seam reaches the comparison
    # through the module attribute this test replaces.
    real = br.shadow_differential

    def capture(_root: Path, vocabulary: dict[str, object] | None):
        seen["vocabulary"] = vocabulary
        return real(_root, vocabulary)

    monkeypatch.setattr(br, "shadow_differential", capture)

    br.scoped_differential(repo, {"required_gates": ("verify", "rubric")})

    assert seen["vocabulary"] == {"required_gates": ("verify", "rubric")}
