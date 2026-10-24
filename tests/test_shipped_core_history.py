from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from basicly import checkout, cli, shipped_core
from tests.release_history_fixture import HISTORY, git, init, manifest, published, tag


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_an_unpublished_author_tag_cannot_change_inventory_or_installer_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    author = init(tmp_path / "author")
    commit = tag(author, "v0.1.0", "published\n", annotated=True)
    publisher = published(author, tmp_path / "publisher.git", ["v0.1.0"])
    tag(author, "v0.2.0", "local-only\n", annotated=True)
    manifest(author, {"v0.1.0": commit})
    original = checkout.git

    def offline(args: list[str], **kwargs):
        assert args[0] not in {"ls-remote", "fetch"}, "ordinary digest generation must stay offline"
        return original(args, **kwargs)

    monkeypatch.setattr(checkout, "git", offline)
    authored = shipped_core.build_digests(author, "0.3.0")
    assert authored["tags"] == ["v0.1.0"]
    assert authored["core"] == {"fragments/rules.yaml": [_sha("published\n")]}
    inventory = author / shipped_core.SOURCE_DIGESTS_FILE
    inventory.write_text(shipped_core.render_digests(authored), encoding="utf-8")
    git(author, "add", "-A")
    git(author, "commit", "-qm", "prepare candidate")
    git(author, "push", "-q", "origin", "HEAD:refs/heads/main")
    fresh = tmp_path / "fresh"
    git(tmp_path, "clone", "-q", "--branch", "main", str(publisher), str(fresh))

    assert git(fresh, "tag", "--list") == "v0.1.0"
    assert (fresh / HISTORY).read_bytes() == (author / HISTORY).read_bytes()
    assert shipped_core.render_digests(
        shipped_core.build_digests(fresh, "0.3.0")
    ) == inventory.read_text(encoding="utf-8")
    monkeypatch.setattr(shipped_core, "DIGESTS_FILE", inventory)
    bundled = tmp_path / "bundled"
    target = bundled / "fragments/rules.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("new upstream\n", encoding="utf-8")
    for label, text, expected in (
        ("private", "local-only\n", "skipped_edits"),
        ("known", "published\n", "updated"),
    ):
        consumer = tmp_path / label
        installed = consumer / "fragments/rules.yaml"
        installed.parent.mkdir(parents=True)
        installed.write_text(text, encoding="utf-8")
        report = cli._sync_catalog(bundled, consumer, None, force=False)
        assert getattr(report, expected) == ["fragments/rules.yaml"]
        assert installed.read_text(encoding="utf-8") == (
            text if label == "private" else "new upstream\n"
        )


@pytest.mark.parametrize("changed", ["missing", "moved"])
def test_missing_or_moved_canonical_refs_refuse_offline_generation(
    tmp_path: Path, changed: str
) -> None:
    author = init(tmp_path / "author")
    commit = tag(author, "v0.1.0", "published\n")
    manifest(author, {"v0.1.0": commit})
    replacement = tag(author, "v0.2.0", "other\n")
    if changed == "missing":
        git(author, "update-ref", "-d", "refs/tags/v0.1.0")
    else:
        git(author, "update-ref", "refs/tags/v0.1.0", replacement)

    with pytest.raises(ValueError, match=r"v0\.1\.0.*git fetch"):
        shipped_core.build_digests(author, "0.3.0")
