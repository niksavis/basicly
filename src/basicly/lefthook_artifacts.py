from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path

METADATA = Path(__file__).with_name("lefthook_assets")
MANIFEST = json.loads((METADATA / "manifest.json").read_text(encoding="utf-8"))
VERSION: str = MANIFEST["version"]
PREPARE = "python src/basicly/lefthook_artifacts.py --from-dir <artifacts> (or --download)"
READ_TIMEOUT_S = 60
RETRY_WAITS_S = (5, 15)
DEADLINE_S = 240


def prepared_directory(root: Path) -> Path:
    return root / "build/lefthook" / VERSION


def verified_bytes(directory: Path, name: str) -> bytes:
    entries = {entry["name"]: entry for entry in MANIFEST["assets"]}
    if name not in entries:
        raise ValueError(f"Unknown pinned Lefthook artifact: {name}")
    path = directory / name
    if not path.is_file():
        raise FileNotFoundError(f"Missing Lefthook artifact {name}; run {PREPARE}")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entries[name]["sha256"]:
        raise ValueError(f"Lefthook checksum mismatch: {name}")
    return data


def validate_artifacts(directory: Path) -> None:
    for entry in MANIFEST["assets"]:
        verified_bytes(directory, entry["name"])


def artifact_directory() -> Path:
    packaged = METADATA / VERSION
    if packaged.is_dir():
        return packaged
    return prepared_directory(Path(__file__).resolve().parents[2])


def prepare(root: Path, source: Path | None) -> Path:
    destination = prepared_directory(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + DEADLINE_S
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary)
        for entry in MANIFEST["assets"]:
            name = entry["name"]
            if source is None:
                url = entry["url"]
                if not url.startswith("https://"):
                    raise ValueError(f"Lefthook artifact URL must use HTTPS: {name}")
                (staged / name).write_bytes(_download(url, deadline))
            else:
                shutil.copyfile(source / name, staged / name)
        validate_artifacts(staged)
        if destination.exists():
            shutil.rmtree(destination)
        staged.replace(destination)
    return destination


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=READ_TIMEOUT_S) as response:  # noqa: S310 — HTTPS is checked by the caller; URLs and artifact hashes are pinned in the committed manifest
        return response.read()


def _download(url: str, deadline: float) -> bytes:
    attempts = len(RETRY_WAITS_S) + 1
    failure: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _fetch(url)
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise
            failure = exc
        except (OSError, http.client.HTTPException) as exc:
            failure = exc
        wait = RETRY_WAITS_S[attempt - 1] if attempt < attempts else 0
        if attempt == attempts or time.monotonic() + wait + READ_TIMEOUT_S > deadline:
            host = urllib.parse.urlsplit(url).hostname
            raise OSError(
                f"download from {host} failed after {attempt} of {attempts} attempts"
                f" with a {READ_TIMEOUT_S} s read timeout each"
                f" and a {DEADLINE_S} s limit for all downloads: {failure}"
            ) from failure
        print(
            f"Lefthook artifact download attempt {attempt} of {attempts}"
            f" failed: {failure}; retrying"
        )
        time.sleep(wait)
    raise AssertionError(attempts)


def _verified(directory: Path) -> bool:
    try:
        validate_artifacts(directory)
    except OSError, ValueError:
        return False
    return True


def provision_lane(lane: Path, donors: Sequence[Path]) -> list[str]:
    manifest = lane / "src/basicly/lefthook_assets/manifest.json"
    if not manifest.is_file() or (manifest.parent / VERSION).is_dir():
        return []
    if _verified(prepared_directory(lane)):
        return []
    if manifest.read_bytes() != (METADATA / "manifest.json").read_bytes():
        return [f"lefthook: the lane pins another Lefthook; run {PREPARE} in the lane"]
    for donor in donors:
        if _verified(prepared_directory(donor)):
            try:
                prepare(lane, prepared_directory(donor))
            except OSError, ValueError:
                continue
            return [f"lefthook: {VERSION} artifacts copied from {donor.name}"]
    return [f"lefthook: no checkout holds verified artifacts; run {PREPARE} in the lane"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare verified Lefthook distribution artifacts")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-dir", type=Path)
    mode.add_argument("--download", action="store_true")
    args = parser.parse_args()
    try:
        directory = prepare(Path(__file__).resolve().parents[2], args.from_dir)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Lefthook artifact preparation failed: {exc}\n")
    print(f"Verified Lefthook {VERSION} artifacts prepared in {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
