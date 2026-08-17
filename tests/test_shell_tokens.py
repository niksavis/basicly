"""Tests for the shell-command parser the tool-usage hook counts through.

Driven through the hook rather than by calling :mod:`shell_tokens` directly, because
that is where every defect pinned below was observed: a payload in, a counter file
out. The module under test is the parser; the hook is the only thing that runs it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "tool-usage.py"
USAGE_FILE = Path(".basicly/usage/tool-usage.json")


def _run(payload: object, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _stats(cwd: Path) -> dict:
    return json.loads((cwd / USAGE_FILE).read_text(encoding="utf-8"))


def test_wrappers_count_wrapper_and_tool(tmp_path: Path) -> None:
    """`uv run pytest -q` counts uv and pytest; builtins and env vars are skipped."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "cd /tmp && FOO=1 uv run pytest -q; echo done"},
    }
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"uv": 1, "pytest": 1}


def test_heredoc_bodies_are_not_counted(tmp_path: Path) -> None:
    """Here-document content lines never register as tools (basicly-587)."""
    command = "python3 - <<'PYEOF'\nt = p.read_text()\n- bullet line\nassert t\nPYEOF\nrg foo"
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"python3": 1, "rg": 1}


def test_quoted_body_words_are_not_counted(tmp_path: Path) -> None:
    """Operators/newlines inside a quoted argument stay in one segment (basicly-zcvo).

    A multi-line ``git commit -m`` body and a ``<<'PY'`` heredoc must not leak
    their words (musical, portable, --description, import) as command heads.
    """
    command = (
        'git commit -m "Refine the layout\n'
        "musical note icon && portable paths\n"
        '--description stuff"\n'
        "python3 - <<'PY'\n"
        "import os; print('nope')\n"
        "PY\n"
        'gh pr create --title "add x; ship it"'
    )
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"git": 1, "python3": 1, "gh": 1}


def test_backslash_and_dash_heredoc_tags_are_stripped(tmp_path: Path) -> None:
    r"""A backslash-escaped (`<<\TAG`) or tab-stripped (`<<-TAG`) heredoc is stripped.

    The tag forms bypassed the opener regex, so the body's python keywords and the
    terminator line leaked as tool names (basicly-v7eu).
    """
    command = "python3 - <<\\PY\ndef f(): return 1\nimport os\nassert f()\nPY\nrg foo"
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"python3": 1, "rg": 1}


def test_flag_or_builtin_led_segment_names_no_tool(tmp_path: Path) -> None:
    """A pipeline segment whose head is a bare flag records nothing for it (basicly-v7eu)."""
    payload = {"tool_name": "Bash", "tool_input": {"command": "grep x file | -d 3"}}
    assert _run(payload, tmp_path).returncode == 0
    assert {tool: entry["count"] for tool, entry in _stats(tmp_path).items()} == {"grep": 1}


def test_python_c_and_m_inline_snippets_do_not_leak(tmp_path: Path) -> None:
    """`python -c`/`-m` inline code is never parsed into tool names (basicly-v7eu)."""
    command = 'python3 -c "import os; x=1; print(x)"\npython3 -m timeit "x+1"'
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    assert {tool: entry["count"] for tool, entry in _stats(tmp_path).items()} == {"python3": 2}


def test_wrapper_flag_value_is_never_credited_as_the_tool(tmp_path: Path) -> None:
    """`uv run --directory <worktree> pytest` credits pytest, not the worktree.

    The flag was skipped but its separate-argv value was not, so this repo's own
    multi-lane pattern counted one junk entry per worktree - 49 counts across 7
    directory names - and never credited the tool it ran (basicly-m0p1). The
    ``--flag=value`` form and a flag placed *before* the subcommand are the same
    defect.
    """
    command = (
        "uv run --directory /repos/basicly.worktrees/basicly-kjc5-61 pytest -q\n"
        "uv run --directory=/repos/basicly.worktrees/basicly-jr0l-37 pytest -q\n"
        "uv --directory /repos/basicly.worktrees/basicly-sy8c run pytest -q"
    )
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"uv": 3, "pytest": 3}


def test_command_substitution_credits_the_command_not_its_subcommand(tmp_path: Path) -> None:
    """`id=$(uv run br create ...)` credits tracker, not `run` or `create`.

    A substitution glued to the assignment token left the wrapper subcommand as
    the segment head: `run` reached 64 counts that way (basicly-m0p1).
    """
    command = 'id=$(uv run br create --title "add a thing")\nname=`gh repo view --json name`'
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"uv": 1, "br": 1, "gh": 1}


def test_shell_keywords_on_their_own_line_are_not_tools(tmp_path: Path) -> None:
    """`continue`/`break` heading a line inside a loop body name no tool (basicly-m0p1)."""
    command = "for f in a b; do\n  continue\ndone\nwhile true; do\n  break\ndone\nrg foo"
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"rg": 1}


def test_a_function_defined_in_the_command_is_not_a_tool(tmp_path: Path) -> None:
    """Calling a function the same command text defines is not tool usage (basicly-m0p1)."""
    command = "write_src() {\n  printf 'x' > f\n}\nwrite_src\nfunction emit {\n  wc -l f\n}\nemit"
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"printf": 1, "wc": 1}


def test_env_wrapper_resolves_the_command_behind_its_flags(tmp_path: Path) -> None:
    """`env -C <dir> <cmd>` and `env VAR=x <cmd>` credit the wrapped command too."""
    command = "env -C /repos/basicly.worktrees/basicly-2rn9 git status --short\nenv FOO=1 pytest -q"
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert _run(payload, tmp_path).returncode == 0
    counts = {tool: entry["count"] for tool, entry in _stats(tmp_path).items()}
    assert counts == {"env": 2, "git": 1, "pytest": 1}
