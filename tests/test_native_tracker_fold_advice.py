from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from basicly import hooks, lefthook_binary, lefthook_config, merge, owned_store
from basicly.catalog import bundled_catalog_root
from tests.kit_deployment_helpers import _load
from tests.test_merge_shard_compaction import LEDGER_DIR, WRITER, _append, _git, repo

__all__ = ["repo"]


@pytest.fixture
def native_repo(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_OFFLINE", "1")
    (repo / "basicly.toml").write_text(
        f'[tracker]\nmode = "{owned_store.MODE_OWNED}"\nfold_on_merge = true\n', encoding="utf-8"
    )
    return repo


@pytest.mark.parametrize("manager", ["central", "standalone"])
@pytest.mark.parametrize("custom_hooks", [False, True], ids=["default-hooks", "custom-hooks"])
@pytest.mark.parametrize("pending", [False, True], ids=["empty-ledger", "pending-shard"])
def test_native_post_merge_upgrade_preserves_state_until_explicit_folding(
    native_repo: Path, manager: str, custom_hooks: bool, pending: bool
) -> None:
    repo = native_repo
    directory = repo / ".git" / ("custom-hooks" if custom_hooks else "hooks")
    directory.mkdir(exist_ok=True)
    if custom_hooks:
        _git(repo, "config", "core.hooksPath", ".git/custom-hooks")
    foreign = '#!/bin/sh\nprintf foreign >> "$(git rev-parse --git-path foreign-runs)"\n'
    post_merge = directory / "post-merge"
    post_merge.write_text(
        foreign
        + "# >>> basicly-tracker compact >>>\n"
        + 'printf obsolete >> "$(git rev-parse --git-path obsolete-runs)"\n'
        + "# <<< basicly-tracker compact <<<\n",
        encoding="utf-8",
    )
    post_merge.chmod(0o755)
    if manager == "central":
        destination = repo / ".basicly/core/hooks"
        destination.mkdir(parents=True)
        for name in (
            "lefthook-runtime.py",
            "staged-snapshot.py",
            "tracker-fold.py",
            "engine_pin.py",
        ):
            shutil.copy2(bundled_catalog_root() / "hooks" / name, destination / name)
        lefthook_binary.sync_binary(repo)
        lefthook_config.sync_config(
            repo,
            [hooks.HookSpec("tracker-fold", "tracker-fold.py", "post-merge", always_run=True)],
            ".basicly/core/hooks",
        )
        assert hooks.install_hooks(repo, ["post-merge"])[0]
    else:
        installer = _load(
            repo / owned_store.KIT_TRACKER_DIR / "install_hook.py",
            f"fold_advice_installer_{repo.parent.name}",
        )
        with (repo.parent / "install.log").open("w", encoding="utf-8") as stream:
            assert (
                installer.install(
                    repo,
                    ledger=repo / LEDGER_DIR,
                    dry_run=False,
                    interpreter=sys.executable,
                    stream=stream,
                )
                == 0
            )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "install tracker hooks")
    if pending:
        _append(repo, "demo-shard", writer=WRITER)
        _git(repo, "add", str(LEDGER_DIR))
        _git(repo, "commit", "-qm", "record pending shard")
    pre_commit = directory / "pre-commit"
    pre_commit.write_text(
        '#!/bin/sh\nprintf checked >> "$(git rev-parse --git-path pre-commit-runs)"\n',
        encoding="utf-8",
    )
    pre_commit.chmod(0o755)
    head = _git(repo, "rev-parse", "HEAD").stdout
    index = _git(repo, "write-tree").stdout
    ledger = {p.name: p.read_bytes() for p in (repo / LEDGER_DIR).glob("*.jsonl")}

    result = _git(repo, "hook", "run", "post-merge")

    assert _git(repo, "rev-parse", "HEAD").stdout == head
    assert _git(repo, "write-tree").stdout == index
    assert {p.name: p.read_bytes() for p in (repo / LEDGER_DIR).glob("*.jsonl")} == ledger
    assert (repo / ".git/foreign-runs").read_text(encoding="utf-8") == "foreign"
    assert not (repo / ".git/pre-commit-runs").exists()
    assert not (repo / ".git/obsolete-runs").exists()
    if not pending:
        assert "pending shards" not in result.stderr
        assert not merge.commit_tracker_state(repo, "demo-shard")
        return
    assert "compact" in result.stderr or "tracker fold" in result.stderr

    assert merge.commit_tracker_state(repo, "demo-shard")
    assert _git(repo, "rev-parse", "HEAD").stdout != head
    assert (repo / ".git/pre-commit-runs").read_text(encoding="utf-8") == "checked"
    assert not list((repo / LEDGER_DIR).glob("pending-*.jsonl"))
    before_ids = {json.loads(line)["id"] for data in ledger.values() for line in data.splitlines()}
    after_ids = {
        json.loads(line)["id"]
        for path in (repo / LEDGER_DIR).glob("*.jsonl")
        for line in path.read_bytes().splitlines()
    }
    assert after_ids == before_ids
