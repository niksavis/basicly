from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

from basicly import __version__, shipped_core, state

REPO_ROOT = Path(__file__).resolve().parent.parent
CROSS_CHECKED_RELEASE = "v0.12.2"


def _git(repo: Path, *args: str) -> str:

    return subprocess.run(  # nosec B603 B607
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _release(repo: Path, tag: str, rules: str) -> None:

    rule_file = repo / shipped_core.CORE_TREE / "fragments" / "rules.yaml"
    rule_file.parent.mkdir(parents=True, exist_ok=True)
    rule_file.write_text(rules, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"release {tag}")
    _git(repo, "tag", tag)


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def test_the_shipped_hash_list_is_current_with_every_earlier_release_tag() -> None:
    committed = (REPO_ROOT / shipped_core.SOURCE_DIGESTS_FILE).read_text(encoding="utf-8")

    fresh = shipped_core.render_digests(shipped_core.build_digests(REPO_ROOT, __version__))

    assert committed == fresh, (
        f"{shipped_core.SOURCE_DIGESTS_FILE.as_posix()} is stale against the release tags; "
        "run `git fetch --tags` and then `uv run python -m basicly.shipped_core`, "
        "and commit the result"
    )


def test_every_file_an_earlier_release_shipped_is_in_the_list(tmp_path: Path) -> None:
    archive = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(REPO_ROOT), "archive", CROSS_CHECKED_RELEASE, shipped_core.CORE_TREE],
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tmp_path, filter="data")
    shipped = state.snapshot_core(tmp_path / shipped_core.CORE_TREE)
    listed = shipped_core.shipped_digests()

    missing = sorted(path for path, digest in shipped.items() if digest not in listed.get(path, ()))

    assert len(shipped) > 100
    assert missing == []


def test_the_list_holds_every_version_of_a_file_from_release_tags_older_than_the_version(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _release(tmp_path, "v0.1.0", "first\n")
    _release(tmp_path, "v0.2.0", "second\n")
    _release(tmp_path, "backup/v0.2.1", "backup\n")
    _release(tmp_path, "v0.3.0", "current\n")

    payload = shipped_core.build_digests(tmp_path, "0.3.0")

    assert payload["tags"] == ["v0.1.0", "v0.2.0"]
    assert payload["core"] == {
        "fragments/rules.yaml": sorted([_sha256("first\n"), _sha256("second\n")])
    }
