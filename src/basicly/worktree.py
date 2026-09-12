from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import checkout, tracker_paths
from .checkout import (
    current_branch,
    git,
    git_common_dir,
    main_checkout,
    registered_worktrees,
    run,
    worktrees_root,
)
from .hooks import PRECOMMIT_CONFIG, hook_stages, install_hooks, load_hook_specs

BRANCH_PREFIX = "harness/"

is_linked_checkout = checkout.is_linked_checkout

DEP_DIRS = (".venv", "node_modules")

NODE_LOCKFILE = "package-lock.json"


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat()


@dataclass
class Session:
    name: str
    branch: str
    base: str
    base_head: str
    worktree_path: str
    created_at: str

    @property
    def path(self) -> Path:
        return Path(self.worktree_path)

    @property
    def stale(self) -> bool:

        return not self.path.exists()


def sessions_dir(cwd: Path | str | None = None) -> Path:
    directory = git_common_dir(cwd) / "basicly-worktrees"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def session_file(name: str, cwd: Path | str | None = None) -> Path:
    return sessions_dir(cwd) / f"{name}.json"


def save_session(session: Session, cwd: Path | str | None = None) -> None:
    session_file(session.name, cwd).write_text(
        json.dumps(asdict(session), indent=2) + "\n", encoding="utf-8"
    )


def load_session(name: str, cwd: Path | str | None = None) -> Session | None:
    path = session_file(name, cwd)
    if not path.exists():
        return None
    return Session(**json.loads(path.read_text(encoding="utf-8")))


def list_sessions(cwd: Path | str | None = None) -> list[Session]:
    return [
        Session(**json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(sessions_dir(cwd).glob("*.json"))
    ]


def node_modules_donor(worktree: Path, donors: Sequence[Path]) -> Path | None:

    lock = worktree / NODE_LOCKFILE
    if not lock.exists():
        return None
    wanted = lock.read_bytes()
    for donor in donors:
        candidate = donor / NODE_LOCKFILE
        if (
            (donor / "node_modules").is_dir()
            and candidate.exists()
            and candidate.read_bytes() == wanted
        ):
            return donor
    return None


def provision_node_modules(worktree: Path, donors: Sequence[Path]) -> str:

    donor = node_modules_donor(worktree, donors)
    if donor is not None:
        try:
            shutil.copytree(donor / "node_modules", worktree / "node_modules", symlinks=True)
        except OSError as exc:
            shutil.rmtree(worktree / "node_modules", ignore_errors=True)
            run(["npm", "install"], cwd=worktree)
            return f"node_modules: npm install (copy from {donor.name} failed: {exc})"
        return f"node_modules: copied from {donor.name} (identical {NODE_LOCKFILE})"
    run(["npm", "install"], cwd=worktree)
    return "node_modules: npm install"


def provision_deps(worktree: Path, donors: Sequence[Path] = ()) -> list[str]:

    notes: list[str] = []
    if (worktree / "pyproject.toml").exists() or (worktree / "uv.lock").exists():
        run(["uv", "sync"], cwd=worktree)
        notes.append(".venv: uv sync")
    if (worktree / "package.json").exists():
        notes.append(provision_node_modules(worktree, donors))
    return notes


def install_worktree_hooks(worktree: Path) -> str:
    stages = hook_stages(load_hook_specs())
    if not stages:
        return "hooks: none defined"
    ok, message = install_hooks(worktree, stages)
    prefix = "hooks" if ok else "hooks (FAILED)"
    return f"{prefix}: {', '.join(stages)} — {message}"


def create(name: str, base: str | None = None, repo_root: Path | str | None = None) -> Session:

    base = base or current_branch(repo_root)
    branch = f"{BRANCH_PREFIX}{name}"
    worktree = worktrees_root(repo_root) / name

    if worktree.exists():
        raise SystemExit(f"worktree path already exists: {worktree}")
    if load_session(name, repo_root) is not None:
        raise SystemExit(f"a worktree named {name!r} already exists; clean it up first")

    base_head = git(["rev-parse", "--short", base], cwd=repo_root).stdout.strip()
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", str(worktree), "-b", branch, base], cwd=repo_root)

    notes: list[str] = []
    main = main_checkout(repo_root)
    if (main / tracker_paths.LEDGER_DIR_NAME).is_dir():
        target_ledger = worktree / tracker_paths.LEDGER_DIR_NAME
        target_ledger.mkdir(parents=True, exist_ok=True)
        (target_ledger / tracker_paths.REDIRECT_NAME).write_text(f"{main}\n", encoding="utf-8")
        notes.append(
            f"{(tracker_paths.LEDGER_DIR_NAME / tracker_paths.REDIRECT_NAME).as_posix()}: "
            f"tracker shared with the base checkout"
        )

    live = [session.path for session in list_sessions(repo_root) if not session.stale]
    notes += provision_deps(worktree, [main, *live])

    env_local = main / ".env.local"
    if env_local.exists():
        (worktree / ".env.local").write_text(
            env_local.read_text(encoding="utf-8"), encoding="utf-8"
        )
        notes.append(".env.local: copied")

    notes.append(install_worktree_hooks(worktree))

    session = Session(
        name=name,
        branch=branch,
        base=base,
        base_head=base_head,
        worktree_path=str(worktree),
        created_at=now_iso(),
    )
    save_session(session, repo_root)

    print(f"Created worktree {name!r}")
    print(f"  path:   {worktree}")
    print(f"  branch: {branch}  (base {base} @ {base_head})")
    for note in notes:
        print(f"  {note}")
    return session


def _resolve_worktree(
    name: str,
    main: Path,
    repo_root: Path | str | None = None,
    *,
    missing_ok: bool = False,
) -> tuple[Path, str | None] | None:

    session = load_session(name, repo_root)
    if session is not None:
        return session.path, session.branch

    target = Path(name)
    for path, branch in registered_worktrees(main).items():
        if path == target or path.name == name:
            return path, branch
    if missing_ok:
        return None
    raise SystemExit(
        f"no worktree named {name!r}: no session record and no registered worktree "
        f"matches it. Run `git worktree list` to see them."
    )


def stale_sessions(cwd: Path | str | None = None) -> list[Session]:

    return [session for session in list_sessions(cwd) if session.stale]


def unlanded_paths(main: Path, base: str, branch: str) -> tuple[str, ...] | None:

    fork = git(["merge-base", base, branch], cwd=main, check=False)
    if fork.returncode != 0 or not fork.stdout.strip():
        return None
    touched = git(["diff", "--name-only", fork.stdout.strip(), branch], cwd=main, check=False)
    against = git(["diff", "--name-only", branch, base], cwd=main, check=False)
    if touched.returncode != 0 or against.returncode != 0:
        return None
    changed = {line for line in touched.stdout.splitlines() if line.strip()}
    differs = {line for line in against.stdout.splitlines() if line.strip()}
    return tuple(sorted(changed & differs))


def _kept_for_content(main: Path, branch: str, base: str | None, detail: str) -> str:

    if base is None:
        return f"no session record names its base, so its content cannot be compared: {detail}"
    missing = unlanded_paths(main, base, branch)
    if missing is None:
        return f"its content could not be compared with {base}: {detail}"
    if missing:
        return (
            f"{base} does not hold {len(missing)} path(s) it changed, so this work is not "
            f"landed and force would discard it: {', '.join(missing)}"
        )
    purged = git(["branch", "-D", branch], cwd=main, check=False)
    if purged.returncode == 0:
        return ""
    return (
        f"{base} holds every path it changed, but git refused to delete it: "
        f"{(purged.stderr or purged.stdout).strip()}"
    )


def _reclaim_branch(main: Path, branch: str, base: str | None, *, force: bool) -> str:

    deleted = git(["branch", "-D" if force else "-d", branch], cwd=main, check=False)
    if deleted.returncode == 0:
        return ""
    absent = git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=main, check=False)
    if absent.returncode == 1:
        return ""
    detail = (deleted.stderr or deleted.stdout).strip()
    if force:
        return f"git refused to delete it: {detail}"
    return _kept_for_content(main, branch, base, detail)


def cap_refusal(concurrency: int, cwd: Path | str | None = None) -> str:

    sessions = list_sessions(cwd)
    stale = [session for session in sessions if session.stale]
    live = len(sessions) - len(stale)
    if live < concurrency:
        return ""
    refusal = (
        f"worktree concurrency cap reached ({live}/{concurrency} live); clean up a "
        "worktree or raise [worktree].concurrency in basicly.toml"
    )
    if not stale:
        return refusal
    names = ", ".join(sorted(session.name for session in stale))
    return (
        f"{refusal}. {len(stale)} record(s) whose checkout is already gone hold no slot "
        f"but are still listed — reclaim them with `basicly worktree cleanup <name> "
        f"--force`: {names}"
    )


@dataclass(frozen=True)
class RemovalVerdict:
    may_remove: bool
    holds: str
    indeterminate: bool = False


def classify_worktree_tree(returncode: int, stdout: str) -> RemovalVerdict:

    if returncode != 0:
        return RemovalVerdict(
            may_remove=False,
            holds=(
                f"git status could not be read in the worktree (exit {returncode}); "
                "refusing to remove a tree whose contents are unknown — a lock held by "
                "a concurrent lane is the usual cause, so retry, or pass force to "
                "discard the tree regardless"
            ),
            indeterminate=True,
        )
    expected_noise = (
        *DEP_DIRS,
        (tracker_paths.LEDGER_DIR_NAME / tracker_paths.REDIRECT_NAME).as_posix(),
    )
    noise_prefixes = tuple(f"{d}/" for d in expected_noise)
    pending: list[str] = []
    unparsable: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if len(line) < 4:
            unparsable.append(line)
            continue
        path = line[3:].strip().strip('"')
        if path in expected_noise or path.startswith(noise_prefixes):
            continue
        pending.append(line)
    if unparsable:
        return RemovalVerdict(
            may_remove=False,
            holds=(
                "git status returned a line this cannot parse, so the worktree's state "
                "is unknown; refusing to remove it:\n" + "\n".join(unparsable)
            ),
            indeterminate=True,
        )
    if pending:
        return RemovalVerdict(may_remove=False, holds="\n".join(pending))
    return RemovalVerdict(may_remove=True, holds="")


def _worktree_removal_verdict(worktree: Path) -> RemovalVerdict:

    try:
        proc = git(["status", "--porcelain"], cwd=worktree, check=False)
    except OSError, RuntimeError:
        return RemovalVerdict(
            may_remove=False,
            holds=(
                "git could not be run in the worktree, so its contents are unknown; "
                "refusing to remove it (pass force to discard the tree regardless)"
            ),
            indeterminate=True,
        )
    return classify_worktree_tree(proc.returncode, proc.stdout)


def cleanup(
    name: str,
    *,
    force: bool = False,
    repo_root: Path | str | None = None,
    missing_ok: bool = False,
) -> None:

    main = main_checkout(repo_root)
    resolved = _resolve_worktree(name, main, repo_root, missing_ok=missing_ok)
    if resolved is None:
        return
    worktree, branch = resolved

    if worktree.exists():
        verdict = _worktree_removal_verdict(worktree)
        if not verdict.may_remove and not force:
            if verdict.indeterminate:
                raise SystemExit(f"worktree {name!r} not removed: {verdict.holds}")
            raise SystemExit(
                f"worktree {name!r} has uncommitted changes; commit them or pass "
                f"force to discard:\n{verdict.holds}"
            )
        if verdict.indeterminate:
            print(f"  warning: forcing removal of {name!r} despite {verdict.holds}")
        git(["worktree", "remove", "--force", str(worktree)], cwd=main)
    git(["worktree", "prune"], cwd=main, check=False)

    if (main / PRECOMMIT_CONFIG).exists():
        print(f"  {install_worktree_hooks(main)}")

    record = load_session(name, main)
    kept = (
        _reclaim_branch(main, branch, record.base if record else None, force=force)
        if branch
        else ""
    )

    if not kept:
        session_file(name, main).unlink(missing_ok=True)
        print(f"Cleaned up worktree {name!r} (worktree + branch + metadata).")
    else:
        print(f"Removed worktree {name!r}; kept branch {branch} and its record ({kept}).")
