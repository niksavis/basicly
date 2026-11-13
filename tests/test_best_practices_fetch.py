from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import IO, Any

import pytest
import yaml

SKILL = (
    Path(__file__).resolve().parents[1] / ".basicly" / "core" / "skills" / "best-practices-audit"
)


def _module():
    spec = importlib.util.spec_from_file_location("fetch_docs", SKILL / "scripts" / "fetch_docs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _offline(url: str) -> bytes:
    raise OSError(f"no route to {url}")


def test_every_page_arrives_and_is_stamped(tmp_path: Path) -> None:
    module = _module()

    code = module.main(["--out", str(tmp_path)], get=lambda url: url.encode())

    assert code == module.COMPLETE
    assert (tmp_path / "skills-best-practices.md").read_bytes().endswith(b"best-practices.md")
    assert (tmp_path / "claude-code-index.txt").exists()
    assert (tmp_path / module.STAMP_FILE).exists()


def test_no_network_exits_offline_and_names_the_fallback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()

    code = module.main(["--out", str(tmp_path)], get=_offline)

    assert code == module.OFFLINE == 3
    err = capsys.readouterr().err
    assert "references/checklist.md" in err
    assert "docs were not refreshed" in err
    assert not (tmp_path / module.STAMP_FILE).exists()


def test_a_partial_fetch_names_each_failed_page(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()

    def flaky(url: str) -> bytes:
        if "hooks" in url:
            raise TimeoutError("timed out")
        return b"page"

    code = module.main(["--out", str(tmp_path)], get=flaky)

    assert code == module.PARTIAL
    assert "failed claude-code-hooks:" in capsys.readouterr().err


def test_list_prints_every_source_without_a_fetch(capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()

    assert module.main(["--list"], get=_offline) == module.COMPLETE

    printed = capsys.readouterr().out.splitlines()
    assert [line.split("\t")[0] for line in printed] == list(module.SOURCES)


def test_every_checklist_source_is_a_fetched_source() -> None:
    module = _module()
    checklist = (SKILL / "references" / "checklist.md").read_text(encoding="utf-8")
    rows = [line for line in checklist.splitlines() if line.startswith("| ") and "---" not in line]
    cited = {
        name.strip()
        for row in rows
        if not row.startswith("| Rule ")
        for name in row.rstrip(" |").rsplit("|", 1)[1].split(",")
    }

    assert cited, "the checklist parse found no row"
    assert cited <= set(module.SOURCES), sorted(cited - set(module.SOURCES))


def test_every_source_is_an_https_url_on_a_vendor_docs_host() -> None:
    module = _module()

    for url in module.SOURCES.values():
        assert re.match(
            r"https://(?:(platform|code)\.claude\.com/docs/|platform\.claude\.com/llms\.txt$)", url
        ), url


def test_the_fetcher_refuses_a_scheme_other_than_https() -> None:
    module = _module()

    with pytest.raises(ValueError, match="only https sources are fetched"):
        module.fetch("file:///etc/passwd")


def test_a_failed_page_leaves_no_stale_copy_from_an_earlier_run(tmp_path: Path) -> None:
    module = _module()
    stale = tmp_path / "skills-best-practices.md"
    stale.write_bytes(b"old page")

    def flaky(url: str) -> bytes:
        if url.endswith("best-practices.md"):
            raise OSError("down")
        return url.encode()

    module.main(["--out", str(tmp_path)], get=flaky)
    assert not stale.exists()


def test_an_out_that_names_a_file_is_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as refused:
        _module().main(["--out", str(target)], get=lambda url: url.encode())
    assert refused.value.code == 2
    assert "--out names a file" in capsys.readouterr().err


@pytest.mark.parametrize("marker", ["dir", "file"])
def test_an_out_inside_a_git_tree_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], marker: str
) -> None:
    if marker == "dir":
        (tmp_path / ".git").mkdir()
    else:
        (tmp_path / ".git").write_text("gitdir: x\n", encoding="utf-8")
    with pytest.raises(SystemExit) as refused:
        _module().main(["--out", str(tmp_path / "docs")], get=lambda url: url.encode())
    assert refused.value.code == 2
    assert "inside a git working tree" in capsys.readouterr().err
    assert not (tmp_path / "docs").exists()


def test_a_relative_out_inside_a_git_tree_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    inner = tmp_path / "a" / "b"
    inner.mkdir(parents=True)
    monkeypatch.chdir(inner)
    with pytest.raises(SystemExit):
        _module().main(["--out", "docs"], get=lambda url: url.encode())
    assert not (inner / "docs").exists()


class _Response:
    status = 404
    reason = "Not Found"

    def read(self) -> bytes:
        return b"<html>missing</html>"


class _Connection:
    def __init__(self, host: str, timeout: float) -> None:
        self.host = host
        self.timeout = timeout

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        self.sent = (method, path, headers)

    def getresponse(self) -> _Response:
        return _Response()

    def close(self) -> None:
        self.closed = True


def test_an_error_page_is_a_failure_not_a_doc() -> None:
    with pytest.raises(OSError, match="HTTP 404"):
        _module().fetch("https://code.claude.com/docs/en/x.md", connect=_Connection)


def test_a_page_that_returns_an_error_is_not_saved(tmp_path: Path) -> None:
    module = _module()

    def get(url: str) -> bytes:
        return module.fetch(url, connect=_Connection)

    assert module.main(["--out", str(tmp_path)], get=get) == module.OFFLINE
    assert not (tmp_path / "skills-best-practices.md").exists()


def test_list_includes_the_prompting_guide_for_the_current_fable_anchor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    anchors = yaml.safe_load((SKILL.parents[1] / "models/anchors.yaml").read_text())
    anthropic = next(vendor for vendor in anchors["vendors"] if vendor["id"] == "anthropic")
    guide = (
        f"{_module().PLATFORM}build-with-claude/prompt-engineering/"
        f"prompting-{anthropic['tiers']['maximum']}.md"
    )
    assert _module().main(["--list"], get=_offline) == 0
    assert guide in capsys.readouterr().out


def _indexes(directory: Path, text: str) -> None:
    (directory / "claude-platform-index.txt").write_text(text, encoding="utf-8")
    (directory / "claude-code-index.txt").write_text("# Claude Code Docs\n", encoding="utf-8")


def test_discovery_reports_unlisted_model_guides_without_a_network_call(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()
    candidate = f"{module.PLATFORM}build-with-claude/prompt-engineering/prompting-claude-fable-6.md"
    listed = f"{module.PLATFORM}build-with-claude/prompt-engineering/prompting-claude-fable-5-2.md"
    _indexes(
        tmp_path, f"- [New]({candidate})\n- [Duplicate]({candidate}#effort)\n- [Listed]({listed})"
    )
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    assert module.main(["--discover", "--out", str(tmp_path)], get=_offline) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["candidates"] == [candidate]
    assert result["failed_indexes"] == []
    assert result["omitted"] == 0
    assert result["downloaded"] is False
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


@pytest.mark.parametrize(
    "url",
    [
        "http://platform.claude.com/docs/en/models/fable-6/migration-guide.md",
        "https://platform.claude.com.evil.test/docs/en/models/fable-6/migration-guide.md",
        "https://platform.claude.com@evil.test/docs/en/models/fable-6/migration-guide.md",
        "https://evil.test@platform.claude.com/docs/en/models/fable-6/migration-guide.md",
        "https://platform.claude.com:443/docs/en/models/fable-6/migration-guide.md",
        "https://platform.claude.com/docs/en/models/fable-6/migration-guide.md?token=private",
        "https://platform.claude.com/docs/en/models/../migration-guide.md",
        "https://platform.claude.com/docs/en/models/%2e%2e/migration-guide.md",
        "https://platform.claude.com/docs/en/manage-claude/billing.md",
        "https://code.claude.com/docs/en/../model-config.md",
    ],
)
def test_discovery_excludes_untrusted_or_unrelated_links_with_a_matching_control(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    url: str,
) -> None:
    control = "https://platform.claude.com/docs/en/models/fable-6/migration-guide.md"
    _indexes(tmp_path, f"[Control]({control})\n[Excluded]({url})")

    assert _module().main(["--discover", "--out", str(tmp_path)], get=_offline) == 0
    assert json.loads(capsys.readouterr().out)["candidates"] == [control]


def test_discovery_bounds_output_and_reports_omitted_candidates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()
    count = module.DISCOVERY_LIMIT + 2
    links = [
        f"https://platform.claude.com/docs/en/models/fable-{i}/migration-guide.md"
        for i in range(count)
    ]
    _indexes(tmp_path, "\n".join(f"[Guide]({url})" for url in links))

    assert module.main(["--discover", "--out", str(tmp_path)], get=_offline) == module.PARTIAL
    result = json.loads(capsys.readouterr().out)
    assert len(result["candidates"]) == module.DISCOVERY_LIMIT
    assert result["omitted"] == 2


@pytest.mark.parametrize(
    "content",
    [None, b"\xff", b"x" * (2 * 1024 * 1024 + 1)],
    ids=["missing", "invalid-encoding", "oversized"],
)
def test_discovery_reports_missing_or_unreadable_indexes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: bytes | None,
) -> None:
    _indexes(tmp_path, "# Platform docs\n")
    path = tmp_path / "claude-platform-index.txt"
    if content is None:
        path.unlink()
    else:
        path.write_bytes(content)

    assert _module().main(["--discover", "--out", str(tmp_path)], get=_offline) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["failed_indexes"] == ["claude-platform-index"]
    assert result["downloaded"] is False


def test_discovery_scans_both_indexes_and_normalizes_listed_markdown_links(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()
    _indexes(tmp_path, "# Platform docs\n")
    code_index = tmp_path / "claude-code-index.txt"
    candidate = "https://code.claude.com/docs/en/effort.md"
    code_index.write_text(
        f"[Listed](https://code.claude.com/docs/en/model-config#effort)\n[Effort]({candidate})",
        encoding="utf-8",
    )
    assert module.main(["--discover", "--out", str(tmp_path)], get=_offline) == 0
    assert json.loads(capsys.readouterr().out)["candidates"] == [candidate]


def test_discovery_refuses_a_symlinked_index_without_reading_its_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _indexes(tmp_path, "# Platform docs\n")
    target = tmp_path / "claude-platform-index.txt"
    original_symlink = Path.is_symlink
    original_open = Path.open

    def is_symlink(path: Path) -> bool:
        return path == target or original_symlink(path)

    def open_path(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        assert path != target
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    monkeypatch.setattr(Path, "open", open_path)
    assert _module().main(["--discover", "--out", str(tmp_path)], get=_offline) == 1
    assert json.loads(capsys.readouterr().out)["index_errors"] == {
        "claude-platform-index": "SymlinkedIndex"
    }


def test_discovery_reports_an_unreadable_index_and_continues_with_the_other(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()
    _indexes(tmp_path, "# Platform docs\n")
    control = "https://code.claude.com/docs/en/effort.md"
    (tmp_path / "claude-code-index.txt").write_text(f"[Control]({control})", encoding="utf-8")
    target = tmp_path / "claude-platform-index.txt"
    original = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        if path == target:
            raise PermissionError("index metadata is unreadable")
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    assert module.main(["--discover", "--out", str(tmp_path)], get=_offline) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["candidates"] == [control]
    assert result["index_errors"] == {"claude-platform-index": "PermissionError"}
