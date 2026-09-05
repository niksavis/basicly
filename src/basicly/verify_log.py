"""What a failing check said, kept where the terminal cannot take it away.

A `pytest` gate failed once and the artifact held all that was known: *"output streamed
rather than captured"*. Six later runs were green, so its identity went with the terminal of
a run nobody was watching (basicly-zlqn7e).

**Not the run artifact.** :mod:`basicly.verify_artifact` refuses to hold output for two
reasons that still hold - the evidence gate opens nothing, and a tool's stdout can carry a
secret - so this is a sibling file that redacts on the way in.

**Not the diagnostic re-run.** :func:`basicly.verify.rerun_failures` captures, but runs after
the fact, so a flake passes there and leaves nothing.
"""

# comment-density-waiver: cohesion: 56.5% of 668 tokens. This module is one rule and the
# meaning of one rule: WHY a failing check's output lands beside the run artifact and not in
# it. `write` is nine statements; the two paragraphs above are the reasons a reader must have
# before moving it - `verify_artifact` refuses output because the evidence gate opens nothing
# and stdout can carry a secret, and the diagnostic re-run cannot capture a flake because it
# runs after the fact. Cut them and the next reader folds this into the artifact and
# reintroduces the hazard. Trimmed twice first, 71.4% -> 62.0% -> 56.5%.

from __future__ import annotations

from pathlib import Path

from . import redact

# The self-ignored directory the run records use: a file every verify run rewrites must not
# be tracked, or the landing refuses to merge on dirt outside the tracker.
LOG_DIR = Path(".basicly/usage")

# The last screens of a `pytest` run, where the `FAILED` lines are - not the whole
# transcript, which is megabytes.
TAIL_BYTES = 60_000


def log_path(repo_root: Path, check: str) -> Path:
    """Where *check*'s failing output is kept: one file per check, never per run.

    The reader's question is "what did the gate that just refused me say", and a per-run
    name is one they do not have.
    """
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in check)
    return repo_root / LOG_DIR / f"verify-fail-{safe or 'check'}.log"


def write(repo_root: Path, check: str, output: str) -> Path | None:
    """Keep *output*'s tail for a failed *check*; its path, or None where nothing was kept.

    Never raises, for `write_run_artifact`'s reason. Empty *output* answers None, so no
    reader is sent to a file that says nothing.
    """
    if not output.strip():
        return None
    kept = output[-TAIL_BYTES:]
    if len(output) > TAIL_BYTES:
        kept = f"[the first {len(output) - TAIL_BYTES} characters are not kept]\n{kept}"
    text = redact.redact_machine_identity(redact.redact_secrets(kept))
    path = log_path(repo_root, check)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return path


def pointer(path: Path | None, repo_root: Path) -> str:
    """The one line a failing check's ``detail`` carries, or "" where nothing was kept.

    Repo-relative: an absolute path here is a machine path in a file other people read.
    """
    if path is None:
        return ""
    try:
        named = path.relative_to(repo_root)
    except ValueError:
        named = path
    return f"what it said: {named.as_posix()}"
