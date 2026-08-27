"""Where a checkout stands in git's worktree layout, and where its siblings are.

One responsibility: answering positional questions about a working tree — which
git common dir it shares, which checkout is the primary one, where the sibling
``<repo>.worktrees`` directory sits, what branch is out, whether this path is a
linked worktree, and which worktrees git currently tracks. Every answer is a
``git rev-parse``/``worktree list`` reading, so :func:`run` and :func:`git` live
here as the single spelling those readings go through rather than as a second
module nothing else would ever import.

Why the answers need a home of their own: they are asked from all over the
engine, by callers that have nothing to do with provisioning a worktree.
``release`` refuses to publish from a linked checkout, ``loop`` refuses its
merge/ship transitions there, ``commit`` resolves the primary tree to find the
tracker, and ``merge`` needs the current branch — none of which is the
create/reclaim lifecycle.

The one non-obvious rule is that ``git rev-parse`` may answer with a path
relative to *cwd*, so every path here is resolved against the *cwd* it was asked
about before being returned; comparing an unresolved answer to an absolute path
is how :func:`is_linked_checkout` would silently report every checkout as linked.

Split out of ``worktree`` when the module-size ratchet caught that module
growing. The boundary is *where* against *lifecycle*: nothing here creates,
provisions, records or removes anything, which is all :mod:`basicly.worktree`
does, and that is why this module needs no import back into it.

The second half of the file is :func:`run`'s error path — reading a refused
command's own output for the check that refused it. Every gated git command in
the engine goes through :func:`run`, and the layering leaves no lower home: every
module below this one is a leaf with nothing to do with hooks.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# pre-commit's `no_git_env` allowlist, minus the `GIT_CONFIG_*` trio it forwards to hooks.
GIT_ENV_KEPT = frozenset({
    "GIT_ALLOW_PROTOCOL",
    "GIT_ASKPASS",
    "GIT_EXEC_PATH",
    "GIT_HTTP_PROXY_AUTHMETHOD",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_SSL_CAINFO",
    "GIT_SSL_NO_VERIFY",
})


def sanitised_git_env(env: Mapping[str, str]) -> dict[str, str]:
    """*env* without the inherited `GIT_*` that would outrank a caller's *cwd*.

    Git reads `GIT_DIR` before *cwd*, and exports it into every hook it runs from a
    checkout whose git dir is not a plain `<worktree>/.git` — every lane worktree. A
    harness running from a hook therefore aims every call below at the wrong repository
    unless it is dropped here (basicly-e2mz.16).
    """
    return {
        name: value
        for name, value in env.items()
        if not name.startswith("GIT_") or name in GIT_ENV_KEPT
    }


# An operator's terminal preference, which a dispatched lane must never inherit: rich
# reads these when it builds a Console, so a forced value fails a verify run on ANSI
# escapes in code that has nothing to do with colour (basicly-e2mz.34). `NO_COLOR` is
# deliberately absent — it only ever moves output toward the plain form gates assert.
COLOUR_ENV_FORCING = frozenset({"FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "COLORTERM"})


def sanitised_colour_env(env: Mapping[str, str]) -> dict[str, str]:
    """*env* without the colour-forcing variables a developer's shell may carry."""
    return {name: value for name, value in env.items() if name not in COLOUR_ENV_FORCING}


def run(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with explicit utf-8 decoding (Windows defaults to cp1252).

    *env* replaces the child's environment wholesale when given (the release
    regeneration needs PYTHONPATH pointed at the repo being released); omitting it
    inherits this process's, which is what every other caller wants. Either way
    :func:`sanitised_git_env` runs on it, so *cwd* decides the repository.
    """
    # Validating `args` here — the alternative to the suppression — would duplicate the
    # naming rules each caller already enforces, and `shell=False` already makes a branch
    # or worktree name one argv element rather than shell text.
    proc = subprocess.run(  # noqa: S603 — argv list built by this module, no shell
        args,
        cwd=cwd,
        env=sanitised_git_env(os.environ if env is None else env),
        check=False,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(_failure(args, proc, cwd))
    return proc


def _failure(
    args: list[str],
    proc: subprocess.CompletedProcess[str],
    cwd: Path | str | None,
) -> str:
    """Why *proc* failed, naming the check when a hook is what refused.

    Both streams are joined rather than preferred: pre-commit reports the chain on stdout
    while git and `uv` warn on stderr, so the `stderr or stdout` this replaces discarded the
    whole report whenever anything had warned — and a `uv` `VIRTUAL_ENV` warning was the only
    text three lane closes reported on 2026-08-21 (basicly-fi1i7z). A failure with no chain
    in it keeps the old wording; `rev-parse` has no check to name.
    """
    output = f"{proc.stdout or ''}{proc.stderr or ''}"
    argv = " ".join(map(str, args))
    named = gate_refusal(output, repo_root=Path(cwd) if cwd is not None else Path.cwd())
    if named is not None:
        return f"a gate refused `{argv}`: {named}"
    return f"command failed ({proc.returncode}): {argv}\n{output.strip()}"


def git(
    args: list[str], *, cwd: Path | str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` with the shared utf-8 subprocess wrapper."""
    return run(["git", *args], cwd=cwd, check=check)


def names_in(ref: str, directory: str, cwd: Path | str | None = None) -> tuple[str, ...]:
    """File names *ref* holds under *directory*, or empty where the question cannot be asked.

    Empty covers no git, no such ref and no such directory alike, because a caller asking
    what another branch holds has no answer in any of the three and a raise would make an
    absent remote fatal.
    """
    done = git(["ls-tree", "--name-only", f"{ref}:{directory}"], cwd=cwd, check=False)
    if done.returncode != 0:
        return ()
    return tuple(line for line in done.stdout.splitlines() if line)


def git_common_dir(cwd: Path | str | None = None) -> Path:
    """Return the shared git common dir (``<main>/.git`` for the main checkout)."""
    out = git(["rev-parse", "--git-common-dir"], cwd=cwd).stdout.strip()
    path = Path(out)
    if not path.is_absolute():
        path = Path(cwd or Path.cwd()) / path
    return path.resolve()


def main_checkout(cwd: Path | str | None = None) -> Path:
    """Return the primary working tree (parent of the git common dir)."""
    return git_common_dir(cwd).parent


def worktrees_root(cwd: Path | str | None = None) -> Path:
    """Return the sibling ``<repo>.worktrees`` directory that holds worktrees."""
    main = main_checkout(cwd)
    return main.parent / f"{main.name}.worktrees"


def current_branch(cwd: Path | str | None = None) -> str:
    """Return the checked-out branch name for *cwd*."""
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).stdout.strip()


def is_linked_checkout(cwd: Path | str | None = None) -> bool:
    """True when *cwd* is inside a linked worktree rather than the primary checkout.

    A linked worktree has its own per-worktree git dir under
    ``<common>/worktrees/<name>``; the primary checkout's git dir *is* the common
    dir. Comparing the two is git's own definition of "am I in a linked worktree",
    which the loop uses to refuse merge/ship transitions that must run from base.
    Returns ``False`` when *cwd* is not a git repository (nothing to refuse).
    """
    proc = git(["rev-parse", "--git-dir"], cwd=cwd, check=False)
    if proc.returncode != 0:
        return False
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = Path(cwd or Path.cwd()) / git_dir
    return git_dir.resolve() != git_common_dir(cwd)


def registered_worktrees(cwd: Path | str | None = None) -> dict[Path, str | None]:
    """Return ``{path: branch}`` for every worktree git currently tracks.

    Branch is ``None`` for a detached-HEAD worktree. Used to resolve and to
    reconcile against session records.
    """
    out: dict[Path, str | None] = {}
    porcelain = git(["worktree", "list", "--porcelain"], cwd=cwd).stdout
    path: Path | None = None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :].strip())
            out[path] = None
        elif line.startswith("branch ") and path is not None:
            out[path] = line[len("branch ") :].strip().removeprefix("refs/heads/")
    return out


# --- What refused, read off the output a gated command left behind ----------
#
# Three code paths reported a hook refusal on 2026-08-21 and none named a check: the
# salvage commit quoted `protect-generated-commit...Passed` as its rejection reason, and
# two lane closes printed only the argv and the exit code. One shape in three places — the
# output holding the answer was summarised by position (its last line) or by stream
# (`stderr or stdout`) instead of by structure (basicly-fi1i7z).
#
# The shape below was observed, not composed: pre-commit's per-hook verdict line and block,
# built in `pre_commit/commands/run.py` as `f"{name}{dots}{postfix}{verdict}"`. A parser
# whose fixtures are composed rather than observed is how the defect it replaces was
# written. `verify.check_remedy` reads the other shape, a ratchet gate's labelled lines.
_VERDICTS = ("Failed", "Passed", "Skipped")
_NO_FILES = "(no files to check)"

# Annotations that restate what the message already carries.
_ANNOTATIONS = ("- hook id:", "- duration:", "- exit code:")

# A formatter that rewrote a file fails with this line and often no output at all, so
# dropping it would leave nothing to report.
_MODIFIED = "- files were modified by this hook"

# A check stating its own verdict, in the vocabulary this repo's gates use.
_STATED = ("FAILED:", "checks failed:", "BROKEN")

# `.basicly/core/hooks/check_runner.py`'s own two verdict shapes. Read off the whole output
# and *appended* to the block reader's answer rather than selected by it, because a line
# reaches that answer only if it survives both the per-block boundary and `_REASON_LINES`:
# a refusal reporting bandit's `nosec` warning while `FAILED: release-notes` sat in the same
# output cost basicly-j7spdb a re-dispatch and stopped two green lanes (basicly-85cadb).
_RUNNER_VERDICTS = ("FAILED:", "checks failed:")

# Weaker evidence, for a third-party tool that prints no summary line.
_FAILURE_WORDS = ("error", "fail", "broken")

# The reason goes in a one-line block detail, so it stays a sentence; the transcript is
# what the dump file is for.
_REASON_LINES = 3
_REASON_CHARS = 400

# Under `.basicly/usage/` for the reason `verify_artifact.RUN_ARTIFACT` is: a landing
# refuses to merge while the checkout carries dirt outside the tracker, so a file a
# failing command rewrites must not be tracked.
GATE_OUTPUT_DUMP = Path(".basicly/usage/gate-output.txt")


@dataclass(frozen=True)
class Refusal:
    """One check that refused, and the reason it reported."""

    check: str
    reason: str

    def __str__(self) -> str:
        """The refusal as an operator reads it."""
        return f"`{self.check}` refused: {self.reason}"


def _verdict(line: str) -> tuple[str, str] | None:
    """``(name, verdict)`` when *line* is one of pre-commit's verdict lines, else None.

    The dots discriminate: a line merely *ending* in "Failed" is something a hook printed.
    A name long enough to leave no dots is unmatched, and falls to the admission below
    rather than being guessed at.
    """
    for verdict in _VERDICTS:
        head = line.removesuffix(verdict)
        if head == line:
            continue
        padded = head.removesuffix(_NO_FILES)
        name = padded.rstrip(".")
        if name and name != padded:
            return name, verdict
    return None


def _stated_lines(body: list[str]) -> list[str]:
    """The lines of *body* that claim a failure, by the strongest available evidence."""
    if stated := [line for line in body if any(mark in line for mark in _STATED)]:
        return stated
    return [line for line in body if any(word in line.lower() for word in _FAILURE_WORDS)] or body


def _reason(block: list[str]) -> str:
    """The reason a failing hook's *block* reports, capped to one sentence's worth.

    **Chosen by what a line claims, not by where it sits.** A tail was the first design and
    real output refuted it: this repo's `pre-commit-script` hook wraps the whole verify
    suite, so its block ends on a list of check names while the answer, `checks failed:
    28/32 passed ... (failed: ...)`, sits six lines earlier.

    The surviving annotation loses its bullet so it reads as a sentence, not a transcript.
    """
    body = [
        _MODIFIED.removeprefix("- ") if line.strip() == _MODIFIED else line
        for line in block
        if line.strip() and not line.startswith(_ANNOTATIONS)
    ]
    if not body:
        return "the hook reported no output"
    reason = " · ".join(line.strip() for line in _stated_lines(body)[-_REASON_LINES:])
    return reason if len(reason) <= _REASON_CHARS else reason[:_REASON_CHARS] + "…"


def ran_hooks(output: str) -> bool:
    """Whether *output* holds a pre-commit chain at all.

    The discriminator between "a hook refused" and "git itself refused": a failing
    ``git rev-parse`` has no check to name, and naming one would be the invention this
    reader exists to stop.
    """
    return any(_verdict(line) is not None for line in output.splitlines())


def _runner_verdicts(output: str) -> tuple[str, ...]:
    """Every :data:`_RUNNER_VERDICTS` line anywhere in *output*, deduplicated, in run order."""
    seen = [
        stripped
        for line in output.splitlines()
        if (stripped := line.strip()).startswith(_RUNNER_VERDICTS)
    ]
    return tuple(dict.fromkeys(seen))


def refusals(output: str) -> tuple[Refusal, ...]:
    """Every check in *output* that refused, in the order the chain ran them.

    A verdict other than ``Failed`` closes its block and contributes nothing, which is the
    whole guarantee that no passing verdict reaches a message.
    """
    found: list[Refusal] = []
    name = ""
    block: list[str] = []
    for line in output.splitlines():
        seen = _verdict(line)
        if seen is None:
            if name:
                block.append(line)
            continue
        if name:
            found.append(Refusal(name, _reason(block)))
        name, block = (seen[0] if seen[1] == "Failed" else ""), []
    if name:
        found.append(Refusal(name, _reason(block)))
    return tuple(found)


def _write_gate_output(repo_root: Path, output: str) -> Path | None:
    """Persist *output* to :data:`GATE_OUTPUT_DUMP`; its path, or None when unwritable.

    Never raises: an exception here would discard the refusal the caller is reporting, where
    a message naming no path is merely degraded. Pid-scoped temporary for the reason
    :func:`basicly.verify_artifact.write_run_artifact` gives.
    """
    path = Path(repo_root) / GATE_OUTPUT_DUMP
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(output, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        return None
    return path


def gate_refusal(output: str, *, repo_root: Path | None = None) -> str | None:
    """Name what refused in *output*, or None when no hook chain ran in it.

    None is "not my question" and is distinct from the string returned when a chain *did*
    run and named nothing: the first leaves the caller's wording alone, the second replaces
    it with an admission naming where the full output went.
    """
    if not ran_hooks(output):
        return None
    if refused := refusals(output):
        named = "; ".join(str(refusal) for refusal in refused)
        missed = [line for line in _runner_verdicts(output) if line not in named]
        return f"{named} · {' · '.join(missed)}" if missed else named
    written = _write_gate_output(repo_root, output) if repo_root is not None else None
    where = (
        f"the full output is in {GATE_OUTPUT_DUMP.as_posix()}" if written else "it was not captured"
    )
    return f"a hook refused but its output names no failing check; {where}"
