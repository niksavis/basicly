from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

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

    return {
        name: value
        for name, value in env.items()
        if not name.startswith("GIT_") or name in GIT_ENV_KEPT
    }


COLOUR_ENV_FORCING = frozenset({"FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "COLORTERM"})


def sanitised_colour_env(env: Mapping[str, str]) -> dict[str, str]:
    return {name: value for name, value in env.items() if name not in COLOUR_ENV_FORCING}


def run(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:

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

    output = f"{proc.stdout or ''}{proc.stderr or ''}"
    argv = " ".join(map(str, args))
    named = gate_refusal(output, repo_root=Path(cwd) if cwd is not None else Path.cwd())
    if named is not None:
        return f"a gate refused `{argv}`: {named}"
    return f"command failed ({proc.returncode}): {argv}\n{output.strip()}"


def git(
    args: list[str], *, cwd: Path | str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, check=check)


def names_in(ref: str, directory: str, cwd: Path | str | None = None) -> tuple[str, ...]:

    done = git(["ls-tree", "--name-only", f"{ref}:{directory}"], cwd=cwd, check=False)
    if done.returncode != 0:
        return ()
    return tuple(line for line in done.stdout.splitlines() if line)


def git_common_dir(cwd: Path | str | None = None) -> Path:
    out = git(["rev-parse", "--git-common-dir"], cwd=cwd).stdout.strip()
    path = Path(out)
    if not path.is_absolute():
        path = Path(cwd or Path.cwd()) / path
    return path.resolve()


def main_checkout(cwd: Path | str | None = None) -> Path:
    return git_common_dir(cwd).parent


def worktrees_root(cwd: Path | str | None = None) -> Path:
    main = main_checkout(cwd)
    return main.parent / f"{main.name}.worktrees"


def current_branch(cwd: Path | str | None = None) -> str:
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).stdout.strip()


def is_linked_checkout(cwd: Path | str | None = None) -> bool:

    proc = git(["rev-parse", "--git-dir"], cwd=cwd, check=False)
    if proc.returncode != 0:
        return False
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = Path(cwd or Path.cwd()) / git_dir
    return git_dir.resolve() != git_common_dir(cwd)


def registered_worktrees(cwd: Path | str | None = None) -> dict[Path, str | None]:

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


_VERDICTS = ("Failed", "Passed", "Skipped")
_NO_FILES = "(no files to check)"

_ANNOTATIONS = ("- hook id:", "- duration:", "- exit code:")

_MODIFIED = "- files were modified by this hook"

_STATED = ("FAILED:", "checks failed:", "BROKEN")

_RUNNER_VERDICTS = ("FAILED:", "checks failed:")

_FAILURE_WORDS = ("error", "fail", "broken")

_REASON_LINES = 3
_REASON_CHARS = 400

GATE_OUTPUT_DUMP = Path(".basicly/usage/gate-output.txt")


@dataclass(frozen=True)
class Refusal:
    check: str
    reason: str

    def __str__(self) -> str:
        return f"`{self.check}` refused: {self.reason}"


def _verdict(line: str) -> tuple[str, str] | None:

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
    if stated := [line for line in body if any(mark in line for mark in _STATED)]:
        return stated
    return [line for line in body if any(word in line.lower() for word in _FAILURE_WORDS)] or body


def _reason(block: list[str]) -> str:

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

    return any(_verdict(line) is not None for line in output.splitlines())


def _runner_verdicts(output: str) -> tuple[str, ...]:
    seen = [
        stripped
        for line in output.splitlines()
        if (stripped := line.strip()).startswith(_RUNNER_VERDICTS)
    ]
    return tuple(dict.fromkeys(seen))


def refusals(output: str) -> tuple[Refusal, ...]:

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
