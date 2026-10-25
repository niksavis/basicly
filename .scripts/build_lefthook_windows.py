from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import runpy
import subprocess  # nosec B404
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METADATA = ROOT / "src/basicly/lefthook_assets"
MANIFEST = json.loads((METADATA / "manifest.json").read_text(encoding="utf-8"))


def build(source_dir: Path, output: Path, *, check: bool) -> None:
    artifacts = runpy.run_path(str(ROOT / "src/basicly/lefthook_artifacts.py"))
    archive = artifacts["verified_bytes"](source_dir, "source.tar.gz")
    patch = MANIFEST["windows_patch"]
    patch_path = METADATA / patch["file"]
    if hashlib.sha256(patch_path.read_bytes()).hexdigest() != patch["sha256"]:
        raise ValueError("Windows source patch checksum mismatch")
    environment = {
        **os.environ,
        "GOTOOLCHAIN": patch["go"],
        "GOENV": "off",
        "GOWORK": "off",
        "GOFLAGS": "",
        "CGO_ENABLED": "0",
        "GOOS": "windows",
        "GOTELEMETRY": "off",
    }
    built = {}
    with tempfile.TemporaryDirectory(prefix="basicly-lefthook-build-") as temporary:
        root = Path(temporary)
        source_archive = root / "source.tar.gz"
        source_archive.write_bytes(archive)
        with tarfile.open(source_archive) as tree:
            tree.extractall(root, filter="data")
        source = root / f"lefthook-{MANIFEST['version']}"
        for arguments in (("--check",), ()):
            subprocess.run(  # nosec B603 B607
                ["git", "apply", *arguments, str(patch_path)], cwd=source, check=True
            )
        version = subprocess.run(  # nosec B603 B607
            ["go", "env", "GOVERSION"],
            cwd=source,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip()
        if version != patch["go"]:
            raise ValueError(f"Windows builds require {patch['go']}; found {version}")
        flags = (
            "-buildid= -s -w -X "
            "github.com/evilmartians/lefthook/v2/internal/version.commit=" + patch["revision"]
        )
        for entry in MANIFEST["assets"]:
            if "bundled" not in entry:
                continue
            architecture = "arm64" if "_arm64.gz" in entry["name"] else "amd64"
            binary = root / f"lefthook-{architecture}.exe"
            subprocess.run(  # nosec B603 B607
                [
                    "go",
                    "build",
                    "-mod=readonly",
                    "-trimpath",
                    "-buildvcs=false",
                    "-ldflags",
                    flags,
                    "-o",
                    str(binary),
                    ".",
                ],
                cwd=source,
                env={**environment, "GOARCH": architecture},
                check=True,
            )
            data = gzip.compress(binary.read_bytes(), compresslevel=9, mtime=0)
            digest = hashlib.sha256(data).hexdigest()
            if check:
                pinned = METADATA / entry["bundled"]
                if digest != entry["sha256"] or data != pinned.read_bytes():
                    raise ValueError(
                        f"Rebuilt Windows archive differs from the pin: {entry['name']}"
                    )
            built[entry["name"]] = data
            print(f"{digest}  {entry['name']}", flush=True)
        output.mkdir(parents=True, exist_ok=True)
        for name, data in built.items():
            target = output / name
            if target.exists() and target.read_bytes() != data:
                raise ValueError(
                    "Output differs from an existing archive; choose an empty directory"
                )
        for name, data in built.items():
            (output / name).write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the pinned Windows-only Lefthook patch")
    parser.add_argument(
        "--source-dir", type=Path, default=ROOT / "build/lefthook" / MANIFEST["version"]
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        build(args.source_dir, args.out, check=args.check)
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        parser.exit(1, f"Windows Lefthook rebuild refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
