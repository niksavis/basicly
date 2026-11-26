from __future__ import annotations

import subprocess
from pathlib import Path

from basicly import owned_store, release, tracker_paths
from tests.release_history_fixture import manifest, published

CURRENT = "0.5.1"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def seeded(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "basicly").mkdir(parents=True)
    (root / "site").mkdir()
    (root / ".scripts").mkdir()
    (root / "src" / "basicly" / "__init__.py").write_text(
        f'"""basicly."""\n\n__version__ = "{CURRENT}"\n', encoding="utf-8"
    )
    (root / "README.md").write_text(
        f"Install with `basicly@v{CURRENT}`.\nPin `@v{CURRENT}` for reproducible installs.\n",
        encoding="utf-8",
    )
    (root / "site" / "index.html").write_text(
        f"<pre>uvx --from git+https://x/basicly@v{CURRENT} basicly install</pre>\n",
        encoding="utf-8",
    )
    (root / ".scripts" / "bootstrap.sh").write_text(
        f"# curl ... | sh -s -- --ref v{CURRENT} --technologies python\n", encoding="utf-8"
    )
    (root / ".scripts" / "bootstrap.ps1").write_text(
        f"# ./bootstrap.ps1 -Ref v{CURRENT}\n", encoding="utf-8"
    )
    (root / "docs" / "how-to").mkdir(parents=True)
    (root / "docs" / "how-to" / "upgrade.md").write_text(
        f"Run `uvx --from git+https://x/basicly@v{CURRENT} basicly install`.\n", encoding="utf-8"
    )
    (root / "packages" / "basicly-tracker").mkdir(parents=True)
    (root / "packages" / "basicly-tracker" / "README.md").write_text(
        f"uv tool install 'git+https://x/basicly@v{CURRENT}#subdirectory=packages/basicly-tracker'\n",
        encoding="utf-8",
    )
    (root / "packages" / "basicly-orch").mkdir(parents=True, exist_ok=True)
    (root / "packages" / "basicly-orch" / "README.md").write_text(
        f"uv tool install 'git+https://x/basicly@v{CURRENT}#subdirectory=packages/basicly-orch'\n",
        encoding="utf-8",
    )
    (root / "packages" / "basicly-board").mkdir(parents=True, exist_ok=True)
    (root / "packages" / "basicly-board" / "README.md").write_text(
        f"uv tool install 'git+https://x/basicly@v{CURRENT}#subdirectory=packages/basicly-board'\n",
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\nSummary.\n", encoding="utf-8"
    )
    (root / release.CHANGELOG_SCRIPT).write_text(
        "import sys\n"
        "tag = sys.argv[sys.argv.index('--tag') + 1]\n"
        "date = sys.argv[sys.argv.index('--date') + 1]\n"
        "from pathlib import Path\n"
        "p = Path('CHANGELOG.md')\n"
        "p.write_text(p.read_text() + f'\\n## {tag} - {date}\\n')\n",
        encoding="utf-8",
    )
    (root / release.RELEASE_NOTES_SCRIPT).write_text("", encoding="utf-8")
    ledger = root / tracker_paths.LEDGER_DIR_NAME
    ledger.mkdir(parents=True)
    events = owned_store.packaged_kit("events")
    events.append(ledger, [events.Draft("fx-1", events.KIND_STATUS, {"status": "open"})])
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "tag", "-a", f"v{CURRENT}", "-m", f"v{CURRENT}")
    manifest(root, {f"v{CURRENT}": _git(root, "rev-parse", f"v{CURRENT}^{{commit}}").strip()})
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: record published history")
    published(root, tmp_path / "publisher.git", [f"v{CURRENT}"])
    return root
