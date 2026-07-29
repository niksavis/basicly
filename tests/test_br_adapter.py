"""Tests for the single br adapter seam (src/basicly/br.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from basicly import br


def test_run_br_raises_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hard entry point raises with one canonical absence message."""
    monkeypatch.setattr(br, "which", lambda: None)
    with pytest.raises(RuntimeError, match="br is not on PATH"):
        br.run_br(tmp_path, ["ready"])


def test_try_run_br_returns_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The soft entry point degrades to None for optional tracker features."""
    monkeypatch.setattr(br, "which", lambda: None)
    assert br.try_run_br(tmp_path, ["sync", "--merge"]) is None


def test_version_probe_warns_below_the_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An outdated br gets one warning per process, never a failure."""
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", set())

    def fake_run(cmd, **_kw):
        out = "br 0.0.1" if "--version" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    br.run_br(tmp_path, ["ready"])
    br.run_br(tmp_path, ["ready"])
    err = capsys.readouterr().err
    assert err.count("older than the harness floor") == 1


def test_version_probe_warns_when_newer_than_the_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A br *above* the pin warns too: 0.2.19 broke `gate report` (basicly-o7z5).

    The floor check cannot see this — it compares major.minor, where 0.2.19 and
    the pinned 0.2.16 are equal — so an upgraded machine ran a tracker the
    harness was never tested against and the only symptom was integration
    tests failing there while CI, still on the pin, stayed green.
    """
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", set())

    def fake_run(cmd, **_kw):
        out = "br 0.2.19" if "--version" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    br.run_br(tmp_path, ["ready"])
    br.run_br(tmp_path, ["ready"])
    err = capsys.readouterr().err
    assert err.count("is not the pinned") == 1
    assert br.PINNED_VERSION in err
    assert "older than the harness floor" not in err


def test_version_probe_is_silent_on_the_pinned_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The supported state warns about nothing, so the warning stays meaningful."""
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", set())

    def fake_run(cmd, **_kw):
        out = f"br {br.PINNED_VERSION}" if "--version" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    br.run_br(tmp_path, ["ready"])
    assert capsys.readouterr().err == ""


# --- Reading the committed export (basicly-kjc5.50) --------------------------


def _write_export(beads: Path, *lines: str) -> None:
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "issues.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_export_records_skips_junk_and_keeps_file_order(tmp_path: Path) -> None:
    """An unparsable or id-less line is skipped, never fatal: every consumer is evidence."""
    _write_export(
        tmp_path / ".beads",
        json.dumps({"id": "b-1"}),
        "{not json",
        json.dumps({"title": "no id"}),
        "",
        json.dumps({"id": "b-2"}),
    )
    assert [record["id"] for record in br.export_records(tmp_path)] == ["b-1", "b-2"]


def test_export_records_is_empty_without_an_export(tmp_path: Path) -> None:
    """No workspace, no records — the callers all degrade rather than fail."""
    assert br.export_records(tmp_path) == []


def test_export_records_follows_the_beads_redirect(tmp_path: Path) -> None:
    """A harness worktree shares the base tracker, so the redirect target is authoritative."""
    base = tmp_path / "base" / ".beads"
    _write_export(base, json.dumps({"id": "base-1"}))
    worktree = tmp_path / "wt"
    _write_export(worktree / ".beads", json.dumps({"id": "stale-1"}))
    (worktree / ".beads" / "redirect").write_text(str(base), encoding="utf-8")

    assert br.beads_dir(worktree) == base
    assert [record["id"] for record in br.export_records(worktree)] == ["base-1"]


def test_export_comment_texts_reads_only_well_formed_comments() -> None:
    """Comments are the shared ledger's carrier; a malformed row is ignored."""
    record = {
        "id": "b-1",
        "comments": [{"text": "first"}, {"author": "niksa"}, "not a row", {"text": 7}],
    }
    assert br.export_comment_texts(record) == ["first"]
    assert br.export_comment_texts({"id": "b-2"}) == []


# --- br's clock-skew rejection (basicly-jr0l.41) -------------------------------

_SKEW_STDERR = "Error: Validation failed: updated_at: cannot be before created_at"


def _skewed_run(monkeypatch: pytest.MonkeyPatch, failures: int, stderr: str) -> list[list[str]]:
    """Fake br: fail *failures* times with *stderr*, then succeed. Returns the calls."""
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", {"/usr/bin/br"})
    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        calls.append(list(cmd))
        if len(calls) <= failures:
            return subprocess.CompletedProcess(cmd, 1, "", stderr)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    return calls


def test_a_clock_skew_rejection_is_retried_until_it_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Br rejects an update whose updated_at precedes created_at: the clock, not the request.

    This blocked a landing twice consecutively with a different victim test each
    run, so it read as suite flakiness rather than as a dependency defect.
    """
    calls = _skewed_run(monkeypatch, failures=2, stderr=_SKEW_STDERR)
    slept: list[float] = []
    monkeypatch.setattr(br.time, "sleep", slept.append)

    proc = br.run_br(tmp_path, ["update", "x", "-t", "task"])

    assert proc.returncode == 0
    assert len(calls) == 3  # two rejections, then the retry that stuck
    assert slept, "the retry must wait for the clock to catch up, not re-read the same skew"


def test_any_other_br_error_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real error must still fail fast — this is one defect's escape hatch, not a retry policy."""
    calls = _skewed_run(monkeypatch, failures=1, stderr="Error: issue not found")
    monkeypatch.setattr(br.time, "sleep", lambda _s: pytest.fail("must not back off"))

    with pytest.raises(RuntimeError, match="issue not found"):
        br.run_br(tmp_path, ["update", "nope"])

    assert len(calls) == 1


def test_a_persistent_clock_skew_gives_up_at_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry is bounded by elapsed time, not by an attempt count.

    Bounded that way because the wait a skew needs cannot be derived — br's error
    names no timestamps — so a fixed ladder could not span a step larger than
    itself (basicly-jr0l.42). The clock here advances slowly enough to allow far
    more attempts than any fixed ladder would, which is what makes this
    discriminating rather than merely satisfied.

    Injects the clock rather than patching ``br.time.monotonic``: that patches the
    global ``time`` module, so ``tracker_usage.timed`` would consume the same
    ticks for its own latency measurement and the count would silently be wrong.
    """
    _skewed_run(monkeypatch, failures=99, stderr=_SKEW_STDERR)
    calls: list[float] = []
    ticks = iter([n * 0.5 for n in range(40)])

    proc = br._spawn_tolerating_clock_skew(
        "/usr/bin/br",
        tmp_path,
        ["update", "x"],
        sleep=calls.append,
        monotonic=lambda: next(ticks),
    )

    assert br._is_clock_skew(proc), "the caller must still see the unrescued failure"
    # deadline = 0.0 + 5.0; the check reaches it on the tenth attempt, so nine waits
    assert len(calls) == 9, len(calls)


def test_the_deadline_never_consults_the_wall_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wall clock is the thing misbehaving, so it cannot bound a wait on itself.

    A wall-clock deadline would be extended by the very backward step it is
    waiting out, so the retry could outlive its own bound.
    """
    _skewed_run(monkeypatch, failures=1, stderr=_SKEW_STDERR)
    monkeypatch.setattr(br.time, "sleep", lambda _s: None)
    monkeypatch.setattr(br.time, "time", lambda: pytest.fail("must not read the wall clock"))

    assert br.run_br(tmp_path, ["update", "x"]).returncode == 0


def test_a_retry_is_countable_in_the_usage_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound stays a guess until the ledger says how many attempts a real skew needs.

    Only a retry carries the field, so existing ledger lines stay byte-identical
    and the committed file does not churn.
    """
    _skewed_run(monkeypatch, failures=2, stderr=_SKEW_STDERR)
    monkeypatch.setattr(br.time, "sleep", lambda _s: None)
    recorded: list[int] = []
    monkeypatch.setattr(
        br.tracker_usage, "record", lambda *_a, **kw: recorded.append(kw.get("attempt", 1))
    )

    br.run_br(tmp_path, ["update", "x"])

    assert recorded == [1, 2, 3]


def test_a_soft_call_site_tolerates_the_same_skew(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """try_run_br swallows failures, so without the retry the skew would corrupt state silently."""
    calls = _skewed_run(monkeypatch, failures=1, stderr=_SKEW_STDERR)
    monkeypatch.setattr(br.time, "sleep", lambda _s: None)

    proc = br.try_run_br(tmp_path, ["comments", "add", "x", "note"])

    assert proc is not None and proc.returncode == 0
    assert len(calls) == 2
