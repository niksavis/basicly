"""Tests for the pinned ``br`` installer used by CI (basicly-jr0l.6).

The script runs on three runner images and installs an executable from the
network, so the parts worth pinning are the ones a green CI run would not
notice: that a tampered download is refused rather than installed, that every
runner architecture actually resolves to an asset, and that an unknown host
fails loudly instead of silently leaving the integration suite skipping.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from basicly import br


def _load_module():
    """Load the install-br script module from its path.

    Registered in ``sys.modules`` before execution, which the importlib recipe
    calls for and ``@dataclass`` actually requires: it resolves the defining
    module by name, and an unregistered one makes the decorator raise.
    """
    script_path = Path(__file__).resolve().parents[1] / ".scripts" / "install_br.py"
    spec = importlib.util.spec_from_file_location("install_br", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Every (system, machine) pair the GitHub-hosted runners in quality-gates.yml
# report, including the spellings platform.machine() uses per OS.
RUNNER_HOSTS = [
    ("Linux", "x86_64"),
    ("Darwin", "arm64"),
    ("Darwin", "aarch64"),
    ("Windows", "AMD64"),
    ("Windows", "x86_64"),
]


@pytest.mark.parametrize(("system", "machine"), RUNNER_HOSTS)
def test_every_ci_runner_resolves_to_a_pinned_asset(
    system: str, machine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No runner image may fall through to the unsupported-host error.

    A miss here does not break CI loudly — it fails the install step, which is
    exactly the outcome that used to be invisible: the suite skipping.
    """
    module = _load_module()
    monkeypatch.setattr(module.platform, "system", lambda: system)
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    asset = module.select_asset()
    assert module.BR_VERSION in asset.name
    assert asset.member in ("br", "br.exe")


def test_the_installer_and_the_engine_agree_on_one_pinned_version() -> None:
    """The version CI installs and the version the engine expects are one constant.

    They used to be two unrelated literals, so a machine could run a br the
    harness had never been tested against with nothing to notice it
    (basicly-o7z5). Pinning the *asset digests* still lives in the installer;
    only the version is shared, and this is the assertion that keeps it so.
    """
    assert _load_module().BR_VERSION == br.PINNED_VERSION


def test_an_unknown_host_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A runner image change must name the unsupported host, not install nothing."""
    module = _load_module()
    monkeypatch.setattr(module.platform, "system", lambda: "Plan9")
    monkeypatch.setattr(module.platform, "machine", lambda: "sparc")
    with pytest.raises(SystemExit, match="no pinned br build for Plan9/sparc"):
        module.select_asset()


def test_a_tampered_download_is_refused() -> None:
    """The digest is the whole point: a mismatched payload must never be installed."""
    module = _load_module()
    payload = b"not the release archive"
    expected = hashlib.sha256(b"the release archive").hexdigest()
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        module.verify(payload, expected, "br-test.tar.gz")


def test_a_matching_download_passes() -> None:
    """And the honest payload is accepted, so the check is not vacuously strict."""
    module = _load_module()
    payload = b"the release archive"
    module.verify(payload, hashlib.sha256(payload).hexdigest(), "br-test.tar.gz")


def test_extract_pulls_the_binary_out_of_a_tarball(tmp_path: Path) -> None:
    """The POSIX archives carry the bare binary at the archive root."""
    module = _load_module()
    archive = tmp_path / "br.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("br")
        info.size = 4
        tf.addfile(info, io.BytesIO(b"ELF\n"))
    asset = module.Asset("br.tar.gz", "unused", "br")
    installed = module.extract(archive, asset, tmp_path)
    assert installed.read_bytes() == b"ELF\n"


def test_extract_pulls_the_binary_out_of_a_zip(tmp_path: Path) -> None:
    """The Windows archive is a zip, which Git Bash on the runner cannot unpack."""
    module = _load_module()
    archive = tmp_path / "br.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("br.exe", b"MZ\n")
    asset = module.Asset("br.zip", "unused", "br.exe")
    installed = module.extract(archive, asset, tmp_path)
    assert installed.read_bytes() == b"MZ\n"


def test_publish_path_appends_to_the_github_path_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATH is published by appending to GITHUB_PATH, not by exporting a variable."""
    module = _load_module()
    path_file = tmp_path / "github_path"
    path_file.write_text("/pre/existing\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_PATH", str(path_file))
    module.publish_path(tmp_path / "br-bin")
    assert path_file.read_text(encoding="utf-8").splitlines() == [
        "/pre/existing",
        str(tmp_path / "br-bin"),
    ]


def test_publish_path_outside_actions_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Running the script by hand must not crash on a missing GITHUB_PATH."""
    module = _load_module()
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    module.publish_path(tmp_path / "br-bin")
    assert "add" in capsys.readouterr().out
