"""Install a pinned ``br`` (beads tracker) binary for CI.

``tests/test_integration_loop.py`` drives the loop and the supervisor against a
real git repository and a real tracker, and it is the only test that can catch a
defect a stub would agree with. Without ``br`` on PATH it skips, so before this
script existed it ran on developer machines and nowhere else.

Python rather than three shells on purpose: the runners disagree about archive
tools (Git Bash on ``windows-latest`` has no ``unzip``) and about quoting, and
this repo prefers a cross-platform implementation whenever there is a choice.
Python is already provisioned on every runner by ``setup-uv``.

Pinned twice, by version *and* by content hash. The version alone is not enough:
a release asset can be replaced in place, and CI installing an unverified binary
from the network is a supply-chain hole. The digests below were taken from the
project's per-asset ``.sha256`` files — note that v0.2.16's aggregate
``SHA256SUMS`` disagrees with them and is wrong, so do not refresh from it.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

# The floor the harness is exercised against; worktree provisioning refuses an
# older br because tracker sharing needs `.beads/redirect` support.
BR_VERSION = "0.2.16"
RELEASES = "https://github.com/Dicklesworthstone/beads_rust/releases/download"


@dataclass(frozen=True)
class Asset:
    """One published archive: its release asset name, digest, and binary member."""

    name: str
    sha256: str
    member: str


# Keyed by (platform.system(), 64-bit machine family). Only the architectures
# GitHub-hosted runners actually provide are listed; an unlisted host is an
# explicit error rather than a silent skip, so a runner image change is visible.
ASSETS: dict[tuple[str, str], Asset] = {
    ("Linux", "x86_64"): Asset(
        f"br-{BR_VERSION}-linux_amd64.tar.gz",
        "9ee22d340b56dacfb20460d8e4b2a0065fb66f161910472285d55f51e04512b0",
        "br",
    ),
    ("Darwin", "arm64"): Asset(
        f"br-{BR_VERSION}-darwin_arm64.tar.gz",
        "43c2d38e8e550737e15e35e8eb1d804e726b85842ea02dc8ff02c3c50d4c2b81",
        "br",
    ),
    ("Windows", "x86_64"): Asset(
        f"br-{BR_VERSION}-windows_amd64.zip",
        "4dd5ae3422fd3f77fd6912833f21304a46ca055c93517b1b6dc101fb1fc18312",
        "br.exe",
    ),
}

# platform.machine() spells the same architecture differently per OS.
MACHINE_ALIASES = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}


def select_asset() -> Asset:
    """The archive for this host, or a clear error naming what was unsupported."""
    system = platform.system()
    machine = platform.machine().lower()
    machine = MACHINE_ALIASES.get(machine, machine)
    asset = ASSETS.get((system, machine))
    if asset is None:
        supported = ", ".join(f"{s}/{m}" for s, m in sorted(ASSETS))
        raise SystemExit(f"no pinned br build for {system}/{machine}; pinned: {supported}")
    return asset


def download(url: str) -> bytes:
    """Fetch *url* into memory (the archive is ~7 MB)."""
    with urllib.request.urlopen(url, timeout=120) as response:  # nosec B310 — pinned https URL
        return response.read()


def verify(payload: bytes, expected: str, name: str) -> None:
    """Refuse a payload whose digest is not the pinned one."""
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise SystemExit(f"{name}: sha256 mismatch\n  expected {expected}\n  actual   {actual}")


def extract(archive: Path, asset: Asset, bin_dir: Path) -> Path:
    """Pull the single binary out of *archive* into *bin_dir*, executable."""
    target = bin_dir / asset.member
    if asset.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf, zf.open(asset.member) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as tf:
            src = tf.extractfile(asset.member)
            if src is None:
                raise SystemExit(f"{asset.name}: no member named {asset.member!r}")
            with src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    target.chmod(0o755)
    return target


def publish_path(bin_dir: Path) -> None:
    """Append *bin_dir* to the job's PATH via GITHUB_PATH, when running in Actions."""
    github_path = os.environ.get("GITHUB_PATH")
    if not github_path:
        print(f"GITHUB_PATH unset; add {bin_dir} to PATH yourself")
        return
    with Path(github_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{bin_dir}\n")


def main(argv: list[str] | None = None) -> int:
    """Download, verify and install the pinned br for this host."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bin-dir",
        type=Path,
        required=True,
        help="Directory to install the br binary into (created if absent)",
    )
    args = parser.parse_args(argv)

    asset = select_asset()
    bin_dir: Path = args.bin_dir
    bin_dir.mkdir(parents=True, exist_ok=True)

    url = f"{RELEASES}/v{BR_VERSION}/{asset.name}"
    print(f"Downloading {url}")
    payload = download(url)
    verify(payload, asset.sha256, asset.name)
    print(f"Verified sha256 {asset.sha256}")

    archive = bin_dir / asset.name
    archive.write_bytes(payload)
    installed = extract(archive, asset, bin_dir)
    archive.unlink()

    publish_path(bin_dir)
    print(f"Installed br {BR_VERSION} at {installed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
