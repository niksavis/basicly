from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_RELATIVE_DIR = Path(".basicly/core/kit/tier")
KIT_DIR = REPO_ROOT / KIT_RELATIVE_DIR
INSTALLER = KIT_DIR / "install_hook.py"
HOOK = KIT_DIR / "claude_tier_hook.py"


def _load(path: Path, suffix: str = "") -> ModuleType:
    name = f"kit_uninstall_{path.stem}{suffix}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kit = _load(INSTALLER)


@pytest.fixture
def consumer(tmp_path: Path) -> Path:

    kit_dir = tmp_path / KIT_RELATIVE_DIR
    kit_dir.mkdir(parents=True)
    for source in (INSTALLER, HOOK):
        shutil.copy2(source, kit_dir / source.name)
    return tmp_path


def _installer_in(repo: Path) -> ModuleType:
    return _load(repo / KIT_RELATIVE_DIR / INSTALLER.name, suffix=f"_{repo.name}")


def _settings(root: Path) -> dict:
    return json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))


def _our_groups(settings: dict) -> list:
    return [g for g in settings["hooks"]["PreToolUse"] if kit._runs_our_hook(g)]


def test_uninstalling_removes_our_hook_and_leaves_a_stranger_alone(consumer: Path) -> None:

    installer = _installer_in(consumer)
    installer.install(["claude"], consumer, user=False, dry_run=False)
    settings = consumer / ".claude" / "settings.json"
    payload = json.loads(settings.read_text(encoding="utf-8"))
    stranger = {"matcher": "Agent", "hooks": [{"type": "command", "command": "echo not-ours"}]}
    payload["hooks"]["PreToolUse"].append(stranger)
    settings.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    removed, lines = installer.uninstall(["claude"], consumer, user=False, dry_run=False)

    assert removed
    assert any("removed" in line for line in lines)
    assert _our_groups(_settings(consumer)) == []
    assert stranger in _settings(consumer)["hooks"]["PreToolUse"]


def test_uninstalling_a_hook_that_was_never_installed_is_quiet(consumer: Path) -> None:

    removed, lines = _installer_in(consumer).uninstall(
        ["claude"], consumer, user=False, dry_run=False
    )

    assert removed
    assert any("no PreToolUse/Agent hook of ours" in line for line in lines)
