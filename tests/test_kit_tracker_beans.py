from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from basicly import cli as engine_cli
from basicly import tracker_import
from basicly.schema import ValidationError
from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "kit_beans_test_cli")
beans = cli.beans

BEANS = {
    "demo-ep01--the-release.md": """---
# demo-ep01
title: 'Release: the first one'
status: in-progress
type: milestone
priority: high
tags:
    - milestone
    - release
created_at: 2026-03-01T10:00:00Z
updated_at: 2026-03-02T10:00:00Z
order: V
---

The release groups the work.
""",
    "demo-ta02--write-the-reader.md": """---
# demo-ta02
title: Write the reader
status: todo
type: task
created_at: 2026-03-03T10:00:00Z
updated_at: 2026-03-03T10:00:00Z
order: "y"
parent: demo-ep01
blocking:
    - demo-bu03
---

Read each bean.

## Tasks
- [ ] parse the frontmatter
""",
    "demo-bu03--fix-the-quote.md": """---
# demo-bu03
title: 'It''s quoted'
status: draft
type: bug
priority: critical
created_at: 2026-03-04T10:00:00Z
updated_at: 2026-03-04T10:00:00Z
blocked_by:
    - demo-ta02
---
""",
    "archive/demo-ol04--an-old-one.md": """---
# demo-ol04
title: An old one
status: todo
type: feature
priority: low
created_at: 2025-12-01T10:00:00Z
updated_at: 2025-12-02T10:00:00Z
---

Done long ago.
""",
    "archive/demo-sc05--dropped.md": """---
title: Dropped
status: scrapped
type: task
priority: deferred
---
""",
}


def _repo(tmp_path: Path, **replaced: str) -> Path:
    repo = tmp_path / "repo"
    for name, text in {**BEANS, **replaced}.items():
        path = repo / ".beans" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (repo / ".beans.yml").write_text("beans:\n  prefix: demo-\n", encoding="utf-8")
    (tmp_path / "ledger").mkdir()
    return repo


def _import(tmp_path: Path, repo: Path, *flags: str) -> tuple[int, dict]:
    argv = ["import", str(tmp_path / "ledger"), str(repo), "--from", "beans", *flags]
    code, report = cli.invoke(cli.arguments.parser().parse_args(argv))
    return code, report


def _shown(tmp_path: Path, record: str) -> dict:
    return cli.read_record(str(tmp_path / "ledger"), record)


def test_every_bean_is_imported_and_an_archived_one_is_closed(tmp_path: Path) -> None:
    code, report = _import(tmp_path, _repo(tmp_path))

    assert code == 0, report
    assert report["imported"] == ["demo-bu03", "demo-ep01", "demo-ol04", "demo-sc05", "demo-ta02"]
    assert report["source"] == ".beans"
    assert report["rejected"] == []
    old = _shown(tmp_path, "demo-ol04")
    assert old["status"] == "closed"
    assert old["fields"]["close_reason"] == "archived in beans at status todo"


def test_status_type_and_priority_map_through_the_vocabulary(tmp_path: Path) -> None:
    _import(tmp_path, _repo(tmp_path))

    held = {record: _shown(tmp_path, record) for record in ("demo-ep01", "demo-ta02", "demo-bu03")}
    scrapped = _shown(tmp_path, "demo-sc05")

    assert [held[one]["status"] for one in held] == ["in_progress", "open", "deferred"]
    assert [held[one]["fields"]["issue_type"] for one in held] == ["epic", "task", "bug"]
    assert [held[one]["fields"]["priority"] for one in held] == [1, 2, 0]
    assert (scrapped["status"], scrapped["fields"]["priority"]) == ("closed", 4)
    assert scrapped["fields"]["close_reason"] == "scrapped in beans"


def test_links_tags_and_body_become_edges_labels_and_description(tmp_path: Path) -> None:
    _import(tmp_path, _repo(tmp_path))

    epic, reader, bug = (_shown(tmp_path, one) for one in ("demo-ep01", "demo-ta02", "demo-bu03"))

    assert [(one["id"], one["dependency_type"]) for one in reader["dependencies"]] == [
        ("demo-ep01", "parent-child")
    ]
    assert [(one["id"], one["dependency_type"]) for one in bug["dependencies"]] == [
        ("demo-ta02", "blocks")
    ]
    assert epic["fields"]["labels"] == ["milestone", "release"]
    assert epic["fields"]["title"] == "Release: the first one"
    assert bug["fields"]["title"] == "It's quoted"
    assert reader["fields"]["description"] == (
        "Read each bean.\n\n## Tasks\n- [ ] parse the frontmatter"
    )
    assert reader["fields"]["order"] == "y"
    assert epic["dates"]["created"] == "2026-03-01T10:00:00Z"


def test_a_dry_run_writes_nothing_and_reports_the_same_summary(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    _, planned = _import(tmp_path, repo, "--dry-run")
    written = list((tmp_path / "ledger").iterdir())
    _, done = _import(tmp_path, repo)

    assert written == []
    assert {**planned, "dry_run": False} == done


@pytest.mark.parametrize(
    ("line", "replaced", "named"),
    [
        ("type: bug\n", "tags: [a, b]\n", "holds tags as '[a, b]'"),
        ("title: 'It''s quoted'\n", "title: >-\n  folded\n", "holds the value '>-'"),
        ("type: bug\n", "estimate: 3\n", "names the key 'estimate'"),
        ("type: bug\n", "type: bug\ntype: bug\n", "names the key 'type' twice"),
        ("status: draft\n", "status: someday\n", "holds status 'someday', which is not one of"),
        ("type: bug\n", "type: chore\n", "holds type 'chore'"),
        ("type: bug\n", "order: yes\n", "holds the plain value 'yes'"),
        ("type: bug\n", "parent: demo-zz99\n", "links to 'demo-zz99'"),
        ("title: 'It''s quoted'\n", "title: 'open\n", "holds the single-quoted value"),
        ("title: 'It''s quoted'\n", 'title: "a \\q"\n', "holds the double-quoted value"),
    ],
)
def test_a_form_the_reader_does_not_know_refuses_the_file_and_the_batch(
    tmp_path: Path, line: str, replaced: str, named: str
) -> None:
    text = BEANS["demo-bu03--fix-the-quote.md"].replace(line, replaced)
    assert text != BEANS["demo-bu03--fix-the-quote.md"]
    repo = _repo(tmp_path, **{"demo-bu03--fix-the-quote.md": text})

    code, report = _import(tmp_path, repo)

    assert code == cli.EXIT_REFUSED
    assert "demo-bu03--fix-the-quote.md" in report["refused"]
    assert named in report["refused"]
    assert list((tmp_path / "ledger").iterdir()) == []


def test_a_bean_with_no_status_or_a_misnamed_file_is_refused_by_name(tmp_path: Path) -> None:
    text = BEANS["demo-bu03--fix-the-quote.md"].replace("status: draft\n", "")
    code, report = _import(tmp_path, _repo(tmp_path, **{"demo-bu03--fix-the-quote.md": text}))
    other = tmp_path / "other"
    other.mkdir()
    misnamed = _repo(other, **{"README.md": "---\ntitle: x\nstatus: todo\n---\n"})

    assert code == cli.EXIT_REFUSED
    assert "demo-bu03--fix-the-quote.md has no status" in report["refused"]
    assert "README.md is not named <id>--<slug>.md" in _import(other, misnamed)[1]["refused"]


def test_a_folder_with_no_beans_is_refused_rather_than_imported_as_empty(tmp_path: Path) -> None:
    (tmp_path / "ledger").mkdir()

    code, report = _import(tmp_path, tmp_path)

    assert code == cli.EXIT_REFUSED
    assert "holds no beans backlog" in report["refused"]


def _choices(parser: argparse.ArgumentParser, *path: str) -> tuple:
    for name in path:
        sub = next(one for one in parser._actions if isinstance(one, argparse._SubParsersAction))
        parser = sub.choices[name]
    found = next(one for one in parser._actions if one.dest == "source_format")
    return tuple(found.choices or ())


def test_both_parsers_offer_exactly_the_formats_the_kit_reads() -> None:
    assert tuple(beans.READERS) == cli.arguments.IMPORT_FORMATS
    assert _choices(cli.arguments.parser(), "import") == tuple(beans.READERS)
    assert _choices(engine_cli._build_parser(), "tracker", "import") == tuple(beans.READERS)


def test_the_engine_imports_a_beans_backlog(tmp_path: Path) -> None:
    host = tmp_path / "host"
    shutil.copytree(REPO_ROOT / KIT_RELATIVE, host / KIT_RELATIVE)
    (host / ".basicly" / "ledger").mkdir(parents=True)
    repo = _repo(tmp_path)

    planned = tracker_import.run_import(host, repo, dry_run=True, source_format="beans")
    code, lines = tracker_import.run_import(host, repo, source_format="beans")

    assert planned[1][0].startswith("import .beans (dry run, nothing written): 5 new record(s)")
    assert (code, lines[0]) == (0, "import .beans: 5 record(s) created, 12 event(s) appended")


def test_the_engine_refuses_a_bean_file_it_cannot_read_by_name(tmp_path: Path) -> None:
    host = tmp_path / "host"
    shutil.copytree(REPO_ROOT / KIT_RELATIVE, host / KIT_RELATIVE)
    (host / ".basicly" / "ledger").mkdir(parents=True)
    repo = _repo(tmp_path, **{"demo-zz09--bad.md": "---\ntitle: x\nstatus: todo\ntags: [a]\n---\n"})

    with pytest.raises(ValidationError, match=r"demo-zz09--bad\.md holds tags as '\[a\]'"):
        tracker_import.run_import(host, repo, source_format="beans")
    assert list((host / ".basicly" / "ledger").iterdir()) == []
