from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "protect-generated-commit.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("protect_generated_commit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load_hook()


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def test_hash_bytes_matches_sha256_of_text_form() -> None:
    assert hook.hash_bytes(b"hello") == _sha(b"hello")


def test_manifest_hashes_extracts_output_hashes(tmp_path: Path) -> None:
    manifest = tmp_path / "generated-manifest.json"
    manifest.write_text(
        json.dumps({
            "outputs": {
                "AGENTS.md": {"hash": "sha256:abc", "source_fragments": ["x"]},
                "bad-entry": {"no_hash": True},
                "wrong-type": "nope",
            }
        }),
        encoding="utf-8",
    )
    assert hook.manifest_hashes(manifest) == {"AGENTS.md": "sha256:abc"}


def test_manifest_hashes_empty_on_corrupt_or_shapeless(tmp_path: Path) -> None:
    corrupt = tmp_path / "c.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert hook.manifest_hashes(corrupt) == {}
    shapeless = tmp_path / "s.json"
    shapeless.write_text(json.dumps({"outputs": []}), encoding="utf-8")
    assert hook.manifest_hashes(shapeless) == {}


def test_find_manifest_default_then_search_then_none(tmp_path: Path) -> None:
    assert hook.find_manifest(tmp_path) is None
    nested = tmp_path / ".basicly" / "nested"
    nested.mkdir(parents=True)
    found = nested / "generated-manifest.json"
    found.write_text("{}", encoding="utf-8")
    assert hook.find_manifest(tmp_path) == found
    default = tmp_path / ".basicly" / "generated-manifest.json"
    default.write_text("{}", encoding="utf-8")
    assert hook.find_manifest(tmp_path) == default


def test_violations_flags_only_diverged_generated_files() -> None:
    hashes = {"AGENTS.md": _sha(b"good"), "CLAUDE.md": _sha(b"clean")}
    blobs = {"AGENTS.md": b"TAMPERED", "CLAUDE.md": b"clean", "other.py": b"x"}
    staged = ["AGENTS.md", "CLAUDE.md", "other.py", "gone.md"]
    result = hook.violations(hashes, staged, blobs.get)
    assert result == ["AGENTS.md"]


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=path, check=True)


def _seed_generated(repo: Path, rel: str, content: bytes) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    manifest = repo / ".basicly" / "generated-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"version": "1", "outputs": {rel: {"hash": _sha(content)}}}),
        encoding="utf-8",
    )


def test_main_passes_when_staged_generated_file_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _seed_generated(tmp_path, "AGENTS.md", b"generated body\n")
    subprocess.run(["git", "add", "AGENTS.md"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert hook.main() == 0


def test_main_blocks_when_staged_generated_file_is_tampered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_repo(tmp_path)
    _seed_generated(tmp_path, "AGENTS.md", b"generated body\n")
    (tmp_path / "AGENTS.md").write_bytes(b"hand-edited body\n")
    subprocess.run(["git", "add", "AGENTS.md"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert hook.main() == hook.BLOCK_EXIT_CODE
    assert "AGENTS.md" in capsys.readouterr().err


def test_main_exits_zero_with_no_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert hook.main() == 0


def test_main_ignores_a_staged_deletion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _seed_generated(tmp_path, "AGENTS.md", b"generated body\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True)
    subprocess.run(["git", "rm", "-q", "AGENTS.md"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert hook.main() == 0
