from __future__ import annotations

import json
import os
import shutil
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
        repo, {"PATH": "/usr/bin:/bin"}, fake_run, records, consumer_proof.Scope(tmp_path / "work")
    )


def test_a_passing_proof_records_head(tmp_path: Path) -> None:
    repo = workspace(tmp_path)

    result = prove(repo, tmp_path)

    head = git(repo, "rev-parse", "HEAD")
    assert result["passed"] is True
    assert result["commit"] == head
    assert git(tmp_path / "work" / "app", "log", "-1", "--format=%s").endswith("(app-1)")
    stored = json.loads(consumer_proof.result_path(repo, head).read_text(encoding="utf-8"))
    assert stored["v"] == consumer_proof.FORMAT


def test_a_failing_ci_command_fails_the_proof_and_names_the_consumer(tmp_path: Path) -> None:
    repo = workspace(tmp_path, '[["true"], ["false"]]')

    result = prove(repo, tmp_path)

    assert result["passed"] is False
    assert consumer_proof.failure(consumer_proof.consumer_results(result)[0]) == "step ci exited 1"


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
    with pytest.raises(consumer_proof.ProofError, match="--only names no consumer web"):
        consumer_proof.run_proof(
            repo, {}, fake_run, held, consumer_proof.Scope(tmp_path / "w2", only=("web",))
        )


def test_a_failed_wheel_build_names_its_log_and_prints_its_end(tmp_path: Path) -> None:
    repo = workspace(tmp_path)

    def failing(argv: list[str], cwd: Path, env: dict[str, str]) -> int:
        if argv[:2] == ["uv", "build"]:
            (cwd.parent / f"{cwd.name}.log").write_text(
                "x\nlefthook artifacts are missing\n", "utf-8"
            )
            return 1
        return fake_run(argv, cwd, env)

    with pytest.raises(consumer_proof.ProofError) as refused:
        consumer_proof.run_proof(repo, {}, failing, held, consumer_proof.Scope(tmp_path / "work"))

    assert f"see {tmp_path / 'tool.log'}" in str(refused.value)
    assert str(refused.value).endswith("lefthook artifacts are missing")


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


def test_consumers_run_in_parallel_each_on_its_own_core_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init(tmp_path / "web")
    repo = workspace(tmp_path)
    (repo / "basicly.toml").write_text(
        '[[release.consumers]]\nname = "app"\nci = [["true"]]\n\n'
        '[[release.consumers]]\nname = "web"\nci = [["true"]]\n',
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: two consumers")
    monkeypatch.setattr(consumer_proof.shutil, "which", lambda _name: "/usr/bin/taskset")
    seen: list[list[str]] = []

    def recording(argv: list[str], cwd: Path, env: dict[str, str]) -> int:
        seen.append(argv)
        return fake_run(argv[3:] if argv[0] == "taskset" else argv, cwd, env)

    result = consumer_proof.run_proof(
        repo,
        {"PATH": "/usr/bin:/bin", consumer_proof.CPUS_ENV: "16-21,22-26"},
        recording,
        held,
        consumer_proof.Scope(tmp_path / "work"),
    )

    assert result["passed"] is True
    pinned = {tuple(argv[:3]) for argv in seen if argv[0] == "taskset"}
    assert pinned == {("taskset", "-c", "16-21"), ("taskset", "-c", "22-26")}


def test_a_wrong_core_set_is_refused_by_name() -> None:
    with pytest.raises(consumer_proof.ProofError, match="core sets like 16-21"):
        consumer_proof.cpu_sets({consumer_proof.CPUS_ENV: "16-21,all"})


def test_a_throwaway_vault_gives_the_proof_a_held_record_and_no_shared_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = init(tmp_path / "app")
    git(app, "config", "basicly.holder", HOLDER)
    kit = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "kit" / "tracker"
    shutil.copytree(
        kit,
        app / ".basicly" / "core" / "kit" / "tracker",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (app / ".basicly" / "ledger").mkdir()
    (app / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (app / consumer_proof.VAULT_CONFIG).write_text(
        'vault = "https://example.invalid/v"\nprefix = "app"\n', "utf-8"
    )
    git(app, "add", "-A")
    git(app, "commit", "-q", "-m", "chore: use a vault")
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "state"))
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    session = consumer_proof.throwaway_vault(app, env)
    found, why = consumer_proof.vault_record(app, session, consumer_proof.vault_held)

    url = consumer_proof.vault_url(app)
    assert url == (tmp_path / "app-vault.git").resolve().as_uri()
    assert session[consumer_proof.SESSION_ENV] == consumer_proof.THROWAWAY_SESSION
    assert why == "" and isinstance(found, str) and found.startswith("app-")
    assert git(app, "status", "--porcelain") == ""


def vault_consumer(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / consumer_proof.VAULT_CONFIG).write_text(
        'vault = "https://example.invalid/v"\nprefix = "app"\n', "utf-8"
    )
    git(app, "add", "-A")
    git(app, "commit", "-q", "-m", "chore: use a vault")


@pytest.mark.parametrize(
    ("judged", "record", "said"),
    [
        ({"session": "s1", "filled": "app-7"}, "app-7", ""),
        ({"session": "s1", "held": ["app-8", "app-9"]}, "app-8", ""),
        ({"session": "s1", "filled": "tool-3", "held": []}, None, "this session holds no open"),
        ({"session": "s1", "held": ["tool-3", "app-4"]}, "app-4", ""),
        ({"session": "s1", "held": []}, None, "this session holds no open record"),
        ({"session": ""}, None, "no tracker session"),
    ],
)
def test_a_vault_consumer_names_only_a_record_this_session_holds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    judged: dict[str, object],
    record: str | None,
    said: str,
) -> None:
    repo = workspace(tmp_path)
    vault_consumer(tmp_path)
    monkeypatch.setattr(consumer_proof, "vault_held", lambda _c, _e, _u: judged)

    result = prove(repo, tmp_path, held)

    item = consumer_proof.consumer_results(result)[0]
    subject = git(tmp_path / "work" / "app", "log", "-1", "--format=%s")
    if record:
        assert result["passed"] is True
        assert subject.endswith(f"({record})")
    else:
        assert result["passed"] is False
        assert said in consumer_proof.failure(item)
        assert "claim RECORD --vault https://example.invalid/v" in consumer_proof.failure(item)
        assert not subject.startswith("chore(basicly): install the kit")
