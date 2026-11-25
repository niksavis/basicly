from __future__ import annotations

import importlib.util
import json
import py_compile
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from basicly import (
    lefthook_binary,
    verify_cache,
    worktree,
    worktree_contents,
)
from tests import test_worktree_safety
from tests.test_worktree import _git

safety_repo = test_worktree_safety.safety_repo


@pytest.mark.parametrize(
    ("status", "path", "allowed"),
    [
        ("!!", verify_cache.CACHE_PATH.as_posix(), True),
        ("??", verify_cache.CACHE_PATH.as_posix(), True),
        (" M", verify_cache.CACHE_PATH.as_posix(), False),
        ("!!", ".basicly/usage/consumer-cache.json", False),
    ],
)
def test_cleanup_recognizes_only_untracked_generated_verification_cache(
    status: str, path: str, allowed: bool
) -> None:
    verdict = worktree_contents.classify_worktree_tree(0, f"{status} {path}\n")
    assert verdict.may_remove is allowed


def _hook_pass_store() -> Path:
    path = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "hooks" / "check_reuse.py"
    spec = importlib.util.spec_from_file_location("check_reuse_cleanup_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STORE


def test_cleanup_names_the_store_the_hook_pass_cache_writes() -> None:
    assert _hook_pass_store() == worktree_contents.CHECK_PASSES


@pytest.mark.parametrize(
    "body",
    [
        {"inputs:abc": {"checks": ["ruff", "pyright"], "at": "2026-11-05T00:00:00+00:00"}},
        {"inputs:abc": {"checks": "ruff", "at": "2026-11-05T00:00:00+00:00"}},
        {"inputs:abc": {"checks": ["ruff"]}},
        ["ruff"],
    ],
)
def test_cleanup_archives_every_hook_pass_cache_before_removal(
    safety_repo: Path, capsys: pytest.CaptureFixture[str], body: object
) -> None:
    session = worktree.create("pass-cache", repo_root=safety_repo)
    path = session.path / worktree_contents.CHECK_PASSES
    path.parent.mkdir(parents=True)
    path.with_name(".gitignore").write_text("*\n", encoding="utf-8")
    path.write_text(json.dumps(body), encoding="utf-8")

    worktree.cleanup("pass-cache", repo_root=safety_repo)

    assert not session.path.exists()
    archived = test_worktree_safety.archived_files(capsys.readouterr().out)
    assert archived[worktree_contents.CHECK_PASSES.as_posix()] == json.dumps(body).encode()


@pytest.mark.parametrize("contents", ["bytecode", "unexpected", "symlink", "outside", "directory"])
def test_cleanup_recognizes_only_regular_bytecode_in_owned_python_cache(
    safety_repo: Path, contents: str
) -> None:
    session = worktree.create("cache", repo_root=safety_repo)
    cache = session.path / (
        ".cache/__pycache__" if contents == "outside" else ".basicly/core/hooks/__pycache__"
    )
    cache.mkdir(parents=True)
    source = session.path / ".basicly/core/hooks/hook.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("value = 1\n", encoding="utf-8")
    _git(session.path, "add", "--", source.relative_to(session.path).as_posix())
    _git(session.path, "commit", "-m", "test: prepare a source owned by the fixture")
    _git(session.path, "config", "core.excludesFile", str(safety_repo / "cache-ignore"))
    (safety_repo / "cache-ignore").write_text("__pycache__/\n", encoding="utf-8")
    generated = Path(importlib.util.cache_from_source(str(source)))
    py_compile.compile(str(source), doraise=True)
    bytecode = cache / generated.name
    if contents == "symlink":
        bytecode.unlink()
        bytecode.symlink_to(session.path / "README.md")
    else:
        bytecode.write_bytes(generated.read_bytes())
    if contents == "unexpected":
        (cache / "notes.txt").write_text("retain this", encoding="utf-8")
    if contents == "directory":
        (cache / "nested.pyc").mkdir()
    if contents == "bytecode":
        worktree.cleanup("cache", repo_root=safety_repo)
        assert not session.path.exists()
    else:
        with pytest.raises(SystemExit, match="uncommitted changes"):
            worktree.cleanup("cache", repo_root=safety_repo)
        assert bytecode.is_symlink() if contents == "symlink" else bytecode.exists()


@pytest.mark.parametrize(
    "contents", ["managed", "unknown", "binary", "license", "ignore", "symlink"]
)
def test_cleanup_deletes_verified_lefthook_assets_and_archives_the_rest(
    safety_repo: Path, capsys: pytest.CaptureFixture[str], contents: str
) -> None:
    session = worktree.create("binary", repo_root=safety_repo)
    lefthook_binary.sync_binary(session.path)
    directory = session.path / ".basicly/bin"
    if contents == "unknown":
        (directory / "notes.txt").write_text("retain this", encoding="utf-8")
    elif contents == "binary":
        lefthook_binary.binary_path(session.path).write_bytes(b"consumer executable")
    elif contents == "license":
        (directory / "LICENSE.lefthook").write_text("retain this", encoding="utf-8")
    elif contents == "ignore":
        (directory / ".gitignore").write_text("*\nconsumer rule\n", encoding="utf-8")
    elif contents == "symlink":
        path = lefthook_binary.binary_path(session.path)
        saved = safety_repo.parent / "saved-binary"
        path.rename(saved)
        path.symlink_to(saved)
    before = {
        path.relative_to(session.path).as_posix(): path.read_bytes()
        for path in directory.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    if contents == "symlink":
        with pytest.raises(SystemExit, match="symlink"):
            worktree.cleanup("binary", repo_root=safety_repo)
        assert directory.exists()
        return
    worktree.cleanup("binary", repo_root=safety_repo)
    assert not session.path.exists()
    output = capsys.readouterr().out
    if contents == "managed":
        assert "inventory: " not in output
    else:
        assert test_worktree_safety.archived_files(output) == before


def test_cleanup_deletes_actual_pytest_outputs_without_copying_them(
    safety_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = worktree.create("pytest-output", repo_root=safety_repo)
    source = session.path / "test_producer.py"
    source.write_text("def test_value():\n    assert 2 + 2 == 4\n", encoding="utf-8")
    _git(session.path, "add", "--", source.name)
    _git(session.path, "commit", "-m", "test: prepare the actual pytest producer")
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", source.name],
        cwd=session.path,
        capture_output=True,
        text=True,
        check=True,
    )

    assert (session.path / ".pytest_cache/v/cache/nodeids").is_file()
    assert any((session.path / "__pycache__").iterdir())

    worktree.cleanup(session.name, repo_root=safety_repo)

    assert not session.path.exists()
    assert "inventory: " not in capsys.readouterr().out
    assert not (safety_repo / ".git" / "basicly-preserved").exists()


def test_cleanup_reclaims_generated_verification_cache(
    safety_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = worktree.create("cache-result", repo_root=safety_repo)
    path = session.path / verify_cache.CACHE_PATH
    path.parent.mkdir(parents=True)
    path.with_name(".gitignore").write_text("*\n", encoding="utf-8")
    path.write_text('{"checks": []}\n', encoding="utf-8")
    worktree.cleanup("cache-result", repo_root=safety_repo)
    assert not session.path.exists()
    assert test_worktree_safety.archived_files(capsys.readouterr().out) == {
        verify_cache.CACHE_PATH.as_posix(): b'{"checks": []}\n',
        verify_cache.CACHE_PATH.with_name(".gitignore").as_posix(): b"*\n",
    }


@pytest.mark.parametrize("tamper", ["foreign", "malformed", "symlink", "tracked", "untracked"])
def test_tool_cache_cleanup_archives_unrecognized_content_and_refuses_the_rest(
    safety_repo: Path, capsys: pytest.CaptureFixture[str], tamper: str
) -> None:
    session = worktree.create("cache-control", repo_root=safety_repo)
    directory = session.path / ".pytest_cache"
    (directory / "v/cache").mkdir(parents=True)
    (directory / "CACHEDIR.TAG").write_bytes(b"Signature: 8a477f597d28d172789f06886806bc55\n")
    (directory / ".gitignore").write_bytes(b"# Created by pytest automatically.\n*\n")
    artifact = directory / "v/cache/nodeids"
    artifact.write_text("[]", encoding="utf-8")
    if tamper == "foreign":
        artifact = directory / "user-notes.txt"
        artifact.write_text("user data", encoding="utf-8")
    elif tamper == "malformed":
        artifact.write_text("not json", encoding="utf-8")
    elif tamper == "symlink":
        artifact.unlink()
        artifact.symlink_to(session.path / "README.md")
    elif tamper == "untracked":
        artifact = session.path / "notes.txt"
        artifact.write_text("user data", encoding="utf-8")
    else:
        _git(session.path, "add", "-f", "--", artifact.relative_to(session.path).as_posix())
        _git(session.path, "commit", "-m", "test: track a fixture cache deliberately")
        artifact.write_text('["changed"]', encoding="utf-8")

    if tamper in {"foreign", "malformed"}:
        data = artifact.read_bytes()
        worktree.cleanup(session.name, repo_root=safety_repo)
        assert not session.path.exists()
        assert test_worktree_safety.archived_files(capsys.readouterr().out) == {
            artifact.relative_to(session.path).as_posix(): data
        }
        return

    with pytest.raises(SystemExit):
        worktree.cleanup(session.name, repo_root=safety_repo)

    assert session.path.exists()
    assert artifact.is_symlink() if tamper == "symlink" else artifact.exists()


@pytest.mark.parametrize("path_type", [PurePosixPath, PureWindowsPath])
def test_artifact_paths_cannot_escape_the_worktree(
    tmp_path: Path, path_type: type[PurePosixPath] | type[PureWindowsPath]
) -> None:
    escaped = path_type("../outside/__pycache__").as_posix()
    with pytest.raises(ValueError, match="not relative"):
        worktree_contents.generated_files(tmp_path, f"!! {escaped}")


@pytest.mark.parametrize("raw", [r"\outside\__pycache__", r"..\outside\__pycache__", r"C:\outside"])
def test_windows_rooted_or_parent_paths_are_refused_before_filesystem_access(
    tmp_path: Path, raw: str
) -> None:
    with pytest.raises(ValueError, match="not relative"):
        worktree_contents.generated_files(tmp_path, f"!! {raw}")


@pytest.mark.parametrize("producer", ["ruff", "grimp"])
def test_cleanup_preserves_actual_tool_cache_outputs(safety_repo: Path, producer: str) -> None:
    session = worktree.create("tool-output", repo_root=safety_repo)
    package = session.path / "fixture_package"
    package.mkdir()
    (package / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    _git(session.path, "add", "--", "fixture_package")
    _git(session.path, "commit", "-m", "test: prepare the actual cache producer")
    command = (
        [sys.executable, "-m", "ruff", "check", "fixture_package"]
        if producer == "ruff"
        else [
            sys.executable,
            "-c",
            "import grimp; grimp.build_graph('fixture_package', cache_dir='.import_linter_cache')",
        ]
    )
    subprocess.run(command, cwd=session.path, capture_output=True, text=True, check=True)
    cache = session.path / (".ruff_cache" if producer == "ruff" else ".import_linter_cache")
    assert cache.is_dir()

    worktree.cleanup(session.name, repo_root=safety_repo)

    assert not session.path.exists()
