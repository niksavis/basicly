"""The tracker-surface census: what the harness reaches, against what `br`/`bv` ship.

Written when the §9.4 naming gate was made binding (basicly-u2hl.14). The module was
extracted from `usage_report` and inherited **no** tests — the census that sizes the work
tracker's replacement had never been run by anything but a human at a prompt, which is
the same "built, shipped, never exercised" shape the capability gate exists to refuse.

`cmd_tracker` reads `Path.cwd()`, so every test chdirs; it prints through `ui`, so every
assertion is on captured stdout or on the `--json` payload. The two side effects are
asserted to be **off** unless their flag is passed, because "the plain report is
read-only" is a claim about a command a human runs on a checkout they care about.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from basicly import surface_report, tracker_surface, tracker_usage

INVENTORY = {
    "schema": tracker_surface.SCHEMA,
    "br": {
        "commands": ["show", "create", "dep", "dep add", "audit", "audit log"],
        "groups": ["dep", "audit"],
        "global_flags": [],
        "version": "br 0.2.16",
    },
    "bv": {"commands": [], "flags": ["--agents"], "version": "bv 0.1.0"},
}


def _args(**overrides) -> argparse.Namespace:
    """The namespace `basicly usage tracker` builds, with every flag off by default."""
    fields = {"promote": False, "refresh_surface": False, "as_json": False}
    fields.update(overrides)
    return argparse.Namespace(**fields)


def _record(repo: Path, *entries: dict) -> None:
    """Write measured invocations into the committed ledger."""
    path = repo / tracker_usage.LEDGER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries),
        encoding="utf-8",
    )


def _call(binary: str, subcommand: str, site: str = tracker_usage.SITE_ENGINE) -> dict:
    return {"binary": binary, "subcommand": subcommand, "site": site, "ok": True}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout the command is run from, with the ledger directory opted in."""
    (tmp_path / tracker_usage.LEDGER_FILE).parent.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _report(capsys: pytest.CaptureFixture[str], **overrides) -> dict:
    """Run the command in JSON mode and return the payload it printed."""
    assert surface_report.cmd_tracker(_args(as_json=True, **overrides)) == 0
    return json.loads(capsys.readouterr().out)


# --- the classification that sizes the replacement -----------------------------


def test_a_surface_an_engine_path_reaches_is_a_hard_requirement(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Engine, interactive-only and both are three different scope decisions.

    A harness phase breaks without the engine's set; an interactive-only surface can be
    served later or never (`work-tracker.md` §6), which is the whole point of the split.
    """
    tracker_surface.save(repo, INVENTORY)
    _record(
        repo,
        _call("br", "show"),
        _call("br", "create", tracker_usage.SITE_INTERACTIVE),
        _call("br", "dep add"),
        _call("br", "dep add", tracker_usage.SITE_INTERACTIVE),
    )

    classes = {row["subcommand"]: row["surface_class"] for row in _report(capsys)["used"]}

    assert classes == {
        "show": "engine",
        "create": "interactive-only",
        "dep add": "engine+interactive",
    }


# --- the never-used set, printed in full ---------------------------------------


def test_the_never_used_set_is_reported_in_full_rather_than_counted(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """This is the set Phase 6 gets to not build, so "and 26 more" would hide it."""
    tracker_surface.save(repo, INVENTORY)
    _record(repo, _call("br", "show"))

    assert surface_report.cmd_tracker(_args()) == 0

    out = capsys.readouterr().out
    for never_used in ("create", "dep", "dep add", "audit", "audit log"):
        assert never_used in out, out
    assert "more" not in out


def test_a_group_namespace_is_distinguished_from_an_unused_operation(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`dep` is a namespace, `dep add` is an operation; only the second is work.

    Counting a namespace as an unbuilt surface would inflate the scope estimate the
    freeze is taken from.
    """
    tracker_surface.save(repo, INVENTORY)
    _record(repo, _call("br", "show"))

    assert surface_report.cmd_tracker(_args()) == 0

    assert "a group namespace rather than an operation" in capsys.readouterr().out


def test_a_binary_with_nothing_left_over_says_so(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The positive control for the never-used report: an empty set is stated, not silent."""
    tracker_surface.save(repo, {**INVENTORY, "bv": {"commands": [], "flags": [], "version": "x"}})
    _record(repo, _call("br", "show"))

    assert surface_report.cmd_tracker(_args()) == 0

    assert "bv: every one of its 0 known surfaces is used." in capsys.readouterr().out


def test_a_measured_surface_the_inventory_does_not_list_is_surfaced(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Either br drifted or the recorder invented a surface; the freeze must not inherit it."""
    tracker_surface.save(repo, INVENTORY)
    _record(repo, _call("br", "invented"))

    payload = _report(capsys)

    assert payload["used_but_not_in_inventory"] == [["br", "invented"]]


def test_no_committed_inventory_leaves_the_never_used_set_unknown(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unknown, never empty: an absent inventory must not read as "everything is used"."""
    _record(repo, _call("br", "show"))

    payload = _report(capsys)

    assert payload["never_used"] == {}
    assert any("never-used set is unknown" in note for note in payload["notes"])
    assert payload["inventory_br_version"] is None


@pytest.mark.usefixtures("repo")
def test_an_empty_ledger_says_where_to_look_rather_than_reporting_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Zero rows is "nothing measured yet", which is not the same as "nothing is used"."""
    assert surface_report.cmd_tracker(_args()) == 0

    assert "No tracker usage recorded yet" in capsys.readouterr().out


# --- the two side effects are opt-in -------------------------------------------


def test_the_plain_report_re_probes_nothing_and_promotes_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read-only is a claim about a command a human runs on a checkout they care about.

    Both side effects fail the test if reached, so the property cannot be satisfied by a
    run that happens to have nothing to do.
    """
    monkeypatch.setattr(
        surface_report.tracker_surface,
        "discover",
        lambda *_a, **_k: pytest.fail("the plain report must not re-probe"),
    )
    monkeypatch.setattr(
        surface_report.tracker_usage,
        "promote",
        lambda *_a, **_k: pytest.fail("the plain report must not promote"),
    )
    tracker_surface.save(repo, INVENTORY)
    _record(repo, _call("br", "show"))

    assert surface_report.cmd_tracker(_args()) == 0


def test_refresh_surface_rewrites_the_committed_inventory(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refresh is a re-probe of recorded state, not a judgement about it."""
    monkeypatch.setattr(surface_report.br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(surface_report.tracker_surface, "discover", lambda *_a, **_k: INVENTORY)
    _record(repo, _call("br", "show"))

    assert surface_report.cmd_tracker(_args(refresh_surface=True)) == 0

    assert tracker_surface.load(repo) == INVENTORY
    assert "Wrote 6 br surface(s) and 1 bv flag(s)" in capsys.readouterr().out


def test_no_br_on_path_reports_the_inventory_unchanged_rather_than_failing(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A consumer without br installed still gets the report the ledger supports."""
    monkeypatch.setattr(surface_report.br, "which", lambda: None)
    tracker_surface.save(repo, INVENTORY)
    _record(repo, _call("br", "show"))

    assert surface_report.cmd_tracker(_args(refresh_surface=True)) == 0

    out = capsys.readouterr().out
    assert "br is not on PATH" in out
    assert tracker_surface.load(repo) == INVENTORY


def test_promote_folds_the_spool_and_names_what_it_refused(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A discarded record is reported, because a silent drop looks like an empty spool."""
    monkeypatch.setattr(surface_report.tracker_usage, "promote", lambda *_a, **_k: (3, 2))
    _record(repo, _call("br", "show"))

    assert surface_report.cmd_tracker(_args(promote=True)) == 0

    out = capsys.readouterr().out
    assert "Promoted 3 spooled record(s)" in out
    assert "Discarded 2 spooled record(s)" in out


def test_an_empty_spool_says_so_rather_than_claiming_a_promotion(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control for the line above: nothing moved is stated, not reported as a promotion."""
    monkeypatch.setattr(surface_report.tracker_usage, "promote", lambda *_a, **_k: (0, 0))
    _record(repo, _call("br", "show"))

    assert surface_report.cmd_tracker(_args(promote=True)) == 0

    assert "Nothing spooled to promote." in capsys.readouterr().out


# --- the JSON payload the freeze is taken from ---------------------------------


def test_the_json_payload_carries_every_half_of_the_census(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The freeze is taken from this document, so a missing key is a missing decision."""
    tracker_surface.save(repo, INVENTORY)
    _record(repo, _call("br", "show"), _call("br", "close"))

    payload = _report(capsys)

    assert set(payload) == {
        "used",
        "never_used",
        "used_but_not_in_inventory",
        "calls_by_access",
        "inventory_br_version",
        "notes",
    }
    assert payload["inventory_br_version"] == "br 0.2.16"
    assert payload["calls_by_access"] == {"read": 1, "write": 1}
    assert sorted(payload["never_used"]["br"]) == ["audit", "audit log", "create", "dep", "dep add"]
