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
        "tier": None,
        "read_only": False,
        "billing": None,
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

    saved = sorted((world.base / delegate.FOLDER / "failures").glob("*-codex-*.json"))
    assert str(limited.value) == (
        "usage limit: codex refused the turn at its usage limit; it resets 3:05 PM; "
        f"the failure is saved in {saved[0]}"
    )
    assert world.tracker.comments == []
    assert [p.name for p in (world.base / delegate.FOLDER).glob("*.json")] == []


@pytest.mark.parametrize(
    ("given", "named"),
    [
        ({"vendor": "gemini", "record": "app-x1"}, "use one of: claude, codex, copilot"),
        ({"vendor": "claude", "record": "not a record"}, "is not a record id"),
        ({"vendor": "claude"}, "needs --vendor and --record"),
        ({"resume": "nope"}, "no delegation with that session is recorded"),
        ({"resume": "nope", "vendor": "claude"}, "takes the coding agent, record and mode"),
        ({"resume": "nope", "read_only": True}, "takes the coding agent, record and mode"),
        ({"resume": "nope", "billing": "console"}, "takes the coding agent, record and mode"),
        ({"vendor": "codex", "record": "app-x1", "billing": "console"}, "runs only claude"),
        ({"vendor": "claude", "record": "app-x1", "tier": "top"}, "is not in the tier map"),
        (
            {"vendor": "claude", "record": "app-x1", "tier": "cheap", "model": "m"},
            "takes --tier or --model, not both",
        ),
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


class Bare:
    name = "bare"

    def argv(self, _turn: object) -> list[str]:
        return []

    def reply(self, _done: object, _turn: object) -> object:
        return None

    def resume_command(self, session: str) -> str:
        return session


class Viewer(adapters.Copilot):
    name = "viewer"
    capabilities = adapters.Capabilities(True, True, True, False)


def _broken() -> None:
    raise ImportError("no module named viewer_kit")


def _points(monkeypatch: pytest.MonkeyPatch, **loads: Any) -> None:
    points = [
        SimpleNamespace(name=name, load=load, value=f"{name}_kit:Adapter")
        for name, load in loads.items()
    ]
    monkeypatch.setattr(adapters.importlib.metadata, "entry_points", lambda **_: points)


def test_a_broken_plugin_is_refused_by_name_and_the_built_ins_still_run(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _points(monkeypatch, broken=_broken, bare=lambda: Bare)

    with pytest.raises(log.RefusedError, match=r"refuses the plugin 'broken'.*ImportError"):
        adapters.adapter("broken")
    with pytest.raises(log.RefusedError, match=r"refuses the plugin 'bare'.*lacks capabilities"):
        adapters.adapter("bare")
    lines = delegate.run(
        _args(vendor="claude", record="app-x1", prompt="p"), world.repo, world.base, world.ports
    )

    assert "answer: claude says: p" in lines
    assert list(adapters.registry()) == ["claude", "codex", "copilot"]


def test_a_turn_that_needs_an_undeclared_capability_is_refused_before_any_process(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _points(monkeypatch, viewer=lambda: Viewer)

    with pytest.raises(log.RefusedError, match=r"viewer cannot edit in a lane.*lane_edits"):
        delegate.run(
            _args(vendor="viewer", record="app-x1", prompt="p"), world.repo, world.base, world.ports
        )

    assert not world.calls.exists()


def test_list_prints_each_adapter_with_its_origin_and_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _points(monkeypatch, viewer=lambda: Viewer, broken=_broken)

    lines = delegate.listing(_args(prompt=None))

    assert lines == [
        "claude  built-in  names_session resumes_read_only read_only lane_edits",
        "codex  built-in  read_only lane_edits",
        "copilot  built-in  names_session resumes_read_only read_only lane_edits",
        "viewer  plugin viewer_kit:Adapter  names_session resumes_read_only read_only",
        "broken  plugin broken_kit:Adapter  refused: ImportError: no module named viewer_kit",
    ]
    with pytest.raises(log.RefusedError, match="--list takes no other option"):
        delegate.listing(_args(vendor="claude"))


READ_ONLY_ARGV = {
    "claude": [
        *["-p", "p", "--output-format", "json", "--permission-mode", "plan"],
        *["--model", "haiku", "--session-id", "SESSION"],
    ],
    "codex": ["exec", "--json", "-s", "read-only", "-m", "gpt-6-luna", "p"],
    "copilot": [
        *["-p", "p", "--silent", "--deny-tool=write", "--model", "mai-code-2-flash"],
        "--session-id=SESSION",
    ],
}


@pytest.mark.parametrize("vendor", ["claude", "codex", "copilot"])
def test_a_read_only_cheap_delegation_runs_in_the_repository_with_the_read_only_argv(
    world: SimpleNamespace, vendor: str
) -> None:
    lines = delegate.run(
        _args(vendor=vendor, record="app-x1", prompt="p", read_only=True, tier="cheap"),
        world.repo,
        world.base,
        world.ports,
    )

    session = _session(lines)
    run = _calls(world, vendor)[0]
    assert run["argv"] == [a.replace("SESSION", session) for a in READ_ONLY_ARGV[vendor]]
    assert Path(run["cwd"]) == world.repo
    assert not _calls(world, "basicly")
    assert not adapters.lane_path(world.repo, f"delegate-app-x1-{vendor}").parent.exists()
    assert f"session {session}, read-only]" in world.tracker.comments[0][1]
    assert json.loads((world.base / "tiers.json").read_text(encoding="utf-8"))["cheap"][vendor]


def test_a_resumed_read_only_session_stays_read_only_and_codex_refuses_one(
    world: SimpleNamespace,
) -> None:
    given = {"record": "app-x1", "prompt": "p", "read_only": True}
    claude = _session(
        delegate.run(_args(vendor="claude", **given), world.repo, world.base, world.ports)
    )
    codex = _session(
        delegate.run(_args(vendor="codex", **given), world.repo, world.base, world.ports)
    )

    delegate.run(_args(resume=claude, prompt="again"), world.repo, world.base, world.ports)
    with pytest.raises(log.RefusedError, match="codex cannot resume a read-only session"):
        delegate.run(_args(resume=codex, prompt="again"), world.repo, world.base, world.ports)

    resumed = _calls(world, "claude")[1]
    assert resumed["argv"][4:6] == ["--permission-mode", "plan"]
    assert Path(resumed["cwd"]) == world.repo
    assert len(_calls(world, "codex")) == 1
    assert not _calls(world, "basicly")


def test_a_tier_with_no_model_for_the_coding_agent_is_refused_before_any_turn(
    world: SimpleNamespace,
) -> None:
    world.base.mkdir()
    (world.base / "tiers.json").write_text('{"cheap": {"claude": "haiku"}}', encoding="utf-8")

    with pytest.raises(log.RefusedError, match="names no model for the coding agent 'copilot'"):
        delegate.run(
            _args(vendor="copilot", record="app-x1", prompt="p", tier="cheap"),
            world.repo,
            world.base,
            world.ports,
        )

    assert not world.calls.exists()


def test_a_copilot_lane_turn_passes_the_lane_rules_and_prints_them(world: SimpleNamespace) -> None:
    lines = delegate.run(
        _args(vendor="copilot", record="app-x1", prompt="p"), world.repo, world.base, world.ports
    )

    lane = adapters.lane_path(world.repo, "delegate-app-x1-copilot")
    session = _session(lines)
    rules = [
        f"--allow-tool=write({lane}/**)",
        "--allow-tool=shell(git status)",
        "--allow-tool=shell(git diff)",
        "--allow-tool=shell(git add)",
        "--allow-tool=shell(git commit)",
        "--allow-tool=shell(uv run)",
        "--allow-tool=shell(pytest)",
        "--deny-tool=shell(git push)",
        "--deny-tool=shell(git reset)",
        "--deny-tool=shell(git checkout)",
        "--deny-tool=shell(rm)",
        "--deny-tool=url",
        "--disallow-temp-dir",
    ]
    argv = ["-p", "p", "--silent", *rules, f"--session-id={session}"]
    assert _calls(world, "copilot")[0]["argv"] == argv
    assert f"rules: {' '.join(rules)}" in lines


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("vendor", ["claude", "codex", "copilot"])
def test_no_coding_agent_gets_an_allow_all_or_bypass_flag(
    world: SimpleNamespace, vendor: str, read_only: bool
) -> None:
    delegate.run(
        _args(vendor=vendor, record="app-x1", prompt="p", read_only=read_only),
        world.repo,
        world.base,
        world.ports,
    )

    argv = _calls(world, vendor)[0]["argv"]
    assert not [part for part in argv if part.split("=", 1)[0] in adapters.FORBIDDEN]
    if read_only:
        assert not [part for part in argv if part.startswith("--allow-tool")]


def test_a_plugin_that_passes_a_bypass_flag_is_refused_before_the_turn(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Loose(adapters.Copilot):
        name = "loose"

        def argv(self, turn: object) -> list[str]:
            return [*super().argv(turn), "--yolo"]

    point = SimpleNamespace(name="loose", load=lambda: Loose)
    monkeypatch.setattr(adapters.importlib.metadata, "entry_points", lambda **_: [point])

    with pytest.raises(log.RefusedError, match="never passes --yolo to loose"):
        delegate.run(
            _args(vendor="loose", record="app-x1", prompt="p"), world.repo, world.base, world.ports
        )

    assert not _calls(world, "copilot")
