from __future__ import annotations

from pathlib import Path

from tests import test_kit_tracker_claims as claims
from tests.test_kit_tracker_claims import SHAPED, _git, _use_gate
from tests.tracker_claims_fixture import _kit, _review

repo = claims.repo


def _redirected_lane(repo: Path, tmp_path: Path, name: str) -> tuple[Path, str]:
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", "chore: install tracker").returncode == 0
    _use_gate(repo, "engine")
    record = _kit(repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", name, *SHAPED)[
        "record"
    ]
    _git(repo, "add", ".basicly/ledger")
    assert _git(repo, "commit", "-q", "-m", f"chore: file {record}").returncode == 0
    lane = tmp_path / name
    assert _git(repo, "worktree", "add", "-b", name, str(lane)).returncode == 0
    (lane / ".basicly/ledger/redirect").write_text(str(repo), encoding="utf-8")
    (lane / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(lane, "add", "app.py")
    return lane, record


def test_a_claim_committed_on_main_after_the_cut_authorizes_a_redirected_lane(
    repo: Path, tmp_path: Path
) -> None:
    lane, record = _redirected_lane(repo, tmp_path, "claimed-after-cut")
    _review(repo, record)
    _kit(repo, "claim", ".basicly/ledger", record)
    _git(repo, "add", ".basicly/ledger")
    assert _git(repo, "commit", "-q", "-m", f"chore: claim {record}").returncode == 0

    done = _git(lane, "commit", "-q", "-m", f"feat: value {record}")

    assert done.returncode == 0, done.stderr


def test_a_claim_written_on_main_but_not_committed_cannot_authorize_a_redirected_lane(
    repo: Path, tmp_path: Path
) -> None:
    lane, record = _redirected_lane(repo, tmp_path, "claim-not-committed")
    _review(repo, record)
    _kit(repo, "claim", ".basicly/ledger", record)

    refused = _git(lane, "commit", "-q", "-m", f"feat: value {record}")

    assert refused.returncode != 0
    assert "Claim the record first" in refused.stderr


def test_a_record_nobody_holds_is_refused_in_a_redirected_lane(repo: Path, tmp_path: Path) -> None:
    lane, record = _redirected_lane(repo, tmp_path, "nobody-holds")

    refused = _git(lane, "commit", "-q", "-m", f"feat: value {record}")

    assert refused.returncode != 0
    assert "Claim the record first" in refused.stderr


def test_a_broken_redirect_is_refused_by_name(repo: Path, tmp_path: Path) -> None:
    lane, record = _redirected_lane(repo, tmp_path, "broken-redirect")
    (lane / ".basicly/ledger/redirect").write_text(str(tmp_path / "nowhere"), encoding="utf-8")

    refused = _git(lane, "commit", "-q", "-m", f"feat: value {record}")

    assert refused.returncode != 0
    assert "ledger redirect" in refused.stderr
