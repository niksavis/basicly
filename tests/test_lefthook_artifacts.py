from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import subprocess  # nosec B404
from email.message import Message
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


def test_preparation_removes_unpinned_files_from_the_cache(tmp_path: Path, supplied: Path) -> None:
    destination = artifacts.prepare(tmp_path / "checkout", supplied)
    stale = destination / "source.tar.gz.old"
    stale.write_bytes(b"stale")

    artifacts.prepare(tmp_path / "checkout", supplied)

    assert not stale.exists()
    assert {path.name for path in destination.iterdir()} == {
        entry["name"] for entry in artifacts.MANIFEST["assets"]
    }


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


def test_a_timed_out_download_is_retried_and_preparation_continues(
    tmp_path: Path, supplied: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    waits: list[float] = []

    def flaky(url: str, *, timeout: int):
        assert timeout == artifacts.READ_TIMEOUT_S
        calls.append(url)
        if calls.count(url) == 1:
            raise TimeoutError("The read operation timed out")
        return io.BytesIO((supplied / url.rsplit("/", 1)[1]).read_bytes())

    monkeypatch.setattr(artifacts.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(artifacts.time, "sleep", waits.append)
    artifacts.validate_artifacts(artifacts.prepare(tmp_path / "checkout", None))
    urls = [entry["url"] for entry in artifacts.MANIFEST["assets"]]
    assert calls == [url for url in urls for _ in range(2)]
    assert waits == [artifacts.RETRY_WAITS_S[0]] * len(urls)


@pytest.mark.usefixtures("supplied")
def test_a_download_that_never_answers_names_host_attempts_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def stalled(url: str, *, timeout: int):
        assert timeout == artifacts.READ_TIMEOUT_S
        calls.append(url)
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(artifacts.urllib.request, "urlopen", stalled)
    monkeypatch.setattr(artifacts.time, "sleep", lambda _seconds: None)
    attempts = len(artifacts.RETRY_WAITS_S) + 1
    with pytest.raises(
        OSError,
        match=rf"example\.invalid failed after {attempts} of {attempts} attempts"
        r" with a 60 s read timeout",
    ):
        artifacts.prepare(tmp_path / "checkout", None)
    assert len(calls) == attempts


@pytest.mark.usefixtures("supplied")
def test_no_retry_starts_past_the_limit_for_all_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    calls: list[str] = []

    def stalled(url: str, *, timeout: int):
        assert timeout == artifacts.READ_TIMEOUT_S
        calls.append(url)
        clock[0] += timeout
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(artifacts.urllib.request, "urlopen", stalled)
    monkeypatch.setattr(artifacts.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        artifacts.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(artifacts, "DEADLINE_S", 100)
    with pytest.raises(OSError, match=r"failed after 1 of 3 attempts .* and a 100 s limit"):
        artifacts.prepare(tmp_path / "checkout", None)
    assert len(calls) == 1


@pytest.mark.usefixtures("supplied")
def test_a_refused_download_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def missing(url: str, *, timeout: int):
        assert timeout == artifacts.READ_TIMEOUT_S
        calls.append(url)
        raise artifacts.urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

    monkeypatch.setattr(artifacts.urllib.request, "urlopen", missing)
    with pytest.raises(artifacts.urllib.error.HTTPError):
        artifacts.prepare(tmp_path / "checkout", None)
    assert len(calls) == 1


@pytest.mark.usefixtures("supplied")
def test_download_refuses_non_https_artifact_urls(tmp_path: Path) -> None:
    artifacts.MANIFEST["assets"][0]["url"] = "file:///private/artifact"
    with pytest.raises(ValueError, match="must use HTTPS"):
        artifacts.prepare(tmp_path / "checkout", None)


RELEASE_PREFIX = "https://github.com/evilmartians/lefthook/releases/download/v"
PLATFORM_ARCHIVES = {
    f"lefthook_{artifacts.VERSION}_{system}_{machine}.gz"
    for system in ("Linux", "MacOS", "Windows")
    for machine in ("x86_64", "arm64")
}
EXECUTABLE_MAGIC = {
    b"\x1f\x8b": "gzip archive",
    b"PK\x03\x04": "zip archive",
    b"BZh": "bzip2 archive",
    b"\xfd7zXZ": "xz archive",
    b"\x28\xb5\x2f\xfd": "zstd archive",
    b"!<arch>": "ar archive",
    b"MZ": "Windows executable",
    b"\x7fELF": "ELF executable",
    b"\xcf\xfa\xed\xfe": "Mach-O executable",
}


def _committed_binaries(paths: list[Path]) -> dict[str, str]:
    found = {}
    for path in paths:
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            head = handle.read(8)
        for magic, kind in EXECUTABLE_MAGIC.items():
            if head.startswith(magic):
                found[path.as_posix()] = kind
    return found


def _tracked_paths(root: Path) -> list[Path]:
    listing = subprocess.run(  # nosec B603 B607
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    )
    return [root / name.decode() for name in listing.stdout.split(b"\0") if name]


def test_the_committed_manifest_pins_only_official_upstream_release_archives() -> None:
    committed = json.loads((artifacts.METADATA / "manifest.json").read_text(encoding="utf-8"))
    assert set(committed) == {"version", "assets"}
    names = {entry["name"] for entry in committed["assets"]}
    assert names >= PLATFORM_ARCHIVES
    for entry in committed["assets"]:
        assert set(entry) == {"name", "sha256", "url"}, entry["name"]
        assert entry["url"] == f"{RELEASE_PREFIX}{committed['version']}/{entry['name']}"
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry["name"]


def test_compatibility_ci_uses_official_artifacts_without_a_private_builder() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/compatibility.yml").read_text(encoding="utf-8")
    assert "tests/test_hook_compatibility.py" in workflow
    assert "Build and exercise the wheel as a consumer" in workflow
    assert "build_lefthook_windows.py" not in workflow
    assert "actions/setup-go" not in workflow


@pytest.mark.live_ledger
def test_no_executable_or_archive_is_committed_in_the_tracked_tree(tmp_path: Path) -> None:
    planted = tmp_path / "lefthook_Windows_x86_64.gz"
    planted.write_bytes(gzip.compress(b"MZ" + b"\x00" * 64))
    assert _committed_binaries([planted]) == {planted.as_posix(): "gzip archive"}

    root = Path(__file__).resolve().parents[1]
    tracked = _tracked_paths(root)
    assert len(tracked) > 100, "git ls-files returned too little to be the repo"
    assert _committed_binaries(tracked) == {}
    assert not list((artifacts.METADATA).glob("**/*.gz"))
    assert not list((artifacts.METADATA).glob("**/*.patch"))
