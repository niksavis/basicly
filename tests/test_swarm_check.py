from __future__ import annotations

from pathlib import Path

import pytest

from tests.swarm_fixtures import _finding, _reply, swarm


def test_a_missing_reply_is_not_run_and_fails_the_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _reply(tmp_path, "correctness", {"lens": "correctness", "findings": []})
    assert swarm.main(["check", "--lenses", "correctness,security", "--dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "| security | not run: no security.md |" in out
    assert "incomplete: security not run or malformed" in out


def test_a_reply_without_a_json_block_is_malformed(tmp_path: Path) -> None:
    _reply(tmp_path, "facts", None, prose="I think it is fine.\n")
    assert swarm.read_report(tmp_path, "facts").status == "malformed"


def test_a_verdict_other_than_pass_or_fail_is_malformed(tmp_path: Path) -> None:
    _reply(tmp_path, "facts", {"verdict": "maybe", "findings": []})
    assert swarm.read_report(tmp_path, "facts").problem == "verdict must be pass or fail"


def test_an_unknown_severity_is_malformed(tmp_path: Path) -> None:
    _reply(tmp_path, "facts", {"findings": [_finding("critical", "a.py:1")]})
    report = swarm.read_report(tmp_path, "facts")
    assert (report.status, "severity must be one of" in report.problem) == ("malformed", True)


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("src/a.py:12", True),
        ("$ pytest -q\n3 passed", True),
        ("ran: rg foo", True),
        ("I believe so", False),
    ],
)
def test_evidence_needs_a_location_or_a_command(evidence: str, expected: bool) -> None:
    assert swarm.cited({"evidence": evidence}) is expected


def test_findings_citing_a_shared_line_stay_separate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shared = "src/registry.py:18"
    findings = [
        _finding("important", shared, "x: delete"),
        _finding("important", shared, "y: keep"),
    ]
    _reply(tmp_path, "usage", {"findings": findings})
    swarm.main(["check", "--lenses", "usage", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "- [important] usage: x: delete\n" in out
    assert "- [important] usage: y: keep\n" in out


def test_command_evidence_never_links_findings(tmp_path: Path) -> None:
    _reply(tmp_path, "a", {"findings": [_finding("important", "ran: pytest a", "one")]})
    _reply(tmp_path, "b", {"findings": [_finding("nit", "ran: ruff", "two")]})
    merged = swarm.merge([swarm.read_report(tmp_path, "a"), swarm.read_report(tmp_path, "b")])
    assert [m["also"] for m in merged] == [[], []]


def test_the_verdict_is_shown_in_the_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _reply(tmp_path, "a", {"verdict": "fail", "findings": []})
    swarm.main(["check", "--lenses", "a", "--dir", str(tmp_path)])
    assert "| a | ok | fail |" in capsys.readouterr().out


def test_an_unverified_finding_is_marked_in_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _reply(tmp_path, "a", {"findings": [_finding("important", "trust me", claim="bad")]})
    assert swarm.main(["check", "--lenses", "a", "--dir", str(tmp_path)]) == 0
    assert "- [important UNCITED] a: bad" in capsys.readouterr().out


def test_a_reply_naming_another_lens_is_malformed(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        '```json\n{"lens": "zzz", "verdict": "pass"}\n```\n', encoding="utf-8"
    )
    assert swarm.read_report(tmp_path, "a").problem == "the json block's lens is 'zzz', not 'a'"


def test_a_reply_without_the_field_its_command_needs_is_malformed(tmp_path: Path) -> None:
    _reply(tmp_path, "a", {"findings": []})
    assert swarm.read_report(tmp_path, "a", "vote").problem == "the json block has no 'vote' field"


def test_a_lens_named_twice_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="named twice"):
        swarm.main(["check", "--lenses", "a,a", "--dir", str(tmp_path)])


@pytest.mark.parametrize(
    ("raw", "problem"),
    [
        ("{bad", "json error at line 1"),
        ("[]", "the json block is not an object"),
        ('{"lens": "a", "verdict": "pass", "findings": {}}', "findings is not a list of objects"),
    ],
)
def test_a_broken_json_block_is_malformed_with_its_reason(
    tmp_path: Path, raw: str, problem: str
) -> None:
    (tmp_path / "a.md").write_text(f"```json\n{raw}\n```\n", encoding="utf-8")
    report = swarm.read_report(tmp_path, "a")
    assert (report.status, report.problem) == ("malformed", problem)


def test_two_lenses_on_one_line_note_each_other(tmp_path: Path) -> None:
    _reply(tmp_path, "a", {"findings": [_finding("nit", "src/x.py:3")]})
    _reply(tmp_path, "b", {"findings": [_finding("important", "src/x.py:3")]})
    merged = swarm.merge([swarm.read_report(tmp_path, "a"), swarm.read_report(tmp_path, "b")])
    assert [(m["lens"], m["severity"], m["also"]) for m in merged] == [
        ("b", "important", ["a"]),
        ("a", "nit", ["b"]),
    ]
