from __future__ import annotations

from pathlib import Path

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

layout = _load(REPO_ROOT / KIT_RELATIVE / "git_layout.py", "tracker_git_layout_test")


def test_linked_checkout_uses_its_common_git_hooks(tmp_path: Path) -> None:
    base = tmp_path / "base"
    common = base / ".git"
    linked = common / "worktrees" / "lane"
    linked.mkdir(parents=True)
    (linked / "commondir").write_text("../..\n", encoding="utf-8")
    lane = tmp_path / "lane"
    lane.mkdir()
    (lane / ".git").write_text(f"gitdir: {linked}\n", encoding="utf-8")
    assert layout.git_dir(lane) == linked
    assert layout.hooks_dir(lane) == common / "hooks"


def test_effective_host_hooks_override_local_configuration(tmp_path: Path) -> None:
    common = tmp_path / ".git"
    common.mkdir()
    (common / "config").write_text("[core]\n  hooksPath = local-hooks\n", encoding="utf-8")
    assert layout.hooks_dir(tmp_path) == tmp_path / "local-hooks"
    effective = tmp_path / "effective-hooks"
    assert layout.hooks_dir(tmp_path, effective) == effective


def test_a_non_repository_has_no_hook_directory(tmp_path: Path) -> None:
    assert layout.git_dir(tmp_path) is None
    assert layout.hooks_dir(tmp_path) is None
