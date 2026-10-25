from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from basicly import lefthook_artifacts as artifacts


@pytest.fixture
def supplied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "supplied"
    source.mkdir()
    entries = []
    for name, data in (("binary.gz", b"binary"), ("source.tar.gz", b"source")):
        (source / name).write_bytes(data)
        entries.append({
            "name": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "url": f"https://example.invalid/{name}",
        })
    monkeypatch.setattr(artifacts, "MANIFEST", {"version": artifacts.VERSION, "assets": entries})
    return source


def test_preparation_accepts_verified_local_artifacts_without_network(
    tmp_path: Path, supplied: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_network(*_args, **_kwargs):
        pytest.fail("Local artifact preparation attempted a download")

    monkeypatch.setattr(artifacts.urllib.request, "urlopen", refuse_network)
    destination = artifacts.prepare(tmp_path / "checkout", supplied)
    artifacts.validate_artifacts(destination)
    assert (destination / "binary.gz").read_bytes() == b"binary"
    assert (destination / "source.tar.gz").read_bytes() == b"source"


def test_corrupt_artifacts_do_not_replace_previously_prepared_assets(
    tmp_path: Path, supplied: Path
) -> None:
    destination = artifacts.prepare(tmp_path / "checkout", supplied)
    (supplied / "source.tar.gz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match=r"checksum mismatch: source\.tar\.gz"):
        artifacts.prepare(tmp_path / "checkout", supplied)
    artifacts.validate_artifacts(destination)


def test_missing_artifacts_refuse_with_preparation_remedy(tmp_path: Path, supplied: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"run python .*--from-dir"):
        artifacts.validate_artifacts(tmp_path)
    with pytest.raises(ValueError, match="Unknown pinned"):
        artifacts.verified_bytes(supplied, "../foreign")


def test_explicit_download_uses_pinned_manifest_urls_and_verifies_bytes(
    tmp_path: Path, supplied: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []

    def download(url: str, *, timeout: int):
        seen.append(url)
        assert timeout == 60
        return io.BytesIO((supplied / url.rsplit("/", 1)[1]).read_bytes())

    monkeypatch.setattr(artifacts.urllib.request, "urlopen", download)
    artifacts.validate_artifacts(artifacts.prepare(tmp_path / "checkout", None))
    assert seen == [entry["url"] for entry in artifacts.MANIFEST["assets"]]


@pytest.mark.usefixtures("supplied")
def test_download_refuses_non_https_artifact_urls(tmp_path: Path) -> None:
    artifacts.MANIFEST["assets"][0]["url"] = "file:///private/artifact"
    with pytest.raises(ValueError, match="must use HTTPS"):
        artifacts.prepare(tmp_path / "checkout", None)


def test_bundled_windows_artifact_is_verified_and_never_downloaded(
    tmp_path: Path, supplied: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    (metadata / "patched.gz").write_bytes(b"binary")
    entry = artifacts.MANIFEST["assets"][0]
    entry["bundled"] = "patched.gz"
    entry.pop("url")
    monkeypatch.setattr(artifacts, "METADATA", metadata)
    seen = []

    def download(url: str, *, timeout: int):
        seen.append(url)
        assert timeout == 60
        return io.BytesIO((supplied / url.rsplit("/", 1)[1]).read_bytes())

    monkeypatch.setattr(artifacts.urllib.request, "urlopen", download)
    destination = artifacts.prepare(tmp_path / "checkout", None)
    assert (destination / "binary.gz").read_bytes() == b"binary"
    assert seen == [artifacts.MANIFEST["assets"][1]["url"]]
    (metadata / "patched.gz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match=r"checksum mismatch: binary\.gz"):
        artifacts.prepare(tmp_path / "checkout", None)
    assert (destination / "binary.gz").read_bytes() == b"binary"


def test_packaged_windows_fallback_refuses_corruption(
    tmp_path: Path, supplied: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts.MANIFEST["assets"][0]["bundled"] = "patched.gz"
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    (metadata / "patched.gz").write_bytes(b"binary")
    monkeypatch.setattr(artifacts, "METADATA", metadata)
    assert artifacts.verified_bytes(tmp_path, "binary.gz") == b"binary"
    (metadata / "patched.gz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match=r"checksum mismatch: binary\.gz"):
        artifacts.verified_bytes(tmp_path, "binary.gz")
    destination = artifacts.prepare(tmp_path / "checkout", supplied)
    assert (destination / "binary.gz").read_bytes() == b"binary"
    (supplied / "binary.gz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match=r"checksum mismatch: binary\.gz"):
        artifacts.prepare(tmp_path / "checkout", supplied)


def test_download_reuses_verified_packaged_windows_archive_without_git_copy(
    tmp_path: Path, supplied: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    artifacts.MANIFEST["assets"][0]["bundled"] = "patched.gz"
    artifacts.MANIFEST["assets"][0].pop("url")
    monkeypatch.setattr(artifacts, "METADATA", metadata)
    monkeypatch.setattr(artifacts, "artifact_directory", lambda: supplied)
    seen = []

    def download(url: str, *, timeout: int):
        seen.append(url)
        assert timeout == 60
        return io.BytesIO((supplied / url.rsplit("/", 1)[1]).read_bytes())

    monkeypatch.setattr(artifacts.urllib.request, "urlopen", download)
    destination = artifacts.prepare(tmp_path / "checkout", None)
    assert (destination / "binary.gz").read_bytes() == b"binary"
    assert seen == [artifacts.MANIFEST["assets"][1]["url"]]
