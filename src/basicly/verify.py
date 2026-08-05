"""Config-driven verify runner — the harness's deterministic gate.

Runs the checks declared in ``[verify]`` of ``basicly.toml`` for a given mode
(``fast`` / ``full`` / ``staged``), collecting a pass/fail/skip verdict per
check. When an issue id is supplied it records the aggregate verdict as a gate
via ``br gate report``. The block-vs-advise policy (which gates are required,
the rework rule) lives in the gate/checkpoint engine, not here — this runner
only produces and records the verdict.

A check may declare a ``fix_command`` — a deterministic, lossless repair such as
a formatter's write mode. ``apply_fixes`` runs those and nothing else; it is
never part of a plain verify run, so the verdict a consumer (or CI) gets from
``run_verify`` is unchanged.

Check subprocess output streams straight to the terminal (it is not captured),
so the consumer sees each tool's own output live.

Every check that passes is recorded in the engine's own execution ledger
(:mod:`basicly.usage`), because this runner is the only thing that ever executes
a declared check — see :func:`run_check`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import br, usage, worktree
from .config import VERIFY_GATE_PROVIDER, VerifyCheck, VerifyConfig, load_verify_config

DEFAULT_GATE = "verify"
# Single-sourced in config so policy.gate_status can recognise it as engine-owned
# without importing this module (basicly-jr0l.51).
GATE_PROVIDER = VERIFY_GATE_PROVIDER


def linked_worktree_guard(repo_root: Path) -> str | None:
    """Reason recording a gate from *repo_root* would lose it, or None when safe.

    A linked git worktree whose ``.beads`` redirects to the base checkout (the
    ``redirect`` file provisioning writes) shares the one real tracker, so
    recording from it is safe. Without the redirect, the worktree carries its
    own throwaway tracker copy and a gate recorded there never reaches the base
    checkout — it is discarded at landing.
    """
    try:
        main = worktree.main_checkout(repo_root)
    except OSError, RuntimeError:
        # worktree.run wraps any git failure in RuntimeError — outside a git
        # checkout there is no landing to lose the gate to.
        return None
    root = Path(repo_root).resolve()
    if main == root:
        return None
    redirect = root / ".beads" / "redirect"
    if redirect.is_file():
        try:
            target = Path(redirect.read_text(encoding="utf-8").strip()).resolve()
        except OSError:
            target = None
        if target == main / ".beads":
            return None  # shared tracker — the record lands in the base checkout
    return (
        f"this checkout is a linked worktree of {main} without a .beads/redirect "
        "to it; a gate recorded here lives in the worktree's throwaway tracker "
        "copy and is discarded at landing. The loop records the verify gate from "
        "the base checkout when it lands the worktree — run without --issue "
        "here, or record the gate from the base checkout."
    )


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one verify check."""

    name: str
    status: str  # "pass" | "fail" | "skip"
    returncode: int
    # One-line human-readable context for a failure the tool itself could not
    # report (e.g. the command was not found on PATH).
    detail: str = ""
    # Combined stdout+stderr, populated only when the caller asked to capture
    # (basicly-kjc5.56). Empty for a streamed run, which is every normal one —
    # a gate's output belongs on the operator's terminal, not in memory.
    output: str = ""


@dataclass(frozen=True)
class VerifyReport:
    """The aggregate outcome of a verify run."""

    mode: str
    results: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        """True when no check failed (skips and an empty run count as passing)."""
        return not any(r.status == "fail" for r in self.results)

    @property
    def failures(self) -> tuple[str, ...]:
        """Names of the checks that failed."""
        return tuple(r.name for r in self.results if r.status == "fail")


def staged_files(repo_root: Path, suffix: str) -> list[str] | None:
    """Staged (added/copied/modified) files ending in *suffix*; None if git failed.

    None and [] are deliberately distinct: an empty list means "nothing staged"
    (the check may skip), None means the git call itself failed — a lost gate
    must never pass unnoticed, so callers must fail the check.
    """
    try:
        proc = subprocess.run(  # nosec B603 B607
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.endswith(suffix)]


def run_check(
    check: VerifyCheck, repo_root: Path, mode: str, *, capture: bool = False
) -> CheckResult:
    """Run a single check, filtering to staged files in ``staged`` mode.

    A pass is recorded as an execution of the check (:func:`usage.record_verify_check`),
    which is the evidence :func:`basicly.release.unexercised_capabilities` reads before
    a tag. This is the only component that can produce it: a check is never typed at a
    shell, so the ``tool-usage`` hook cannot see one, and the release gate was refusing
    a tag over checks it had just watched pass (basicly-3yi3).

    Only a pass. The two ways a declared capability demonstrably did *not* run — the
    command is not on PATH (127) and it is not executable (126) — both surface as a
    ``fail`` here, so crediting a failure would witness exactly the case the gate
    exists to catch; a skip ran nothing at all. Recorded here rather than in
    :func:`run_verify` so a re-run counts too, and not in :func:`_run`, which also
    serves ``run_fix`` — a fixer passing says nothing about the check.
    """
    result = _run(check, list(check.command), repo_root, mode, capture=capture)
    if result.status == "pass":
        usage.record_verify_check(repo_root, check.name)
    return result


def run_fix(check: VerifyCheck, repo_root: Path, mode: str) -> CheckResult:
    """Apply *check*'s mechanical repair; skip when it declares no ``fix_command``.

    A fix is never a verdict: the check that follows is what decides pass/fail,
    so a failing fixer is reported (status ``fail``) but does not stand in for
    the gate.
    """
    if not check.fix_command:
        return CheckResult(check.name, "skip", 0)
    return _run(check, list(check.fix_command), repo_root, mode)


def _run(
    check: VerifyCheck,
    command: list[str],
    repo_root: Path,
    mode: str,
    *,
    capture: bool = False,
) -> CheckResult:
    """Run one check-or-fix command, filtering to staged files in ``staged`` mode.

    *command* is passed separately because ``run_fix`` runs the check's
    ``fix_command`` rather than its ``command``; everything else comes off *check*.

    ``capture`` diverts the command's output into ``CheckResult.output`` instead of
    the terminal. Off by default and only ever set for the diagnostic re-run, so
    an operator watching a long gate still sees it stream.
    """
    name = check.name
    if mode == "staged" and check.staged_suffix:
        files = staged_files(repo_root, check.staged_suffix)
        if files is None:
            return CheckResult(
                name,
                "fail",
                1,
                "git diff --cached failed — cannot determine staged files, "
                "refusing to skip the check",
            )
        if not files:
            return CheckResult(name, "skip", 0)
        command += files
    try:
        proc = subprocess.run(  # nosec B603
            command,
            cwd=repo_root,
            check=False,
            capture_output=capture,
            text=capture or None,
        )
    except FileNotFoundError:
        return CheckResult(
            name,
            "fail",
            127,
            f"command not found: {command[0]} — install it or edit "
            f"[[verify.checks]] in basicly.toml",
        )
    except OSError as exc:
        # e.g. PermissionError: a PATH candidate exists but is not executable
        # (common on WSL with Windows mounts on PATH). Same contract: a failed
        # check with a one-line reason, never a traceback.
        return CheckResult(
            name,
            "fail",
            126,
            f"cannot run {command[0]} ({exc.strerror or exc}) — check "
            f"[[verify.checks]] in basicly.toml",
        )
    output = f"{proc.stdout or ''}{proc.stderr or ''}" if capture else ""
    return CheckResult(
        name, "pass" if proc.returncode == 0 else "fail", proc.returncode, output=output
    )


def run_verify(repo_root: Path, mode: str, config: VerifyConfig | None = None) -> VerifyReport:
    """Run every check configured for *mode* and collect the results."""
    config = config or load_verify_config(repo_root)
    results = tuple(run_check(check, repo_root, mode) for check in config.for_mode(mode))
    return VerifyReport(mode=mode, results=results)


def rerun_failures(
    report: VerifyReport,
    repo_root: Path,
    mode: str,
    config: VerifyConfig | None = None,
    *,
    capture: bool = False,
) -> VerifyReport:
    """Re-run just the checks that failed in *report*, unchanged.

    Evidence, not a retry. Nothing in the tree, the config, or the command line
    differs between the two runs, so a check that passes now did not fail on the
    work under test — which is what lets a caller tell an unreliable gate from a
    merit failure instead of scoring both the same (basicly-55yh).

    Only the failed checks re-run, and the whole check re-runs rather than the
    one case inside it that failed: narrowing to a single test would change the
    run, and an order-dependent test that passes alone would then be excused as a
    flake when it is a real defect.

    Fail-safe by construction. A green *report* is returned unchanged, and so is
    one whose failures no longer match a configured check — no new evidence means
    the original verdict stands. A caller may only forgive on a positive pass
    here, never on the absence of a result.

    ``capture`` collects the re-run's output so a caller can also ask
    :func:`dependency_defect` about a failure that *did* reproduce
    (basicly-kjc5.56).
    """
    failed = set(report.failures)
    if not failed:
        return report
    config = config or load_verify_config(repo_root)
    checks = [check for check in config.for_mode(mode) if check.name in failed]
    if not checks:
        return report
    return VerifyReport(
        mode=mode,
        results=tuple(run_check(c, repo_root, mode, capture=capture) for c in checks),
    )


# Failure signatures a dependency emits and the work under test cannot cause
# (basicly-kjc5.56). Each entry is (substrings that must all appear on ONE line,
# why forgiving it is safe). An entry earns its place only on proof that no change
# to this repo can produce it — otherwise this launders a real failure, which is
# worse than the flake it excuses. Keep it short and keep the reason with it.
#
# Matching is per-line and conjunctive because the defect phrase alone is not
# enough. Requiring our own ``br.py`` wrapper text on the same line proves the
# failure came out of a br subprocess rather than out of a test's own fixture.
# It also spans both shapes br uses for one defect — "Validation failed: <field>:
# <msg>" and "Validation errors: [ValidationError { field: .., message: .. }]" —
# which a literal match on either prose form does not (learned the hard way: the
# first version of this register held only the singular form and a landing
# reproduced only the plural one).
#
# The lock entry is anchored on ``write lock at`` rather than on the whole timeout
# sentence for the same reason: that phrase is common to every wording br uses when
# it cannot take the lock, and the observed one ("Timed out after 400ms waiting for
# write lock at <path>/.beads/.write.lock", reproduced 2026-08-05 by holding the
# lock against br 0.2.16) is only the shape this repo has seen so far.
DEPENDENCY_DEFECT_SIGNATURES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("RuntimeError: br ", "failed:", "cannot be before created_at"),
        "br rejects its own write when the host clock steps backwards between two "
        "writes; nothing in this repo sets either timestamp (basicly-vkh0.6 carries "
        "it as a requirement on the replacement)",
    ),
    (
        ("RuntimeError: br ", "failed:", "write lock at"),
        "br serialises every mutating command behind one .beads/.write.lock and "
        "fails the command outright when it cannot take it before the timeout, so a "
        "gate contends with whatever else drives the tracker at that moment; no diff "
        "can make that contention its own fault (R8 in docs/design/work-tracker.md)",
    ),
)


def _defect_reason(output: str) -> str | None:
    """The register reason matching any single line of *output*, else None."""
    for line in output.splitlines():
        for substrings, reason in DEPENDENCY_DEFECT_SIGNATURES:
            if all(s in line for s in substrings):
                return reason
    return None


def dependency_defect(report: VerifyReport) -> str | None:
    """The reason *every* failure in *report* is a known dependency defect, else None.

    Matched on captured output, so it answers only for a report produced with
    ``capture=True``; a streamed report has no text and yields None, which keeps
    the original verdict standing rather than forgiving on absent evidence.

    This exists because the re-run test alone cannot see this class of defect. A
    backwards clock step persists for a window, so the failure reproduces and
    scores as a merit failure — measured on basicly-m4zv.9, where a landing spent
    a rework attempt on it (basicly-55yh shipped the re-run; this closes the gap).

    **Every** failing check must be explained, not merely one: a run that mixes a
    dependency defect with a real failure is a real failure. Granularity is the
    check, though, so a single check whose output holds both is still forgiven —
    the honest bound on this mechanism. What keeps that bound acceptable is that
    the caller's verdict *blocks* the landing rather than merging it, so a wrong
    forgive costs one more cycle and can never merge an unverified tree.
    """
    failures = [r for r in report.results if r.status == "fail"]
    if not failures:
        return None
    reasons: list[str] = []
    for result in failures:
        reason = _defect_reason(result.output) if result.output else None
        if reason is None:
            return None
        reasons.append(f"{result.name}: {reason}")
    return "; ".join(reasons)


def apply_fixes(repo_root: Path, mode: str, config: VerifyConfig | None = None) -> VerifyReport:
    """Apply the ``fix_command`` of every *mode* check that declares one.

    Deliberately separate from ``run_verify``: a plain verify run stays a pure
    verdict, so CI still fails on unformatted input from outside the harness. The
    fix step is opt-in (``basicly verify --fix``, the pre-commit hook) and its
    results are advisory — the checks that follow produce the gate.
    """
    config = config or load_verify_config(repo_root)
    results = tuple(run_fix(check, repo_root, mode) for check in config.for_mode(mode))
    return VerifyReport(mode=mode, results=results)


def report_gate(
    repo_root: Path,
    issue_id: str,
    report: VerifyReport,
    gate: str = DEFAULT_GATE,
    *,
    actor: str | None = None,
) -> tuple[bool, str]:
    """Record the verdict on *issue_id* via ``br gate report``.

    When the dispatched runner is known (basicly-140a), *actor* is recorded as the
    gate's audit-trail actor, so a gate result ties to the agent that produced it.
    It is optional — a gate reported outside a dispatch records no actor. (Model
    provenance rides the landing commit trailer, not the gate's free-text note.)

    Returns ``(ok, message)``; degrades gracefully (returns ``False`` with
    guidance) when ``br`` is not on PATH or the command fails, rather than
    raising, so a missing tracker never masks the verify result itself.
    """
    status = "pass" if report.passed else "fail"
    detail = ", ".join(f"{r.name}={r.status}" for r in report.results) or "no checks"
    note = f"verify {report.mode}: {detail}"
    args = [
        "gate",
        "report",
        "--gate",
        gate,
        "--provider",
        GATE_PROVIDER,
        "--status",
        status,
        "--note",
        note,
    ]
    if actor:
        args += ["--actor", actor]
    args.append(issue_id)
    proc = br.try_run_br(repo_root, args)
    if proc is None:
        return False, "br not on PATH; gate not recorded"
    if proc.returncode != 0:
        return False, f"br gate report failed: {(proc.stderr or proc.stdout).strip()}"
    return True, f"recorded gate {gate}={status} on {issue_id}"
