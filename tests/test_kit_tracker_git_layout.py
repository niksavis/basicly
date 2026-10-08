from __future__ import annotations

from pathlib import Path

import pytest

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


def _lane(tmp_path: Path, named: str, *, checkout: bool = True) -> Path:
    ledger = tmp_path / "lane" / "data" / "ledger"
    ledger.mkdir(parents=True)
    if checkout:
        (tmp_path / "lane" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    (ledger / layout.REDIRECT_FILE).write_text(f"{named}\n", encoding="utf-8")
    return ledger


def test_a_redirect_names_the_ledger_at_the_same_place_in_the_base(tmp_path: Path) -> None:
    shared = tmp_path / "base" / "data" / "ledger"
    shared.mkdir(parents=True)

    assert layout.shared_ledger(_lane(tmp_path, str(tmp_path / "base"))) == shared


def test_a_ledger_without_a_redirect_is_its_own(tmp_path: Path) -> None:
    assert layout.shared_ledger(tmp_path) == tmp_path


@pytest.mark.parametrize("named", ["gone", ""])
def test_a_redirect_that_names_no_ledger_is_refused_by_name(tmp_path: Path, named: str) -> None:
    ledger = _lane(tmp_path, named and str(tmp_path / named))

    with pytest.raises(layout.RedirectError, match=r"ledger redirect .* holds no data/ledger"):
        layout.shared_ledger(ledger)


def test_a_redirect_outside_a_git_checkout_is_refused_by_name(tmp_path: Path) -> None:
    (tmp_path / "base" / "data" / "ledger").mkdir(parents=True)
    ledger = _lane(tmp_path, str(tmp_path / "base"), checkout=False)

    with pytest.raises(layout.RedirectError, match="outside a git checkout"):
        layout.shared_ledger(ledger)
