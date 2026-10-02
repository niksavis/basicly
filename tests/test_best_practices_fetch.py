from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

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

    assert code == module.OFFLINE
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
        assert re.match(r"https://(platform|code)\.claude\.com/docs/", url), url


def test_the_fetcher_refuses_a_scheme_other_than_https() -> None:
    module = _module()

    with pytest.raises(ValueError, match="only https sources are fetched"):
        module.fetch("file:///etc/passwd")
