from __future__ import annotations

import json
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from basicly import lefthook_config, release, release_history, shipped_core

QUIET_PREFIXES = (".basicly/ledger/",)
QUIET_FILES = frozenset({"CHANGELOG.md"})
MANIFEST = Path(".basicly/generated-manifest.json")
WORKFLOW = "quality-gates.yml"
LIMIT = 30


def _git(root: Path, *args: str) -> str:
    return subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def _quiet(root: Path, sha: str) -> bool:
    parents = _git(root, "rev-list", "--parents", "-n", "1", sha).split()
    if len(parents) != 2:
        return False
    return all(
        name in QUIET_FILES or name.startswith(QUIET_PREFIXES) for name in _changed(root, sha)
    )


def _changed(root: Path, sha: str) -> list[str]:
    return _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()


def release_files(root: Path) -> frozenset[str]:
    try:
        outputs = json.loads((root / MANIFEST).read_text(encoding="utf-8")).get("outputs", {})
    except OSError, ValueError:
        outputs = {}
    fixed = (
        release.VERSION_FILE,
        release.KIT_VERSION_FILE,
        release.CHANGELOG_FILE,
        release_history.SOURCE_FILE,
        shipped_core.SOURCE_DIGESTS_FILE,
        MANIFEST,
        Path(lefthook_config.MANAGED_CONFIG),
        *release.pin_paths(root),
    )
    return frozenset({path.as_posix() for path in fixed} | set(outputs))


def _stray(root: Path, sha: str) -> list[str]:
    known = release_files(root)
    fragments = f"{release.FRAGMENT_DIR.as_posix()}/"
    return [
        name for name in _changed(root, sha) if name not in known and not name.startswith(fragments)
    ]


def green_in_ci(sha: str) -> bool:
    done = subprocess.run(  # nosec B603 B607
        ["gh", "run", "list", "--workflow", WORKFLOW, "--commit", sha, "--json", "conclusion"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        return False
    return any(run.get("conclusion") == "success" for run in json.loads(done.stdout or "[]"))


def reusable(root: Path, tag: str, green: Callable[[str], bool] = green_in_ci) -> tuple[bool, str]:
    head = _git(root, "rev-parse", f"{tag}^{{commit}}").strip()
    if _git(root, "log", "-1", "--format=%s", head).strip() != release.COMMIT_SUBJECT:
        return False, f"{tag} does not point at a release commit"
    stray = _stray(root, head)
    if stray:
        return False, f"the release commit changes files outside the release set: {stray[:5]}"
    history = _git(root, "rev-list", "--first-parent", f"--max-count={LIMIT}", f"{head}^").split()
    for sha in history:
        if green(sha):
            return True, (
                f"{sha[:12]} passed {WORKFLOW}, and every later commit is the release commit "
                "or touches only the ledger or CHANGELOG.md"
            )
        if not _quiet(root, sha):
            return False, f"{sha[:12]} changes more than the ledger and has no passing {WORKFLOW}"
    return False, f"no commit within {LIMIT} of {tag} passed {WORKFLOW}"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: release_reuse.py <tag>", file=sys.stderr)
        return 2
    reuse, reason = reusable(Path.cwd(), args[0])
    print(
        f"release-reuse: {'reuse' if reuse else 'run the full checks'}: {reason}", file=sys.stderr
    )
    print(f"reuse={'true' if reuse else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
