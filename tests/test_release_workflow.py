"""Regression tests for the release workflow's asset upload glob (basicly-2clt).

`uv build` writes a 1-byte scratch `.gitignore` (contents `*`) into its output
dir. A bare `files: dist/*` upload glob sweeps that file onto the GitHub release
page as a stray asset, so the glob must name the real artifacts explicitly.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_RELEASE_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def _upload_files_glob() -> str:
    """Return the `files:` value of the action-gh-release step in release.yml."""
    workflow = yaml.safe_load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("softprops/action-gh-release"):
                return step["with"]["files"]
    raise AssertionError("no softprops/action-gh-release step found in release.yml")


def test_upload_glob_is_not_a_bare_wildcard() -> None:
    """A bare `dist/*` would also upload uv's scratch `.gitignore`."""
    globs = _upload_files_glob().split()
    assert "dist/*" not in globs


def test_upload_glob_names_the_real_artifacts() -> None:
    """The wheel and sdist must both be uploaded."""
    globs = _upload_files_glob().split()
    assert "dist/*.whl" in globs
    assert "dist/*.tar.gz" in globs
