from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_kit_vault_shapes import WRITER_A, WRITER_B, Vault, _segment
from tests.test_kit_vault_share import _machine, bare

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vault-migrate"
HOME = "/home" + "/ada/work/acme"
__all__ = ["bare"]


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vault_migrate = _load("basicly_tracker_kit_vault_migrate", "vault_migrate.py")
events = vault_migrate.events
shapes = vault_migrate.vault_shapes
git_runner = vault_migrate.git_runner


def _ledger(folder: Path) -> Path:
    drafts = [
        events.Draft("acme-a1", "created", {"title": "one", "assignee": "ada"}),
        events.Draft("acme-a1", "note", {"text": f"seen in {HOME}/src by ada"}),
        events.Draft("acme-b2", "created", {"title": "two"}),
        events.Draft("acme-b2", "field", {"name": "assignee", "value": "ada"}),
        events.Draft("acme-b2", "status", {"status": "closed"}),
        events.Draft("acme-a1", "edge", {"target": "acme-b2", "edge_type": "blocks"}),
    ]
    events.append(folder, drafts[:4], actor="ada", clock=lambda: 1000.0)
    events.append(folder, drafts[4:], actor="ada", clock=lambda: 2000.0, writer="main")
    return folder


def _plan(tmp_path: Path, bare: Path, **options: Any) -> Any:
    clone, _state = _machine(tmp_path, bare, "importer")
    return vault_migrate.Import(clone, WRITER_A, time.time, **options)


def _head(bare: Path) -> str:
    return git_runner.run(["rev-parse", "main"], bare).strip()


def _source_digest(found: list, plan: Any) -> str:
    lines = [
        vault_migrate.line_of(vault_migrate.scrub(e, plan.redactor), WRITER_A, 0.0) for e in found
    ]
    carried = vault_migrate.vault_view.carried(lines, vault_migrate.EVERY_PROJECT)
    return vault_migrate.digest(carried, {e.record for e in found})


def test_a_ledger_lands_in_fold_order_with_its_ids_and_the_digests_agree(
    tmp_path: Path, bare: Path
) -> None:
    ledger = _ledger(tmp_path / "ledger")
    assert [p.name for p in events.ledger_paths(ledger)] == [
        "events-0001.jsonl",
        "pending-main.jsonl",
    ]
    plan = _plan(tmp_path, bare)
    found = vault_migrate.ledger_events(ledger)

    shown = vault_migrate.ledger_to_vault(ledger, plan)

    verdicts = shapes.check(bare, "main")
    assert all(v.valid for v in verdicts)
    landed = [e for e in shapes.valid_events(verdicts) if e.get("kind") == "ledger"]
    assert [e["ledger"]["id"] for e in landed] == [e.id for e in events.canonical_order(found)]
    assert {e["writer"] for e in landed} == {WRITER_A}
    assert (shown["events"], shown["commits"], shown["tip"]) == (6, 1, _head(bare))
    assert shown["digest"] == _source_digest(found, plan)
    assert shown["not_in_view"] == 1


def test_commits_stay_within_one_mebibyte_and_one_writer(tmp_path: Path, bare: Path) -> None:
    note = "x" * 600
    found = [
        events.Event(f"acme-a1#ev-{n:06d}", "acme-a1", n + 1, "note", "ada", "t", {"text": note})
        for n in range(4000)
    ]
    plan = _plan(tmp_path, bare)

    shown = vault_migrate.to_vault(found, plan)

    made = git_runner.run(["rev-list", "--reverse", "main"], bare).split()[1:]
    assert len(made) == shown["commits"] >= 3
    for commit in made:
        diff = git_runner.run(["diff", "--numstat", f"{commit}^", commit], bare).split()
        assert diff[2].startswith(f"log/{WRITER_A}/")
        added = git_runner.run(["diff", "-U0", f"{commit}^", commit], bare)
        body = "".join(ln[1:] + "\n" for ln in added.splitlines() if ln.startswith("+{"))
        assert len(body.encode("utf-8")) <= vault_migrate.COMMIT_BYTES
    assert shown["events"] == 4000


def test_a_vault_that_already_holds_a_record_refuses_by_both_digests(
    tmp_path: Path, bare: Path
) -> None:
    other = events.Event("acme-a1#ev-other", "acme-a1", 9, "note", "bob", "t", {"text": "x"})
    first = vault_migrate.Import(_machine(tmp_path, bare, "first")[0], WRITER_B, time.time)
    vault_migrate.to_vault([other], first)
    before = _head(bare)

    with pytest.raises(vault_migrate.MigrateError, match="fold digest differs") as refused:
        vault_migrate.ledger_to_vault(_ledger(tmp_path / "ledger"), _plan(tmp_path, bare))

    said = str(refused.value)
    assert said.count(", vault view ") == 1
    assert said.count(": ledger ") == 1
    assert _head(bare) == before


def test_a_second_import_of_the_same_ledger_refuses_by_name(tmp_path: Path, bare: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    vault_migrate.ledger_to_vault(ledger, _plan(tmp_path, bare))
    before = _head(bare)

    with pytest.raises(vault_migrate.MigrateError, match="already carries 6 of the 6 events"):
        vault_migrate.ledger_to_vault(ledger, _plan(tmp_path, bare))
    assert _head(bare) == before


def test_an_ended_vault_refuses_by_both_counts(tmp_path: Path, bare: Path) -> None:
    work = Vault(tmp_path / "work")
    work.git("pull", "-q", str(bare), "main")
    stop = {"kind": "ended", "writer": WRITER_B, "id": "e1", "payload": {"reason": "moved"}}
    work.write(f"log/{WRITER_B}/00001.jsonl", _segment(WRITER_B, stop))
    work.commit("ended")
    work.git("push", "-q", str(bare), "HEAD:main")
    before = _head(bare)

    with pytest.raises(vault_migrate.MigrateError, match="ledger 6, vault 0"):
        vault_migrate.ledger_to_vault(_ledger(tmp_path / "ledger"), _plan(tmp_path, bare))
    assert _head(bare) == before


def test_a_snapshot_that_folded_more_events_than_the_logs_refuses(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    header = {"event_count": 99, "log_lines": 99, "last_event_id": None, "version": 3}
    (ledger / "snapshot.jsonl").write_text(json.dumps(header) + "\n", encoding="utf-8")

    with pytest.raises(vault_migrate.MigrateError, match=r"folded 99 events .* hold 6"):
        vault_migrate.ledger_events(ledger)


def test_the_share_rules_redact_each_line_and_spare_the_holder_name(
    tmp_path: Path, bare: Path
) -> None:
    def names(text: str) -> str:
        return text.replace("ada", "<redacted:machine-username>")

    plan = _plan(tmp_path, bare, redactor=names)

    shown = vault_migrate.ledger_to_vault(_ledger(tmp_path / "ledger"), plan)

    landed = {
        e["ledger"]["id"]: e["ledger"]
        for e in shapes.valid_events(shapes.check(bare, "main"))
        if e.get("kind") == "ledger"
    }
    text = json.dumps(list(landed.values()))
    assert "/home" + "/" not in text
    assert {e["actor"] for e in landed.values()} == {"<redacted:machine-username>"}
    holders = [e["payload"].get("assignee") or e["payload"].get("value") for e in landed.values()]
    assert holders.count("ada") == 2
    assert shown["redacted"] == 6


@pytest.mark.parametrize(
    ("form", "export", "records"),
    [
        ("beads", "beads.jsonl", {"acme-bd01", "acme-bd02"}),
        ("beads", "br.jsonl", {"acme-br01", "acme-br02"}),
        ("beans", "beans", {"demo-ep01", "demo-ta02"}),
    ],
)
def test_a_beads_br_or_beans_backlog_lands_with_its_record_ids(
    tmp_path: Path, bare: Path, form: str, export: str, records: set
) -> None:
    source = vault_migrate.beans.Source(form, FIXTURES / export, "backlog")

    shown = vault_migrate.backlog_to_vault(source, _plan(tmp_path, bare))

    landed = shapes.valid_events(shapes.check(bare, "main"))
    assert {e["record"] for e in landed if e.get("kind") == "ledger"} == records
    assert shown["records"] == 2
    assert shown["events"] == len(landed)


def test_a_prefix_exports_in_vault_order_and_folds_as_the_view(tmp_path: Path, bare: Path) -> None:
    vault_migrate.ledger_to_vault(_ledger(tmp_path / "ledger"), _plan(tmp_path, bare))
    clone = _machine(tmp_path, bare, "reader")[0]
    target = tmp_path / "exported"

    shown = vault_migrate.from_vault(clone, "acme", target, clock=time.time)

    valid = vault_migrate.vault_clone.valid_events(clone)
    view = vault_migrate.view_events(valid, vault_migrate.vault_view.Want("acme"))
    written = events.read_events(target)[0]
    assert [e.id for e in written] == [e.id for e in view]
    assert shown["digest"] == vault_migrate.digest(view, {"acme-a1", "acme-b2"})
    assert (shown["events"], shown["records"]) == (5, 2)
    with pytest.raises(vault_migrate.MigrateError, match="already holds ledger files"):
        vault_migrate.from_vault(clone, "acme", target, clock=time.time)
