from __future__ import annotations

from pathlib import Path

from basicly.agents import (
    AgentDefinition,
    compose_body,
    default_agent_roots,
    discover_agents,
    discover_blocks,
)
from basicly.copilot_tools import WRITE_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = ("Terminal", "Web", "Desktop", "Mobile")
OUTPUT_SECTIONS = (
    "**Mocks**",
    "**Commands and keyboard access**",
    "**Migration**",
    "**Open questions**",
    "**Failure modes**",
)


def _designer() -> AgentDefinition:
    roots = default_agent_roots(REPO_ROOT)
    (agent,) = [agent for agent in discover_agents(roots) if agent.slug == "designer"]
    return agent


def _body() -> str:
    return compose_body(_designer(), discover_blocks(default_agent_roots(REPO_ROOT)))


def test_the_designer_agent_is_read_only_at_tier_high() -> None:

    agent = _designer()

    assert agent.tier == "high"
    assert agent.tools == ("Read", "Grep", "Glob", "Bash")
    assert set(agent.tools).isdisjoint(WRITE_TOOLS)
    assert agent.posture == "Read-only."


def test_the_designer_body_gives_one_section_per_environment() -> None:

    headings = [line for line in _body().splitlines() if line.startswith("## ")]

    assert [heading for heading in headings if heading[3:] in ENVIRONMENTS] == [
        f"## {name}" for name in ENVIRONMENTS
    ]


def test_the_designer_output_contract_names_every_required_section() -> None:

    contract = "\n".join(item.value for item in _designer().slot("output_contract"))

    positions = [contract.find(section) for section in OUTPUT_SECTIONS]

    assert -1 not in positions
    assert positions == sorted(positions)
    assert "answer you recommend" in contract


def test_the_designer_counts_terminal_mock_widths_with_a_script() -> None:

    body = _body()
    terminal = body[body.index("## Terminal") : body.index("## Web")]

    assert "display width with a script" in terminal
    assert "east_asian_width" in terminal


def test_the_designer_terminal_section_carries_the_handily_lessons() -> None:

    body = _body()
    terminal = " ".join(body[body.index("## Terminal") : body.index("## Web")].split())

    for rule in ("**Keyboard.**", "**Focus.**", "**Scrolling.**", "**Mouse.**"):
        assert rule in terminal
    for rule in ("**Colour.**", "**Discoverable shortcuts.**"):
        assert rule in terminal
    assert "recommended buttons take 6 to 9, top to bottom (handily-e83jxw)" in terminal
    assert "Never scroll the view on a press" in terminal
    assert "handily-7vcozj" in terminal
    assert "keyboard path" in terminal
    assert "never offer a press or release rule as the fix (handily-kkzm06)" in terminal
    assert "NO_COLOR" in terminal
    assert "terminal checklist" in body
