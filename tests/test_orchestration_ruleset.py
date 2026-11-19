from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RULESET = REPO / "packages" / "basicly-orch" / "orch_lessons" / "ruleset.json"
DATA = json.loads(RULESET.read_text(encoding="utf-8"))
RULES = DATA["rules"]


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_each_rule_has_one_id_a_source_and_a_known_place() -> None:
    ids = [rule["id"] for rule in RULES]
    assert len(ids) == len(set(ids))
    for rule in RULES:
        assert rule["place"] in DATA["places"], rule["id"]
        assert rule.get("source"), rule["id"]
        if rule["place"] == "local":
            assert rule.get("reason"), rule["id"]
        else:
            assert rule.get("file") and rule.get("anchor"), rule["id"]


@pytest.mark.parametrize(
    "rule", [rule for rule in RULES if rule["place"] != "local"], ids=lambda rule: rule["id"]
)
def test_each_kit_rule_is_present_in_its_kit_place(rule: dict[str, str]) -> None:
    text = flat((REPO / rule["file"]).read_text(encoding="utf-8"))
    assert flat(rule["anchor"]) in text, f"{rule['id']} lost its place in {rule['file']}"
