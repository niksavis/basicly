"""The gate query's store, rung by rung (basicly-vkh0.27).

Two claims, and each is asserted as a *comparison* rather than as a description, because
both stores answering the same way is the ordinary state and proves nothing:

- On ``owned`` the fold is the only answer, shown by making the stand-in br hold a
  contradicting row and then failing the test if any process is spawned at all.
- On ``dual`` both stores are read and a disagreement stops the read, shown by editing one
  store behind the seam's back — with the agreeing case and the history case as controls,
  so "refuses" is not satisfied by refusing everything.

The ledger side is written through the real dual write (`br.run_br` -> `br._mirror_write`),
not by appending events by hand: a gate event this seam cannot read back is exactly what a
hand-built fixture would hide. Nothing here spawns a process or reads the host's tracker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from basicly import br, gate_source, owned_store, policy
from basicly.config import PolicyConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
ISSUE = "gate-0001"

# The engine's own provider: a foreign one on a required gate is disregarded, so a test
# using one would derive `missing` on a record it had just recorded a pass for.
ENGINE_PROVIDER = "basicly-verify"


class _FakeBr:
    """A br stand-in holding one record's gate rows, in br's own argv and JSON shapes."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        args = list(cmd[1:])
        self.calls.append(args)
        if args[:1] == ["--version"]:
            return _proc(f"br {br.PINNED_VERSION}")
        if args[:2] == ["gate", "report"]:
            flags = {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i].startswith("-")}
            self.rows.append({
                "gate": flags["--gate"],
                "provider": flags["--provider"],
                "passed": flags["--status"] == "pass",
            })
            return _proc("")
        if args[:2] == ["gate", "list"]:
            return _proc(json.dumps({"issue_id": args[2], "results": self.rows}))
        return _proc("", stderr=f"Error: unknown command {' '.join(args)}", returncode=2)


def _proc(stdout: str, *, stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(["br"], returncode, stdout, stderr)


def _repo(tmp_path: Path, mode: str) -> Path:
    """A checkout with the kit installed and ``[tracker] mode`` declared."""
    shutil.copytree(KIT_SOURCE, tmp_path / br.KIT_TRACKER_DIR, ignore=shutil.ignore_patterns("*"))
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, tmp_path / br.KIT_TRACKER_DIR / source.name)
    (tmp_path / br.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    return _declare(tmp_path, mode)


def _declare(repo: Path, mode: str) -> Path:
    (repo / "basicly.toml").write_text(f'[tracker]\nmode = "{mode}"\n', encoding="utf-8")
    return repo


@pytest.fixture
def fake_br(monkeypatch: pytest.MonkeyPatch) -> _FakeBr:
    """A br on PATH, answering out of an in-process store instead of a database."""
    stand_in = _FakeBr()
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", {"/usr/bin/br"})
    monkeypatch.setattr(br.subprocess, "run", stand_in)
    return stand_in


def _report(repo: Path, *, gate: str = "verify", status: str = "pass") -> None:
    """One ``br gate report``, through the seam, so the dual write records it too."""
    flags = ["--gate", gate, "--provider", ENGINE_PROVIDER, "--status", status]
    br.run_br(repo, ["gate", "report", *flags, ISSUE])


def _refuse_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any process start fail the test, rather than merely un-installing br.

    "br was absent and the read silently degraded to no rows" would satisfy a weaker
    assertion and is exactly the failure mode a flipped read can have.
    """
    monkeypatch.setattr(br, "which", lambda: None)

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(br.subprocess, "run", refuse)


# --- the flip -----------------------------------------------------------------


def test_the_owned_rung_answers_from_the_fold_with_no_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_br: _FakeBr
) -> None:
    """The first criterion, as a comparison: br holds the other verdict and is never asked."""
    repo = _repo(tmp_path, br.MODE_DUAL)
    _report(repo)
    fake_br.rows[0]["passed"] = False
    _declare(repo, br.MODE_OWNED)
    _refuse_spawn(monkeypatch)

    assert gate_source.read_gates(repo, ISSUE) == [
        {"gate": "verify", "provider": ENGINE_PROVIDER, "passed": True}
    ]


@pytest.mark.usefixtures("fake_br")
def test_the_engine_reads_a_green_gate_off_the_fold_after_the_flip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The criterion at the surface that asks it: `policy.gate_status`, not the seam.

    The engine's question is *may this advance*, and the classification behind it — whose
    provider counts, which gate is required — has to survive the flip unchanged, which a
    test of the seam's row list alone would not show.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _report(repo)
    _declare(repo, br.MODE_OWNED)
    _refuse_spawn(monkeypatch)

    status = policy.gate_status(repo, ISSUE, PolicyConfig(required_gates=("verify",), max_rework=2))

    assert status.can_advance
    assert status.required_passed == ("verify",)


def test_a_gate_no_store_recorded_is_not_green_after_the_flip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: an empty fold answers *missing*, never an advance."""
    repo = _repo(tmp_path, br.MODE_OWNED)
    _refuse_spawn(monkeypatch)

    status = policy.gate_status(repo, ISSUE, PolicyConfig(required_gates=("verify",), max_rework=2))

    assert not status.can_advance
    assert status.required_missing == ("verify",)


@pytest.mark.usefixtures("fake_br")
def test_a_tombstoned_record_answers_no_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The absence rule `br.owned_record` states: the two stores spell a deletion apart."""
    repo = _repo(tmp_path, br.MODE_DUAL)
    _report(repo)
    kit = br.kit(repo)
    kit.events.append(
        br.ledger_dir(repo),
        [kit.events.Draft(ISSUE, kit.events.KIND_TOMBSTONE, {})],
    )
    _declare(repo, br.MODE_OWNED)
    _refuse_spawn(monkeypatch)

    assert gate_source.read_gates(repo, ISSUE) == []


# --- the dual rung ------------------------------------------------------------


def test_the_dual_rung_reads_both_stores_and_agrees(tmp_path: Path, fake_br: _FakeBr) -> None:
    """The control for the refusal below: the dual write leaves the two stores equal."""
    repo = _repo(tmp_path, br.MODE_DUAL)
    _report(repo)
    fake_br.calls.clear()

    rows = gate_source.read_gates(repo, ISSUE)

    assert rows == [{"gate": "verify", "provider": ENGINE_PROVIDER, "passed": True}]
    assert [call[:2] for call in fake_br.calls] == [["gate", "list"]]
    assert gate_source.owned_gates(repo, ISSUE) == rows


def test_a_dual_disagreement_is_refused_rather_than_resolved(
    tmp_path: Path, fake_br: _FakeBr
) -> None:
    """The second criterion, with br's copy edited behind the seam's back.

    The two stores then hold different verdicts for one ``(gate, provider)`` — the state a
    read that returned br's answer and said nothing would resolve silently.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _report(repo)
    fake_br.rows[0]["passed"] = False

    with pytest.raises(owned_store.TrackerDivergenceError) as caught:
        gate_source.read_gates(repo, ISSUE)

    assert ISSUE in str(caught.value)
    assert "basicly tracker adopt" in str(caught.value)


def test_a_row_only_br_holds_is_a_disagreement(tmp_path: Path, fake_br: _FakeBr) -> None:
    """A bypassed write on a record the ledger *does* hold gates for is the reportable case.

    It is what `basicly tracker adopt` exists to repair (basicly-vkh0.24), and it is the one
    the excusal below must not swallow.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    _report(repo)
    fake_br.rows.append({"gate": "dor", "provider": ENGINE_PROVIDER, "passed": True})

    with pytest.raises(owned_store.TrackerDivergenceError):
        gate_source.read_gates(repo, ISSUE)


def test_a_record_with_no_ledger_gate_event_is_history(tmp_path: Path, fake_br: _FakeBr) -> None:
    """The excusal, and it is load-bearing rather than a softening.

    No export carries a gate field (`differential.EXPORT_CANNOT_EXPRESS`), so every gate br
    recorded before the dual write began is absent from the ledger by construction. Refusing
    those would stop the loop on every pre-cutover bead, which is the population
    `baseline.py` excuses for the whole-tracker differential.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    fake_br.rows.append({"gate": "verify", "provider": ENGINE_PROVIDER, "passed": True})

    assert gate_source.read_gates(repo, ISSUE) == fake_br.rows


def test_the_external_rung_never_consults_the_ledger(tmp_path: Path, fake_br: _FakeBr) -> None:
    """The pre-cutover control: the same disagreement is not a finding before step 3."""
    repo = _repo(tmp_path, br.MODE_DUAL)
    _report(repo)
    fake_br.rows[0]["passed"] = False
    _declare(repo, br.MODE_EXTERNAL)

    assert gate_source.read_gates(repo, ISSUE) == [
        {"gate": "verify", "provider": ENGINE_PROVIDER, "passed": False}
    ]


@pytest.mark.usefixtures("fake_br")
def test_an_unusable_reply_from_br_is_a_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty list would read as a gate that has not run and derive an unbuilt phase."""
    repo = _repo(tmp_path, br.MODE_EXTERNAL)
    monkeypatch.setattr(br.subprocess, "run", lambda *_a, **_k: _proc("{not json"))

    with pytest.raises(RuntimeError, match="no usable JSON"):
        gate_source.read_gates(repo, ISSUE)


# --- the confinement ----------------------------------------------------------


def test_the_cutover_branch_lives_only_in_the_two_seam_modules() -> None:
    """Where the mode may be branched on, in either spelling of the store's names.

    `test_br_seam.test_no_module_outside_the_seam_reads_the_owned_store` guards the
    ``br.``-prefixed spelling against every module but `br` itself. This module reaches the
    same functions through :mod:`basicly.owned_store` directly, so it needs the second
    spelling probed and itself named — an exemption a reviewer sees, rather than one the
    first guard's literal strings happen not to catch.

    The expected list is `gate_source` itself rather than empty, which is what makes the
    zero on every other module evidence: a probe matching nothing at all would report the
    same empty list. `br` is left out because it *is* the seam and reaches these names
    unqualified anyway.
    """
    root = REPO_ROOT / "src" / "basicly"
    reaching = [
        f"{prefix}{name}("
        for prefix in ("br.", "owned_store.")
        for name in ("owned_record", "tracker_mode", "ledger_dir", "kit")
    ]
    branching = sorted(
        path.name
        for path in sorted(root.glob("*.py"))
        if path.name != "br.py"
        if any(name in path.read_text(encoding="utf-8") for name in reaching)
    )

    assert branching == ["gate_source.py"]
