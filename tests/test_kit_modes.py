from __future__ import annotations

import importlib.util
import io
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
