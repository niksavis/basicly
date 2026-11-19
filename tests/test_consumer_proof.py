from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from basicly import consumer_proof

HOLDER = "Proof Holder"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def init(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", HOLDER)
    git(path, "config", "user.email", "holder@example.invalid")
    (path / "README.md").write_text("x\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "chore: start")
    return path


def workspace(tmp_path: Path, ci: str = '[["true"]]') -> Path:
    init(tmp_path / "app")
    repo = init(tmp_path / "tool")
    (repo / "basicly.toml").write_text(
        f'[[release.consumers]]\nname = "app"\nci = {ci}\n', encoding="utf-8"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: name a consumer")
    return repo


def fake_run(argv: list[str], cwd: Path, env: dict[str, str]) -> int:
    if argv[:2] == ["uv", "build"]:
        out = Path(argv[argv.index("-o") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "tool-1.0.0-py3-none-any.whl").write_bytes(b"wheel")
        return 0
    if argv[0] == "uvx":
        (cwd / "installed.txt").write_text("kit\n", encoding="utf-8")
        return 0
    return consumer_proof.execute(argv, cwd, env)


def held(_clone: Path, _env: dict[str, str], _wheel: str) -> list[dict[str, object]]:
    return [{"record": "app-1", "fields": {"assignee": HOLDER}}]


def nobody(_clone: Path, _env: dict[str, str], _wheel: str) -> list[dict[str, object]]:
    return [{"record": "app-2", "fields": {"assignee": "someone else"}}]


def prove(repo: Path, tmp_path: Path, records: consumer_proof.Records = held) -> dict[str, object]:
    return consumer_proof.run_proof(
        repo, {"PATH": "/usr/bin:/bin"}, fake_run, records, tmp_path / "work"
    )


def test_a_passing_proof_records_head_and_clears_the_release_refusal(tmp_path: Path) -> None:
    repo = workspace(tmp_path)

    result = prove(repo, tmp_path)

    head = git(repo, "rev-parse", "HEAD")
    assert result["passed"] is True
    assert result["commit"] == head
    assert git(tmp_path / "work" / "app", "log", "-1", "--format=%s").endswith("(app-1)")
    stored = json.loads(consumer_proof.result_path(repo, head).read_text(encoding="utf-8"))
    assert stored["v"] == consumer_proof.FORMAT
    assert consumer_proof.refusal(repo) is None


def test_a_failing_ci_command_fails_the_proof_and_names_the_consumer(tmp_path: Path) -> None:
    repo = workspace(tmp_path, '[["true"], ["false"]]')

    result = prove(repo, tmp_path)

    assert result["passed"] is False
    refusal = consumer_proof.refusal(repo)
    assert refusal is not None and "failed for app" in refusal


def test_a_proof_of_an_older_commit_does_not_clear_the_refusal(tmp_path: Path) -> None:
    repo = workspace(tmp_path)
    prove(repo, tmp_path)
    (repo / "change.txt").write_text("y\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fix: later")

    refusal = consumer_proof.refusal(repo)

    head = git(repo, "rev-parse", "HEAD")[:12]
    assert refusal == f"no consumer proof for {head}; run: basicly consumer-proof"


def test_a_consumer_without_a_held_record_fails_before_its_commit(tmp_path: Path) -> None:
    repo = workspace(tmp_path)

    result = prove(repo, tmp_path, nobody)

    steps = consumer_proof.consumer_results(result)[0]["steps"]
    assert result["passed"] is False
    assert isinstance(steps, list) and steps[-1]["step"] == "record"


def test_a_dirty_tree_or_no_consumer_list_refuses_by_name(tmp_path: Path) -> None:
    repo = workspace(tmp_path)
    (repo / "dirt.txt").write_text("z\n", encoding="utf-8")
    with pytest.raises(consumer_proof.ProofError, match="not clean"):
        prove(repo, tmp_path)

    bare = init(tmp_path / "bare")
    with pytest.raises(consumer_proof.ProofError, match="names no"):
        prove(bare, tmp_path)
    assert consumer_proof.refusal(bare) is None


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ('name = "../x"\nci = []', "plain name"),
        ('name = "app"\nci = ["true"]', "list of argv lists"),
        ('name = "app"\nci = []\npath = "../up"', "path must be relative"),
    ],
)
def test_a_wrong_consumer_entry_is_refused_by_name(
    tmp_path: Path, entry: str, message: str
) -> None:
    repo = init(tmp_path / "tool")
    (repo / "basicly.toml").write_text(f"[[release.consumers]]\n{entry}\n", encoding="utf-8")

    with pytest.raises(consumer_proof.ProofError, match=message):
        consumer_proof.consumers(repo)


def test_this_repository_names_development_handily_and_terminal() -> None:
    root = Path(__file__).resolve().parents[1]

    names = [c.name for c in consumer_proof.consumers(root)]

    assert names == ["development", "handily", "terminal"]


def test_a_consumer_path_names_a_folder_under_the_root(tmp_path: Path) -> None:
    repo = init(tmp_path / "tool")
    (repo / "basicly.toml").write_text(
        '[[release.consumers]]\nname = "ws"\npath = "."\nci = []\n', encoding="utf-8"
    )

    listed = consumer_proof.consumers(repo)

    assert consumer_proof.source(repo, listed[0], {}) == tmp_path
    assert consumer_proof.source(repo, listed[0], {"BASICLY_CONSUMERS_ROOT": "/w"}) == Path("/w")
