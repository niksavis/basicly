from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
KIT_ROOT = REPO / ".basicly" / "core" / "kit"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


modes = _load("basicly_kit_modes", REPO / "packages" / "kit_modes.py")
tracker = _load(
    "basicly_tracker_modes_test",
    REPO / "packages" / "basicly-tracker" / "basicly_tracker" / "__init__.py",
)
board = _load(
    "basicly_board_modes_test",
    REPO / "packages" / "basicly-board" / "basicly_board" / "__init__.py",
)
KITS = [(tracker.USER_SKILL, KIT_ROOT / "tracker"), (board.USER_SKILL, KIT_ROOT / "board")]


def _front(body: str) -> str:
    return body.split("---\n")[1]


@pytest.mark.parametrize(("skill", "kit"), KITS, ids=["tracker", "board"])
def test_the_user_skill_is_named_for_its_command_so_it_shadows_no_repository_skill(
    skill, kit: Path
) -> None:
    body = modes.user_skill_body((kit / "GUIDANCE.md").read_text(encoding="utf-8"), skill)

    assert f"name: {skill.command}\n" in _front(body)
    assert "holds .basicly/ledger/" in _front(body)


@pytest.mark.parametrize(("skill", "kit"), KITS, ids=["tracker", "board"])
def test_the_user_skill_names_a_vendored_path_only_in_its_where_section(skill, kit: Path) -> None:
    body = modes.user_skill_body((kit / "GUIDANCE.md").read_text(encoding="utf-8"), skill)
    where, _, rest = body.partition("## Rules")

    assert ".basicly/kit/" in where and skill.places[0] in where
    assert ".basicly/kit/" not in rest
    assert f"use {skill.engine_use} instead" in where


def test_a_guidance_edit_that_drops_a_replaced_phrase_is_refused() -> None:
    guidance = "---\nname: other\ndescription: x\n---\n\n# Title\n\nbody\n"

    with pytest.raises(SystemExit, match="no longer holds"):
        modes.user_skill_body(guidance, tracker.USER_SKILL)


def _kit_dir(tmp_path: Path) -> Path:
    kit = tmp_path / "kit"
    kit.mkdir(parents=True)
    (kit / "GUIDANCE.md").write_bytes((KIT_ROOT / "tracker" / "GUIDANCE.md").read_bytes())
    return kit


def test_install_then_uninstall_leaves_the_home_as_it_was(tmp_path: Path) -> None:
    home = tmp_path / "home"
    out = io.StringIO()

    modes.install_user(_kit_dir(tmp_path), tracker.USER_SKILL, home, out)
    path = modes.user_skill_path(home, tracker.USER_SKILL)
    assert path.is_file()
    modes.install_user(_kit_dir(tmp_path / "again"), tracker.USER_SKILL, home, out)
    assert "is current; nothing changed" in out.getvalue()
    modes.uninstall_user(tracker.USER_SKILL, home, out)

    assert not path.exists() and not path.parent.exists()


def test_a_skill_the_installer_did_not_write_is_kept(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = modes.user_skill_path(home, tracker.USER_SKILL)
    path.parent.mkdir(parents=True)
    path.write_text("mine\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="did not write"):
        modes.install_user(_kit_dir(tmp_path), tracker.USER_SKILL, home, io.StringIO())
    with pytest.raises(SystemExit, match="not written by this installer"):
        modes.uninstall_user(tracker.USER_SKILL, home, io.StringIO())
    assert path.read_text(encoding="utf-8") == "mine\n"


def _package():
    def bundle(out: Path) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("pyz\n", encoding="utf-8")
        return out

    kit = tracker.KIT._replace(directory=KIT_ROOT / "tracker")
    return tracker.PACKAGE._replace(kit=kit, bundle=bundle, source="SOURCE")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)  # nosec B603 B607
    return root


def _request(package, root: Path) -> object:
    return tracker.installer.Request(package.kit, root, io.StringIO())


def _user_tool(tmp_path: Path, body: str) -> Path:
    bin_dir = tmp_path / "userbin"
    bin_dir.mkdir()
    command = bin_dir / "basicly-tracker"
    command.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    command.chmod(0o755)
    return bin_dir


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}/usr/bin{os.pathsep}/bin")
    return tmp_path


def test_user_command_skips_the_scripts_folder_of_the_running_environment(tmp_path: Path) -> None:
    bin_dir = _user_tool(tmp_path, "exit 0")

    assert modes.user_command("basicly-tracker", {"PATH": str(bin_dir)}, str(bin_dir)) is None
    assert modes.user_command("basicly-tracker", {"PATH": str(bin_dir)}, str(tmp_path)) == str(
        bin_dir / "basicly-tracker"
    )


def test_the_default_mode_refuses_with_the_install_command_when_no_user_install_exists(
    machine: Path,
) -> None:
    package = _package()
    root = _repo(machine)

    with pytest.raises(SystemExit, match="uv tool install --force 'SOURCE'"):
        modes.install_mode(package, _request(package, root), sandbox=False)
    assert not (root / ".basicly").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in user tool is a POSIX script")
def test_the_default_mode_writes_no_kit_code_and_the_sandbox_mode_one_file(
    machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = KIT_ROOT / "tracker" / "cli.py"
    bin_dir = _user_tool(machine, f'exec "{sys.executable}" "{cli}" "$@"')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    package = _package()
    root = _repo(machine)

    modes.install_mode(package, _request(package, root), sandbox=True)
    assert (root / ".basicly" / "tracker.pyz").is_file()
    skill = (root / ".claude" / "skills" / "tracker" / "SKILL.md").read_text(encoding="utf-8")
    assert "python3 .basicly/tracker.pyz ready" in skill and ".basicly/kit/" not in skill

    modes.install_mode(package, _request(package, root), sandbox=False)
    assert not (root / ".basicly" / "tracker.pyz").exists()
    for folder in (".claude", ".agents"):
        guidance = (root / folder / "skills" / "tracker" / "SKILL.md").read_text("utf-8")
        assert "basicly-tracker ready" in guidance
        assert "python3 .basicly/tracker.pyz ready" not in guidance
    assert not (root / ".basicly" / "kit").exists()
    assert (root / ".basicly" / "ledger" / ".kit-version").is_file()
    assert "__pycache__" not in (root / ".gitignore").read_text(encoding="utf-8")
    assert modes.user_skill_path(machine / "home", tracker.USER_SKILL).is_file()


def _vendored(package, root: Path) -> None:
    tracker.installer.install(_request(package, root))
    ledger = root / ".basicly" / "ledger"
    (ledger / "pending-main.jsonl").write_text('{"kept": true}\n', encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in user tool is a POSIX script")
def test_the_vendored_copy_stays_when_the_user_install_refuses_the_pin(
    machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _user_tool(machine, 'echo "this ledger pins another tracker" >&2; exit 1')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    package = _package()
    root = _repo(machine)
    _vendored(package, root)

    with pytest.raises(SystemExit, match="so the vendored copy was kept"):
        modes.install_mode(package, _request(package, root), sandbox=False)
    assert (root / ".basicly" / "kit" / "tracker" / "cli.py").is_file()


def test_the_sandbox_mode_replaces_the_vendored_copy_and_keeps_the_ledger_bytes(
    machine: Path,
) -> None:
    package = _package()
    root = _repo(machine)
    _vendored(package, root)
    ledger = root / ".basicly" / "ledger" / "pending-main.jsonl"
    before = ledger.read_bytes()

    modes.install_mode(package, _request(package, root), sandbox=True)

    assert not (root / ".basicly" / "kit").exists()
    assert (root / ".basicly" / "tracker.pyz").is_file()
    assert ledger.read_bytes() == before


def test_user_guidance_reaches_both_agent_skill_roots(tmp_path: Path) -> None:
    home = tmp_path / "home"
    modes.install_user(_kit_dir(tmp_path), tracker.USER_SKILL, home, io.StringIO())
    paths = [
        home / folder / "skills" / "basicly-tracker" / "SKILL.md"
        for folder in (".claude", ".agents")
    ]
    assert all(path.is_file() for path in paths)
    assert paths[0].read_bytes() == paths[1].read_bytes()
    modes.uninstall_user(tracker.USER_SKILL, home, io.StringIO())
    assert all(not path.exists() for path in paths)


def test_unmanaged_second_user_skill_is_kept_without_partial_install(tmp_path: Path) -> None:
    home = tmp_path / "home"
    foreign = home / ".agents" / "skills" / "basicly-tracker" / "SKILL.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("my instructions\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="did not write"):
        modes.install_user(_kit_dir(tmp_path), tracker.USER_SKILL, home, io.StringIO())
    assert foreign.read_text() == "my instructions\n"
    assert not modes.user_skill_path(home, tracker.USER_SKILL).exists()


def test_repository_guidance_refuses_unmanaged_content_in_either_root(tmp_path: Path) -> None:
    foreign = tmp_path / ".agents" / "skills" / "tracker" / "SKILL.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("my repository instructions\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="did not write"):
        tracker.installer.write_skill(KIT_ROOT / "tracker", tmp_path, "tracker", io.StringIO())
    assert foreign.read_text() == "my repository instructions\n"
    assert not (tmp_path / ".claude" / "skills" / "tracker" / "SKILL.md").exists()


def test_install_uses_the_effective_git_hooks_path(tmp_path: Path) -> None:
    package = _package()
    root = _repo(tmp_path)
    subprocess.run(  # nosec B603 B607
        ["git", "config", "core.hooksPath", "hooks shared"], cwd=root, check=True
    )
    modes.install_mode(package, _request(package, root), sandbox=True)
    assert (root / "hooks shared" / "commit-msg").is_file()
    assert not (root / ".git" / "hooks" / "commit-msg").exists()


def test_the_package_routes_record_updates_to_the_tracker_not_the_installer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _package()
    directory = _repo(tmp_path) / ".basicly" / "ledger"
    assert (
        tracker.installer.run(
            package.kit, ["create", str(directory), "--prefix", "demo", "--title", "idea"]
        )
        == 0
    )
    record = json.loads(capsys.readouterr().out)["record"]
    assert (
        tracker.installer.run(
            package.kit, ["update", str(directory), record, "--field", "title=refined"]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["appended"]
    assert tracker.installer.run(package.kit, ["show", str(directory), record]) == 0
    assert json.loads(capsys.readouterr().out)["fields"]["title"] == "refined"
