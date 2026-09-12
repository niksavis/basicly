from __future__ import annotations

from pathlib import Path

import pytest

from basicly import cli, release


@pytest.fixture
def stub_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    plan = release.ReleasePlan(current_version="0.5.1", version="0.6.0", date="2026-07-26", pins=())
    seen: dict = {}
    monkeypatch.setattr(release, "plan_release", lambda *_a, **_k: plan)

    def fake_run(_repo, _plan, **kwargs):
        seen.update(kwargs)
        return release.ReleaseResult(
            plan=plan, steps=("did a thing",), dry_run=bool(kwargs.get("dry_run")), tagged=False
        )

    monkeypatch.setattr(release, "run_release", fake_run)
    return seen


@pytest.mark.usefixtures("stub_release")
def test_a_successful_release_exits_zero_and_reports_each_step(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(["release", "0.6.0", "--issue", "x-1"])

    out = capsys.readouterr().out
    assert code == 0
    assert "release:  v0.5.1 -> v0.6.0 on 2026-07-26" in out
    assert "step:     did a thing" in out


@pytest.mark.usefixtures("stub_release")
def test_a_refusal_exits_one_and_goes_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = release.plan_release(Path(), "0.6.0")
    monkeypatch.setattr(
        release,
        "run_release",
        lambda *_a, **_k: release.ReleaseResult(
            plan=plan, steps=(), dry_run=False, tagged=False, refusals=("tree is not clean",)
        ),
    )

    code = cli.main(["release", "0.6.0", "--issue", "x-1"])

    captured = capsys.readouterr()
    assert code == 1
    assert "refused:  tree is not clean" in captured.err
    assert "refused" not in captured.out


def test_the_autonomy_flags_reach_the_engine(stub_release: dict) -> None:
    assert (
        cli.main([
            "release",
            "0.6.0",
            "--issue",
            "x-1",
            "--autonomous",
            "--root",
            "epic",
            "--shipping",
            "epic.3",
            "--dry-run",
        ])
        == 0
    )

    assert stub_release["autonomous"] is True
    assert stub_release["root_issue"] == "epic"
    assert stub_release["shipping"] == "epic.3"
    assert stub_release["dry_run"] is True
    assert stub_release["issue_id"] == "x-1"


@pytest.mark.usefixtures("stub_release")
def test_root_without_autonomous_is_refused_rather_than_ignored(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(["release", "0.6.0", "--issue", "x-1", "--root", "epic"])

    assert code == 1
    assert "--root only applies with --autonomous" in capsys.readouterr().err


def test_the_issue_flag_is_required() -> None:
    with pytest.raises(SystemExit):
        cli.main(["release", "0.6.0"])
