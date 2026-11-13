from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / ".basicly/core/skills/agent-mail/scripts/agent_mail.py"
SKILL_TEXT = REPO / ".basicly/core/skills/agent-mail/skill.yaml"
SCRIPT = SOURCE
SCRIPTS = SOURCE.parent
MODULES = ("agent_mail.py", "mail_root.py", "mail_store.py")


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "mail_store_under_test", SCRIPT.parent / "mail_store.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_env(tmp_path: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "ORCH_MAIL_ROOT"}
    absent = str(tmp_path / "no-git-config")
    env.update({"GIT_CONFIG_GLOBAL": absent, "GIT_CONFIG_SYSTEM": absent})
    return env


def mail(
    cwd: Path, *args: str, body: str | None = None, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", "-B", str(SCRIPT), *args],
        cwd=cwd,
        input=body if body is not None else "",
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )


def git(cwd: Path, *args: str, env: dict[str, str]) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=env,
    )


def exchange(first: Path, second: Path, env: dict[str, str]) -> None:
    assert mail(first, "join", "--as", "alpha", env=env).returncode == 0
    assert mail(second, "join", "--as", "beta", env=env).returncode == 0
    sent = mail(first, "send", "alpha", "beta", "ping", body="from alpha", env=env)
    assert sent.returncode == 0, sent.stderr
    answer = mail(second, "send", "beta", "alpha", "pong", body="from beta", env=env)
    assert answer.returncode == 0, answer.stderr
    received = mail(second, "read", "beta", env=env)
    assert "from alpha" in received.stdout
    replied = mail(first, "read", "alpha", env=env)
    assert "from beta" in replied.stdout


def test_two_repositories_under_one_parent_share_one_mailbox(tmp_path: Path) -> None:
    env = clean_env(tmp_path)
    parent = tmp_path / "parent"
    for name in ("one", "two"):
        (parent / name / "src").mkdir(parents=True)
        git(parent / name, "init", "-q", env=env)
    created = mail(parent, "init", env=env)
    assert created.returncode == 0, created.stderr
    assert "not in a git repository" in created.stdout
    exchange(parent / "one" / "src", parent / "two", env)


def test_two_worktrees_of_one_repository_share_one_mailbox(tmp_path: Path) -> None:
    env = clean_env(tmp_path)
    repo = tmp_path / "main" / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", env=env)
    git(repo, "commit", "-q", "--allow-empty", "-m", "seed", env=env)
    lane = tmp_path / "elsewhere" / "lane"
    git(repo, "worktree", "add", "-q", str(lane), env=env)
    created = mail(repo, "init", env=env)
    assert created.returncode == 0, created.stderr
    root = mail(lane, "root", env=env)
    assert root.returncode == 0, root.stderr
    assert str((repo / ".orchestrator").resolve()) in root.stdout
    assert "main worktree" in root.stdout
    exchange(repo, lane, env)


def test_init_ignores_the_folder_once_and_says_what_it_changed(tmp_path: Path) -> None:
    env = clean_env(tmp_path)
    git(tmp_path, "init", "-q", env=env)
    (tmp_path / ".gitignore").write_text("build/", encoding="utf-8")
    first = mail(tmp_path, "init", env=env)
    assert first.returncode == 0, first.stderr
    assert "added /.orchestrator/" in first.stdout
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "build/\n/.orchestrator/\n"
    second = mail(tmp_path, "init", env=env)
    assert second.returncode == 0
    assert "nothing changed" in second.stdout
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "build/\n/.orchestrator/\n"


def test_init_leaves_an_ignore_file_alone_when_a_rule_already_ignores_the_folder(
    tmp_path: Path,
) -> None:
    env = clean_env(tmp_path)
    git(tmp_path, "init", "-q", env=env)
    (tmp_path / ".gitignore").write_text("*\n", encoding="utf-8")
    result = mail(tmp_path, "init", env=env)
    assert result.returncode == 0, result.stderr
    assert ".gitignore" not in result.stdout
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_init_adopts_a_live_folder_without_touching_mail_or_locks(tmp_path: Path) -> None:
    env = clean_env(tmp_path)
    live = tmp_path / ".orchestrator"
    message = live / "mail" / "old" / "new" / "20260101T000000.000000000Z-x.md"
    message.parent.mkdir(parents=True)
    for part in ("tmp", "cur"):
        (live / "mail" / "old" / part).mkdir()
    message.write_text("From: x\nSubject: kept\n\nbody\n", encoding="utf-8")
    owner = live / "locks" / "res" / "owner"
    owner.parent.mkdir(parents=True)
    owner.write_text("old 2026-01-01T00:00:00Z note\n", encoding="utf-8")
    script = live / "bin" / "mail"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in live.rglob("*") if path.is_file()}
    refused = mail(tmp_path, "who", env=env)
    assert refused.returncode == 1
    assert "has no agent-mail-root marker" in refused.stderr
    adopted = mail(tmp_path, "init", env=env)
    assert adopted.returncode == 0, adopted.stderr
    assert "wrote the marker" in adopted.stdout
    assert "created" not in adopted.stdout
    for path, content in before.items():
        assert path.read_bytes() == content
    assert mail(tmp_path, "peek", "old", env=env).stdout.strip().endswith("kept")
    assert "old 2026-01-01" in mail(tmp_path, "locks", env=env).stdout


def test_no_command_but_init_creates_the_folder(tmp_path: Path) -> None:
    env = clean_env(tmp_path)
    for args in (("join", "codex"), ("who",), ("root",), ("locks",)):
        result = mail(tmp_path, *args, env=env)
        assert result.returncode == 1
        assert "init" in result.stderr
    assert not (tmp_path / ".orchestrator").exists()


def test_the_environment_root_must_carry_the_marker(tmp_path: Path) -> None:
    env = clean_env(tmp_path)
    (tmp_path / "plain").mkdir()
    result = mail(tmp_path, "who", env={**env, "ORCH_MAIL_ROOT": str(tmp_path / "plain")})
    assert result.returncode == 1
    assert "ORCH_MAIL_ROOT" in result.stderr


def test_exit_codes_and_join_report_the_root(tmp_path: Path) -> None:
    env = clean_env(tmp_path)
    assert mail(tmp_path, "init", env=env).returncode == 0
    joined = mail(tmp_path, "join", "codex", env=env)
    assert joined.stdout == "codex-1\n"
    assert "mail root:" in joined.stderr
    assert mail(tmp_path, "join", "codex", env=env).stdout == "codex-2\n"
    assert mail(tmp_path, "join", "--as", "codex-1", env=env).returncode == 1
    assert mail(tmp_path, "wait", "codex-1", "0", env=env).returncode == 2
    assert mail(tmp_path, "lock", "res", "codex-1", "note", env=env).returncode == 0
    assert mail(tmp_path, "lock", "res", "codex-2", "note", env=env).returncode == 3
    assert mail(tmp_path, "unlock", "res", "codex-2", env=env).returncode == 1
    assert mail(tmp_path, "unlock", "res", "codex-2", "--break", env=env).returncode == 0
    assert mail(tmp_path, "locks", env=env).stdout == "no lock is held\n"
    assert mail(tmp_path, "send", "codex-1", "codex-2", "s", body="", env=env).returncode == 1
    assert mail(tmp_path, "bogus", env=env).returncode == 1


@pytest.mark.parametrize("name", ["../up", "Upper", "a", "x/y", "..", "a" * 40])
def test_a_bad_name_or_resource_never_reaches_a_path(tmp_path: Path, name: str) -> None:
    env = clean_env(tmp_path)
    assert mail(tmp_path, "init", env=env).returncode == 0
    assert mail(tmp_path, "join", "--as", "agent", env=env).returncode == 0
    for args in (("join", "--as", name), ("lock", name, "agent", "note"), ("read", name)):
        result = mail(tmp_path, *args, env=env)
        assert result.returncode == 1
        assert "bad" in result.stderr
    entries = {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")}
    assert all(entry.startswith(".orchestrator") for entry in entries)


def prepared(tmp_path: Path) -> tuple[ModuleType, Path]:
    module = load()
    base = tmp_path / ".orchestrator" / "mail" / "box"
    for part in ("tmp", "new", "cur"):
        (base / part).mkdir(parents=True)
    return module, base


def flood(module: ModuleType, box: Path, senders: int, each: int, size: int) -> None:
    def send(number: int) -> None:
        for index in range(each):
            module.deliver(box, f"s{number}", f"{number}:{index}:" + "x" * size + "END\n")

    threads = [threading.Thread(target=send, args=(number,)) for number in range(senders)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


@pytest.mark.parametrize("hard_links", [True, False])
def test_concurrent_senders_with_one_clock_reading_lose_no_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hard_links: bool
) -> None:
    module, box = prepared(tmp_path)
    monkeypatch.setattr(module, "stamp", lambda: "20260101T000000.000000000Z")
    if not hard_links:

        def no_link(*_: object) -> None:
            raise PermissionError("hard links are not supported here")

        monkeypatch.setattr(module.os, "link", no_link)
    flood(module, box, senders=4, each=40, size=10)
    bodies = sorted(path.read_text(encoding="utf-8") for path in (box / "new").iterdir())
    assert len(bodies) == 160
    assert len(set(bodies)) == 160
    assert not list((box / "tmp").iterdir())


WATCHER = """
import sys
from pathlib import Path
folder, stop = Path(sys.argv[1]), Path(sys.argv[2])
seen = 0
partial = set()
while not stop.exists():
    for path in folder.glob("*.md"):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        seen += 1
        if not data.endswith(b"END\\n"):
            partial.add(path.name)
print(seen, len(partial))
"""


def test_a_reader_never_sees_a_half_written_message(tmp_path: Path) -> None:
    module, box = prepared(tmp_path)
    stop = tmp_path / "stop"
    watcher = subprocess.Popen(
        [sys.executable, "-S", "-c", WATCHER, str(box / "new"), str(stop)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        flood(module, box, senders=3, each=20, size=2_000_000)
    finally:
        stop.write_text("", encoding="utf-8")
        output, _ = watcher.communicate(timeout=60)
    seen, partial = (int(value) for value in output.split())
    assert seen > 0
    assert partial == 0
    assert len(list((box / "new").glob("*.md"))) == 60


def test_a_lock_has_exactly_one_winner_among_concurrent_processes(tmp_path: Path) -> None:
    env = clean_env(tmp_path)
    assert mail(tmp_path, "init", env=env).returncode == 0
    names = [f"agent-{number}" for number in range(10)]
    for name in names:
        assert mail(tmp_path, "join", "--as", name, env=env).returncode == 0
    for round_number in range(4):
        resource = f"res-{round_number}"
        racers = [
            subprocess.Popen(
                [sys.executable, "-S", "-B", str(SCRIPT), "lock", resource, name, "race"],
                cwd=tmp_path,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for name in names
        ]
        codes = sorted(racer.wait(timeout=60) for racer in racers)
        assert codes == [0] + [3] * (len(names) - 1)
        owner_file = tmp_path / ".orchestrator" / "locks" / resource / "owner"
        owner = owner_file.read_text(encoding="utf-8")
        assert owner.split(" ", 1)[0] in names


@pytest.mark.parametrize("host", [".agents", ".claude"])
def test_the_projected_script_runs_without_engine_or_site_packages(
    tmp_path: Path, host: str
) -> None:
    folder = REPO / host / "skills/agent-mail/scripts"
    for module in MODULES:
        assert (folder / module).read_bytes() == (SCRIPTS / module).read_bytes()
    projected = tmp_path / "scripts"
    shutil.copytree(folder, projected, ignore=shutil.ignore_patterns("__pycache__"))
    script = projected / "agent_mail.py"
    env = clean_env(tmp_path)
    work = tmp_path / "work"
    work.mkdir()

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-S", "-B", str(script), *args],
            cwd=work,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=60,
        )

    assert run("help").returncode == 0
    assert run("init").returncode == 0
    assert run("join", "--as", "solo").stdout == "solo\n"
    assert "found above the current folder" in run("root").stdout


def test_a_non_ascii_body_survives_a_non_utf8_locale(tmp_path: Path) -> None:
    env = clean_env(tmp_path)
    assert mail(tmp_path, "init", env=env).returncode == 0
    for name in ("left", "right"):
        assert mail(tmp_path, "join", "--as", name, env=env).returncode == 0
    legacy = {**env, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252", "LC_ALL": "C"}
    body = "Grüße ✓ naïve 漢字"

    def raw(*args: str, data: bytes) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-S", "-B", str(SCRIPT), *args],
            cwd=tmp_path,
            input=data,
            capture_output=True,
            check=False,
            env=legacy,
            timeout=60,
        )

    assert raw("send", "left", "right", "hello", data=body.encode("utf-8")).returncode == 0
    shown = raw("read", "right", data=b"")
    assert shown.returncode == 0
    assert body in shown.stdout.decode("utf-8")
    refused = raw("send", "left", "right", "bad", data=b"\xff\xfe broken")
    assert refused.returncode == 1


def test_the_script_parses_on_its_python_floor() -> None:
    for module in MODULES:
        ast.parse((SCRIPTS / module).read_text(encoding="utf-8"), feature_version=(3, 12))
    with pytest.raises(SyntaxError):
        ast.parse(
            "try:\n    pass\nexcept OSError, ValueError:\n    pass\n", feature_version=(3, 12)
        )


def test_skill_text_states_the_trust_rules_and_no_workspace_policy() -> None:
    text = SKILL_TEXT.read_text(encoding="utf-8")
    lowered = text.lower()
    for phrase in ("never the owner", "not authenticated", "secret", "init", "wait", "root"):
        assert phrase in lowered
    for policy in ("basicly-main", "landing", "orchestrator --break"):
        assert policy not in lowered
