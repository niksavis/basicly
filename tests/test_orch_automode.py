from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from basicly import permissions

PACKAGE = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "orch"
sys.path.insert(0, str(PACKAGE))

render = importlib.import_module("orch_automode.render")
conflicts = importlib.import_module("orch_automode.conflicts")
commands = importlib.import_module("orch_automode.commands")
runner = importlib.import_module("runner")
log = importlib.import_module("orch_lessons.log")

Checker = Callable[[Path], object]
CLEAN = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
TOKENS = {
    "project",
    "lane",
    "commit",
    "record-id",
    "name",
    "minutes",
    "version",
    "message",
    "tag",
    "path",
    "digits",
}


def config_file(tmp_path: Path, **changes: object) -> Path:
    data: dict[str, object] = {
        "host": "github.com",
        "owner": "acme",
        "workspace": str(tmp_path / "ws"),
        "repositories": [
            {"name": "app", "visibility": "public", "project": True, "tag": "v<version>"},
            {"name": "notes", "visibility": "private", "project": False},
        ],
    }
    data.update(changes)
    path = tmp_path / "automode.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def settings_file(tmp_path: Path, document: object) -> Path:
    folder = tmp_path / "claude"
    folder.mkdir(exist_ok=True)
    path = folder / "settings.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def reader(made: dict[str, list[str]] | None = None, code: int = 0) -> Checker:
    def check(folder: Path) -> object:
        auto = json.loads((folder / "settings.json").read_text(encoding="utf-8"))["autoMode"]
        shown = made if made is not None else auto
        return runner.Done(code, json.dumps(shown), "")

    return check


def test_the_generated_rules_parse_and_keep_defaults_first(tmp_path: Path) -> None:
    made = render.entries(render.load_config(config_file(tmp_path)))
    merged = json.loads(json.dumps(render.merge({"theme": "dark"}, made)))
    assert merged["theme"] == "dark"
    for kind in render.LISTS:
        assert merged["autoMode"][kind][0] == render.DEFAULTS
        assert merged["autoMode"][kind][1:] == made[kind]
    text = " ".join(entry for items in made.values() for entry in items)
    assert not re.search(r"\{[a-z]+\}", text)
    assert set(re.findall(r"<([a-z-]+)>", text)) <= TOKENS
    assert text.count("<lane> is [a-z0-9]") == 1


def test_a_merge_replaces_entries_by_label_and_keeps_the_others(tmp_path: Path) -> None:
    made = render.entries(render.load_config(config_file(tmp_path)))
    old = {
        "autoMode": {
            "allow": ["Lane Git Work: old text", "Mine: keep"],
            "soft_deny": ["Delete Outside Own Work [named]: old"],
        }
    }
    merged = render.merge(old, made)["autoMode"]
    assert "Lane Git Work: old text" not in merged["allow"]
    assert "Mine: keep" in merged["allow"]
    assert not any(entry.endswith(": old") for entry in merged["soft_deny"])


@pytest.mark.parametrize(
    ("change", "named"),
    [
        ({"owner": "acme;rm -r ~"}, "owner"),
        ({"host": "evil host"}, "host"),
        ({"workspace": "~/dev $(id)"}, "workspace"),
        (
            {"repositories": [{"name": "app'x", "visibility": "public", "project": True}]},
            "repositories[0].name",
        ),
        (
            {
                "repositories": [
                    {"name": "app", "visibility": "public", "project": True, "tag": "v*"}
                ]
            },
            "repositories[0].tag",
        ),
        (
            {"repositories": [{"name": "app", "visibility": "secret", "project": True}]},
            "visibility",
        ),
        ({"omit": ["Shared Terms"]}, "omit"),
    ],
)
def test_a_config_value_outside_its_token_class_is_refused_by_name(
    tmp_path: Path, change: dict[str, object], named: str
) -> None:
    with pytest.raises(log.RefusedError, match=re.escape(named)):
        render.load_config(config_file(tmp_path, **change))


def git(cwd: Path, *argv: str) -> str:
    done = subprocess.run(
        ["git", *argv], cwd=cwd, capture_output=True, text=True, check=True, env=CLEAN
    )  # nosec B603 B607
    return done.stdout.strip()


@pytest.fixture
def lane_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, env=CLEAN
    )  # nosec B603 B607
    main = tmp_path / "app"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(main)], check=True, capture_output=True, env=CLEAN
    )  # nosec B603 B607
    for key, value in (
        ("user.name", "t"),
        ("user.email", "t@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        git(main, "config", key, value)
    (main / "a.txt").write_text("a\n", encoding="utf-8")
    git(main, "add", "a.txt")
    git(main, "commit", "-q", "-m", "first")
    git(main, "push", "-q", "origin", "main")
    git(
        main,
        "worktree",
        "add",
        "-q",
        "-b",
        "harness/lane-x",
        str(tmp_path / "app.worktrees" / "lane-x"),
    )
    return main


def guards(name: str, values: dict[str, str]) -> str:
    source = render.rules()
    rule = next(rule for rule in source["allow"] if rule["name"] == name)
    line = render.guard_line(rule["shapes"][0], " ".join(source["guarded_paths"]), "echo PASSED")
    for key, value in values.items():
        line = line.replace(f"<{key}>", value)
    return line


def passes(cwd: Path, line: str) -> bool:
    done = subprocess.run(
        ["bash", "-c", line], cwd=cwd, capture_output=True, text=True, check=False, env=CLEAN
    )  # nosec B603 B607
    return done.returncode == 0 and done.stdout.strip().endswith("PASSED")


def lane_commit(main: Path, name: str) -> str:
    lane = main.parent / "app.worktrees" / "lane-x"
    (lane / name).parent.mkdir(parents=True, exist_ok=True)
    (lane / name).write_text("x\n", encoding="utf-8")
    git(lane, "add", name)
    git(lane, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", name)
    return git(lane, "rev-parse", "HEAD")


CLEANUP = {"project": "app", "lane": "lane-x"}


def test_the_cleanup_guards_pass_a_merged_clean_lane(lane_repo: Path) -> None:
    assert passes(lane_repo, guards("Merged Lane Cleanup", CLEANUP))


def test_the_cleanup_guards_refuse_a_lane_with_an_unmerged_commit(lane_repo: Path) -> None:
    lane_commit(lane_repo, "b.txt")
    assert not passes(lane_repo, guards("Merged Lane Cleanup", CLEANUP))


def test_the_cleanup_guards_refuse_a_remote_lane_branch_with_an_unmerged_commit(
    lane_repo: Path,
) -> None:
    lane = lane_repo.parent / "app.worktrees" / "lane-x"
    lane_commit(lane_repo, "b.txt")
    git(lane, "push", "-q", "origin", "harness/lane-x")
    git(lane, "reset", "-q", "--keep", "main")
    assert not passes(lane_repo, guards("Merged Lane Cleanup", CLEANUP))


def test_the_cleanup_guards_refuse_a_dirty_lane_folder(lane_repo: Path) -> None:
    lane = lane_repo.parent / "app.worktrees" / "lane-x"
    (lane / "loose.txt").write_text("x\n", encoding="utf-8")
    assert not passes(lane_repo, guards("Merged Lane Cleanup", CLEANUP))


def test_the_clean_folder_guard_fails_closed_when_git_cannot_read_the_folder(
    lane_repo: Path,
) -> None:
    git(lane_repo, "worktree", "remove", str(lane_repo.parent / "app.worktrees" / "lane-x"))
    assert git(lane_repo, "rev-parse", "--verify", "harness/lane-x")
    assert not passes(lane_repo, guards("Merged Lane Cleanup", CLEANUP))


def test_the_tag_guard_refuses_a_commit_that_is_not_on_origin_main(lane_repo: Path) -> None:
    on_main = git(lane_repo, "rev-parse", "HEAD")
    off_main = lane_commit(lane_repo, "b.txt")
    assert passes(lane_repo, guards("Release Tags", {"commit": on_main}))
    assert not passes(lane_repo, guards("Release Tags", {"commit": off_main}))


def test_the_landing_guard_refuses_a_lane_that_changes_a_guarded_path(lane_repo: Path) -> None:
    lane_commit(lane_repo, "src/app.py")
    assert passes(lane_repo, guards("Lane Landing", {"lane": "lane-x"}))
    lane_commit(lane_repo, ".github/workflows/ci.yml")
    assert not passes(lane_repo, guards("Lane Landing", {"lane": "lane-x"}))


def test_a_deny_rule_that_blocks_a_generated_command_is_a_conflict(tmp_path: Path) -> None:
    project = tmp_path / "ws" / "app" / ".claude"
    project.mkdir(parents=True)
    (project / "settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Bash(git rebase*)"], "ask": ["Bash(rm -r *)"]}}),
        encoding="utf-8",
    )
    config = render.load_config(config_file(tmp_path))
    found = conflicts.find(config, settings_file(tmp_path, {}))
    assert {(item.rule, item.kind) for item in found} == {
        ("Lane Git Work", "deny"),
        ("Own Scratch Removal", "ask"),
    }
    omitted = render.load_config(
        config_file(tmp_path, omit=["Lane Git Work", "Own Scratch Removal"])
    )
    assert conflicts.find(omitted, settings_file(tmp_path, {})) == []


def test_no_generated_command_meets_a_deny_rule_basicly_ships(tmp_path: Path) -> None:
    shipped = permissions.load_claude_permissions()
    path = settings_file(tmp_path, {"permissions": {"deny": list(shipped.deny)}})
    assert conflicts.find(render.load_config(config_file(tmp_path)), path) == []


def run(tmp_path: Path, action: str, settings: Path, check: Checker, plan: str = "") -> str:
    args = argparse.Namespace(
        group="automode", action=action, config=config_file(tmp_path), settings=settings, plan=plan
    )
    return commands.run(args, tmp_path, {}, check) or ""


def token(text: str) -> str:
    return text.rsplit("plan token: ", 1)[1].strip()


def test_plan_writes_nothing_and_apply_writes_a_backup_then_plans_no_change(tmp_path: Path) -> None:
    settings = settings_file(tmp_path, {"theme": "dark", "autoMode": {"allow": ["Mine: keep"]}})
    settings.chmod(0o600)
    before = settings.read_bytes()
    shown = run(tmp_path, "plan", settings, reader())
    assert settings.read_bytes() == before
    done = run(tmp_path, "apply", settings, reader(), token(shown))
    backups = list(settings.parent.glob("settings.json.orch-automode-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert backups[0].stat().st_mode & 0o777 == settings.stat().st_mode & 0o777
    assert "Mine: keep" in json.loads(settings.read_text(encoding="utf-8"))["autoMode"]["allow"]
    assert "next: claude auto-mode critique" in done
    assert run(tmp_path, "plan", settings, reader()).startswith("no change")


def test_apply_refuses_a_settings_file_that_changed_after_the_plan(tmp_path: Path) -> None:
    settings = settings_file(tmp_path, {"theme": "dark"})
    shown = run(tmp_path, "plan", settings, reader())
    settings.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    changed = settings.read_bytes()
    with pytest.raises(log.RefusedError, match="does not match"):
        run(tmp_path, "apply", settings, reader(), token(shown))
    assert settings.read_bytes() == changed
    assert not list(settings.parent.glob("settings.json.orch-automode-*"))


def test_apply_refuses_when_claude_does_not_read_the_planned_rules(tmp_path: Path) -> None:
    settings = settings_file(tmp_path, {"theme": "dark"})
    before = settings.read_bytes()
    shown = run(tmp_path, "plan", settings, reader())
    with pytest.raises(log.RefusedError, match="did not read the planned rules"):
        run(tmp_path, "apply", settings, reader({"allow": []}), token(shown))
    assert settings.read_bytes() == before


def test_apply_restores_the_file_when_the_written_rules_do_not_load(tmp_path: Path) -> None:
    settings = settings_file(tmp_path, {"theme": "dark"})
    before = settings.read_text(encoding="utf-8")
    shown = run(tmp_path, "plan", settings, reader())
    calls: list[Path] = []

    def second_fails(folder: Path) -> object:
        calls.append(folder)
        return (
            reader()(folder) if len(calls) == 1 else runner.Done(0, json.dumps({"allow": []}), "")
        )

    with pytest.raises(log.RefusedError, match="restored"):
        run(tmp_path, "apply", settings, second_fails, token(shown))
    assert settings.read_text(encoding="utf-8") == before


def test_the_guide_names_every_step_in_order() -> None:
    text = commands.run(argparse.Namespace(group="automode", action="guide"), Path(), {}) or ""
    steps = [
        "/auto-mode-setup",
        "orch automode plan",
        "orch automode apply",
        "claude auto-mode critique",
    ]
    assert [text.index(step) for step in steps] == sorted(text.index(step) for step in steps)
    assert "one guarded step per call" in text


def test_plan_and_apply_refuse_while_a_conflict_is_left(tmp_path: Path) -> None:
    deny = {"permissions": {"deny": ["Bash(git rebase*)"]}}
    settings = settings_file(tmp_path, deny)
    before = settings.read_bytes()
    with pytest.raises(log.RefusedError, match="conflict") as refused:
        run(tmp_path, "plan", settings, reader())
    with pytest.raises(log.RefusedError, match="conflict"):
        run(tmp_path, "apply", settings, reader(), token(str(refused.value)))
    assert settings.read_bytes() == before
