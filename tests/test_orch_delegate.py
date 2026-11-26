from __future__ import annotations

import importlib
import json
import os
import stat
import subprocess  # nosec B404
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".basicly" / "core" / "kit" / "orch"))

adapters = importlib.import_module("orch_relay.adapters")
delegate = importlib.import_module("orch_relay.delegate")
log = importlib.import_module("orch_lessons.log")

CLEAN = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
FAKE = "#!/usr/bin/env python3\n" + (Path(__file__).parent / "orch_delegate_fake.py").read_text(
    encoding="utf-8"
)


class Tracker:
    def __init__(self) -> None:
        self.comments: list[tuple[str, str]] = []

    def comment(self, record: str, text: str) -> None:
        self.comments.append((record, text))


def _git(where: Path, *args: str) -> None:
    subprocess.run(  # nosec B603 B607
        ["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        env=CLEAN,
    )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "start")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("basicly", "claude", "codex", "copilot"):
        path = bin_dir / name
        path.write_text(FAKE, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    for name in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(name)
    tracker = Tracker()
    ports = delegate.Ports(lambda _repo: tracker, lambda _repo: lambda _text: None)
    return SimpleNamespace(
        repo=repo,
        base=tmp_path / "store",
        tracker=tracker,
        ports=ports,
        calls=tmp_path / "calls.jsonl",
    )


def _args(**given: Any) -> SimpleNamespace:
    return SimpleNamespace(**{
        "vendor": None,
        "record": None,
        "resume": None,
        "model": None,
        **given,
    })


def _calls(world: SimpleNamespace, tool: str) -> list[dict]:
    lines = world.calls.read_text(encoding="utf-8").splitlines()
    return [c for c in map(json.loads, lines) if c["tool"] == tool]


def _session(lines: list[str]) -> str:
    return lines[0].split(" session ")[1].split(" ")[0]


@pytest.mark.parametrize("vendor", ["claude", "codex", "copilot"])
def test_a_delegation_answers_in_a_lane_comments_and_resumes_the_same_session(
    world: SimpleNamespace, vendor: str
) -> None:
    first = delegate.run(
        _args(vendor=vendor, record="app-x1", prompt="hello"), world.repo, world.base, world.ports
    )
    session = _session(first)
    again = delegate.run(_args(resume=session, prompt="again"), world.repo, world.base, world.ports)

    lane = adapters.lane_path(world.repo, f"delegate-app-x1-{vendor}")
    runs = _calls(world, vendor)
    assert [Path(call["cwd"]) for call in runs] == [lane, lane]
    assert session in " ".join(runs[1]["argv"])
    assert f"answer: {vendor} says: hello" in first
    assert f"answer: {vendor} says: again" in again
    assert [r for r, _ in world.tracker.comments] == ["app-x1", "app-x1"]
    assert "resumed turn" in world.tracker.comments[1][1]
    assert f"orch delegate --resume {session} --prompt TEXT" in first[3]
    assert len(_calls(world, "basicly")) == 1


def test_the_model_flag_reaches_the_coding_agent(world: SimpleNamespace) -> None:
    delegate.run(
        _args(vendor="codex", record="app-x1", prompt="p", model="gpt-6-luna"),
        world.repo,
        world.base,
        world.ports,
    )

    assert _calls(world, "codex")[0]["argv"][4:6] == ["-m", "gpt-6-luna"]


def test_a_usage_limit_is_reported_by_name_with_its_reset_time_and_writes_nothing(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LIMIT", "codex")

    with pytest.raises(adapters.UsageLimitError) as limited:
        delegate.run(
            _args(vendor="codex", record="app-x1", prompt="p"), world.repo, world.base, world.ports
        )

    assert (
        str(limited.value)
        == "usage limit: codex refused the turn at its usage limit; it resets 3:05 PM"
    )
    assert world.tracker.comments == []
    assert not (world.base / delegate.FOLDER).exists()


@pytest.mark.parametrize(
    ("given", "named"),
    [
        ({"vendor": "gemini", "record": "app-x1"}, "use one of: claude, codex, copilot"),
        ({"vendor": "claude", "record": "not a record"}, "is not a record id"),
        ({"vendor": "claude"}, "needs --vendor and --record"),
        ({"resume": "nope"}, "no delegation with that session is recorded"),
        ({"resume": "nope", "vendor": "claude"}, "takes the coding agent and record it recorded"),
    ],
)
def test_wrong_input_is_refused_by_name_before_any_lane_or_turn(
    world: SimpleNamespace, given: dict[str, str], named: str
) -> None:
    with pytest.raises(log.RefusedError, match=named):
        delegate.run(_args(prompt="p", **given), world.repo, world.base, world.ports)

    assert not world.calls.exists()


def test_an_answer_the_secret_scanner_flags_is_withheld_from_the_record(
    world: SimpleNamespace,
) -> None:
    ports = delegate.Ports(lambda _repo: world.tracker, lambda _repo: lambda _text: "aws-key")

    delegate.run(
        _args(vendor="copilot", record="app-x1", prompt="p"), world.repo, world.base, ports
    )

    assert "withheld: the secret scanner rule aws-key matched" in world.tracker.comments[0][1]


def test_an_installed_plugin_adds_a_coding_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    point = SimpleNamespace(name="echo", load=lambda: adapters.Copilot)
    monkeypatch.setattr(adapters.importlib.metadata, "entry_points", lambda **_: [point])

    assert list(adapters.registry()) == ["claude", "codex", "copilot", "echo"]
    assert isinstance(adapters.adapter("echo"), adapters.Copilot)
