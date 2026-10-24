from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from basicly import checkout, release_history
from tests.release_history_fixture import HISTORY, git, init, manifest, published, tag


@pytest.fixture
def publisher(tmp_path: Path) -> Path:
    author = init(tmp_path / "author")
    first = tag(author, "v0.1.0", "first\n", annotated=True)
    tag(author, "v0.2.0", "second\n")
    git(author, "tag", "backup")
    published(author, tmp_path / "publisher.git", ["v0.1.0", "v0.2.0", "backup"])
    manifest(author, {"v0.1.0": first})
    return author


def test_origin_advertisement_uses_peeled_commits_and_ignores_nonversion_tags(
    publisher: Path,
) -> None:
    assert release_history.advertised(publisher) == {
        name: git(publisher, "rev-parse", f"{name}^{{commit}}") for name in ("v0.1.0", "v0.2.0")
    }
    assert git(publisher, "rev-parse", "v0.1.0") != release_history.read(publisher)["v0.1.0"]


def test_audit_plans_new_published_history_without_writing_it(publisher: Path) -> None:
    before = (publisher / HISTORY).read_bytes()

    plan = release_history.audit(publisher, "0.3.0")

    assert plan.added == ("v0.2.0",)
    assert plan.tags == release_history.advertised(publisher)
    assert (publisher / HISTORY).read_bytes() == before


@pytest.mark.parametrize("change", ["missing", "moved"])
def test_audit_refuses_a_changed_published_commit(publisher: Path, change: str) -> None:
    remote = Path(git(publisher, "remote", "get-url", "origin"))
    if change == "missing":
        git(remote, "update-ref", "-d", "refs/tags/v0.1.0")
    else:
        git(remote, "update-ref", "refs/tags/v0.1.0", git(publisher, "rev-parse", "v0.2.0"))
    before = (publisher / HISTORY).read_bytes()

    with pytest.raises(release_history.HistoryError, match=r"v0\.1\.0.*missing or moved on origin"):
        release_history.audit(publisher, "0.3.0")

    assert (publisher / HISTORY).read_bytes() == before


def test_audit_refuses_unavailable_origin_without_falling_back_to_local_tags(
    publisher: Path,
) -> None:
    git(publisher, "remote", "set-url", "origin", str(publisher / "absent.git"))

    with pytest.raises(release_history.HistoryError, match="cannot audit origin"):
        release_history.audit(publisher, "0.3.0")


def test_audit_refuses_a_published_release_target(publisher: Path) -> None:
    with pytest.raises(release_history.HistoryError, match="already published"):
        release_history.audit(publisher, "0.2.0")


def test_new_published_history_must_be_present_locally(publisher: Path) -> None:
    git(publisher, "update-ref", "-d", "refs/tags/v0.2.0")

    with pytest.raises(release_history.HistoryError, match=r"v0\.2\.0.*git fetch"):
        release_history.audit(publisher, "0.3.0")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": True, "tags": {}},
        {"schema_version": 2, "tags": {}},
        {"schema_version": 1, "tags": []},
        {"schema_version": 1, "tags": {"backup": "a" * 40}},
        {"schema_version": 1, "tags": {"v0.1.0": "abc"}},
        {"schema_version": 1, "tags": {}, "extra": True},
    ],
)
def test_invalid_canonical_input_is_refused(tmp_path: Path, payload: object) -> None:
    path = manifest(tmp_path, {})
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(release_history.HistoryError):
        release_history.read(tmp_path)


def test_missing_canonical_input_is_refused(tmp_path: Path) -> None:
    with pytest.raises(release_history.HistoryError, match="restore the committed canonical"):
        release_history.read(tmp_path)


@pytest.mark.parametrize(
    "advertisement",
    [
        "bad advertisement extra\n",
        f"{'a' * 40}\trefs/heads/main\n",
        "bad\trefs/tags/v0.1.0\n",
        f"{'a' * 40}\trefs/tags/v0.1.0^{{}}\n",
        f"{'a' * 40}\trefs/tags/v0.1.0\n{'b' * 40}\trefs/tags/v0.1.0\n",
    ],
)
def test_invalid_origin_advertisement_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, advertisement: str
) -> None:
    monkeypatch.setattr(
        checkout,
        "git",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, advertisement, ""),
    )

    with pytest.raises(release_history.HistoryError):
        release_history.advertised(tmp_path)
