from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.request
from pathlib import Path

METADATA = Path(__file__).with_name("lefthook_assets")
MANIFEST = json.loads((METADATA / "manifest.json").read_text(encoding="utf-8"))
VERSION: str = MANIFEST["version"]
PREPARE = "python src/basicly/lefthook_artifacts.py --from-dir <artifacts> (or --download)"


def prepared_directory(root: Path) -> Path:
    return root / "build/lefthook" / VERSION


def verified_bytes(directory: Path, name: str) -> bytes:
    entries = {entry["name"]: entry for entry in MANIFEST["assets"]}
    if name not in entries:
        raise ValueError(f"Unknown pinned Lefthook artifact: {name}")
    path = directory / name
    if not path.is_file() and "bundled" in entries[name]:
        path = METADATA / entries[name]["bundled"]
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
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary)
        for entry in MANIFEST["assets"]:
            name = entry["name"]
            if source is None and "bundled" in entry:
                bundled = METADATA / entry["bundled"]
                data = (
                    bundled.read_bytes()
                    if bundled.is_file()
                    else verified_bytes(artifact_directory(), name)
                )
                (staged / name).write_bytes(data)
            elif source is None:
                url = entry["url"]
                if not url.startswith("https://"):
                    raise ValueError(f"Lefthook artifact URL must use HTTPS: {name}")
                with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 — HTTPS is checked above; URLs and artifact hashes are pinned in the committed manifest
                    (staged / name).write_bytes(response.read())
            else:
                shutil.copyfile(source / name, staged / name)
        validate_artifacts(staged)
        destination.mkdir(exist_ok=True)
        for entry in MANIFEST["assets"]:
            (staged / entry["name"]).replace(destination / entry["name"])
    return destination


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
