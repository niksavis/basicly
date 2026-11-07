from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SEVERITIES = ("important", "nit", "pre-existing")
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
LOCATION = re.compile(r"[\w./-]+\.[A-Za-z0-9]+:\d+")
COMMAND = re.compile(r"(?:^|\s)\$ |\bran:", re.MULTILINE)
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CAP = 5
RISE = 1.0
CLEAN_ROUNDS = 2


@dataclass
class Report:
    lens: str
    status: str
    data: dict = field(default_factory=dict)
    problem: str = ""


def _names(raw: str, kind: str) -> list[str]:
    names = [name.strip() for name in raw.split(",") if name.strip()]
    if not names:
        raise SystemExit(f"swarm: name at least one {kind}, e.g. a,b,c")
    bad = [name for name in names if not NAME.match(name)]
    if bad:
        raise SystemExit(
            f"swarm: {kind} {bad[0]!r} must be lowercase letters, digits and hyphens, "
            "e.g. done-states"
        )
    if len(set(names)) != len(names):
        raise SystemExit(f"swarm: a {kind} is named twice; each gets one fresh agent")
    return names


def _shape_problem(data: object) -> str:
    if not isinstance(data, dict):
        return "the json block is not an object"
    findings = data.get("findings", [])
    if not isinstance(findings, list) or not all(isinstance(item, dict) for item in findings):
        return "findings is not a list of objects"
    if any(item.get("severity") not in SEVERITIES for item in findings):
        return f"severity must be one of {', '.join(SEVERITIES)}"
    if data.get("verdict", "pass") not in ("pass", "fail"):
        return "verdict must be pass or fail"
    return ""


def read_report(directory: Path, lens: str) -> Report:
    path = directory / f"{lens}.md"
    if not path.is_file():
        return Report(lens, "not run", problem=f"no {path.name}")
    blocks = JSON_BLOCK.findall(path.read_text(encoding="utf-8"))
    if not blocks:
        return Report(lens, "malformed", problem="no ```json block at the end")
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError as exc:
        return Report(lens, "malformed", problem=f"json error at line {exc.lineno}")
    problem = _shape_problem(data)
    if problem:
        return Report(lens, "malformed", problem=problem)
    return Report(lens, "ok", data)


def verified(finding: dict) -> bool:
    evidence = str(finding.get("evidence", ""))
    return bool(LOCATION.search(evidence) or COMMAND.search(evidence))


def merge(reports: list[Report]) -> list[dict]:
    groups: dict[str, dict] = {}
    loose: list[dict] = []
    for report in reports:
        for finding in report.data.get("findings", []):
            entry = {**finding, "lens": report.lens, "verified": verified(finding)}
            match = LOCATION.search(str(finding.get("evidence", "")))
            if not match:
                loose.append({"severity": finding["severity"], "items": [entry]})
                continue
            group = groups.setdefault(
                match.group(0), {"severity": finding["severity"], "items": []}
            )
            group["items"].append(entry)
            if SEVERITIES.index(finding["severity"]) < SEVERITIES.index(group["severity"]):
                group["severity"] = finding["severity"]
    for anchor, group in groups.items():
        group["anchor"] = anchor
    for group in [*groups.values(), *loose]:
        group["lenses"] = sorted({item["lens"] for item in group["items"]})
    return sorted(
        [*groups.values(), *loose],
        key=lambda group: (SEVERITIES.index(group["severity"]), -len(group["lenses"])),
    )


def cmd_check(args: argparse.Namespace) -> int:
    reports = [read_report(args.dir, lens) for lens in _names(args.lenses, "lens")]
    print("| lens | status | verdict | important | nit | pre-existing | unverified |")
    print("|---|---|---|---|---|---|---|")
    for report in reports:
        findings = report.data.get("findings", [])
        counts = [sum(item["severity"] == level for item in findings) for level in SEVERITIES]
        unverified = sum(not verified(item) for item in findings)
        status = report.status if not report.problem else f"{report.status}: {report.problem}"
        verdict = str(report.data.get("verdict", "-"))
        print(
            f"| {report.lens} | {status} | {verdict} | "
            f"{' | '.join(map(str, counts))} | {unverified} |"
        )
    print()
    for group in merge([report for report in reports if report.status == "ok"]):
        where = group.get("anchor", "no location")
        agreed = f", {len(group['lenses'])} lenses agree" if len(group["lenses"]) > 1 else ""
        print(f"- [{group['severity']}] {where}{agreed}")
        for item in group["items"]:
            mark = "" if item["verified"] else " UNVERIFIED"
            print(f"  - {item['lens']} ({item['severity']}{mark}): {item.get('claim', '')}")
            print(f"    evidence: {item.get('evidence', '')}")
            if item.get("fix"):
                print(f"    fix: {item['fix']}")
    missing = [report.lens for report in reports if report.status != "ok"]
    if missing:
        print(
            f"\nincomplete: {', '.join(missing)} not run or malformed; "
            "rerun once, then report them as not run"
        )
        return 1
    return 0


def _vote(report: Report, options: list[str]) -> tuple[str | None, str]:
    if report.status != "ok":
        return None, report.problem
    vote = report.data.get("vote")
    if not isinstance(vote, str) or not vote.strip():
        return None, "no vote field"
    if vote.strip() not in options:
        return None, f"vote {vote.strip()!r} is not one of {', '.join(options)}"
    return vote.strip(), ""


def cmd_tally(args: argparse.Namespace) -> int:
    voters = _names(args.voters, "voter")
    options = [option.strip() for option in args.options.split(",") if option.strip()]
    if len(voters) % 2 == 0:
        raise SystemExit(
            "council: use an odd number of voters, 3 or 5, so a tie cannot hide a majority"
        )
    if not 2 <= len(options) <= 4:
        raise SystemExit("council: give 2 to 4 options, e.g. --options per-event,shards")
    reports = [read_report(args.dir, voter) for voter in voters]
    votes = {report.lens: _vote(report, options) for report in reports}
    for report in reports:
        vote, problem = votes[report.lens]
        if vote is None:
            print(f"- {report.lens}: not run ({problem}); counts against every option")
        else:
            print(f"- {report.lens}: {vote}: {report.data.get('reason', '')}")
    counts = {
        option: [name for name, (vote, _) in votes.items() if vote == option] for option in options
    }
    print()
    for option, names in sorted(counts.items(), key=lambda item: -len(item[1])):
        print(f"{option}: {len(names)} of {len(voters)} ({', '.join(names) or 'none'})")
    winner = next(
        (option for option, names in counts.items() if len(names) * 2 > len(voters)), None
    )
    if winner is None:
        print("\ndecision: none; no option has a majority of all voters, so the user decides")
        return 2
    print(f"\ndecision: {winner}")
    for report in reports:
        vote, _ = votes[report.lens]
        if vote is None:
            print(f"dissent ({report.lens}): did not vote")
        elif vote != winner:
            print(f"dissent ({report.lens}): {report.data.get('strongest_counter', '')}")
    return 0


def _load_state(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(
            f"hill-climb: no state at {path}; "
            f"start with: climb-init --state {path} --properties a,b"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_climb_init(args: argparse.Namespace) -> int:
    if args.state.exists():
        raise SystemExit(f"hill-climb: {args.state} exists; one state file per climb")
    properties = _names(args.properties, "property")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    state = {"properties": properties, "cap": args.cap, "rounds": []}
    args.state.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(f"climb started: {len(properties)} properties, cap {args.cap} rounds")
    return 0


def decide(state: dict) -> str:
    rounds = state["rounds"]
    if len(rounds) >= CLEAN_ROUNDS and all(r["important"] == 0 for r in rounds[-CLEAN_ROUNDS:]):
        return f"stop: {CLEAN_ROUNDS} rounds in a row with no important finding"
    if len(rounds) >= 2:
        before, now = rounds[-2]["scores"], rounds[-1]["scores"]
        if not any(now[p] - before[p] >= RISE for p in state["properties"]):
            return f"stop: no property rose {RISE} since the last round"
    if len(rounds) >= state["cap"]:
        return f"stop: the cap of {state['cap']} rounds is reached"
    return "continue: revise the artifact, then grade it with at least one new lens"


def _grade(report: Report, prop: str) -> float | None:
    value = (
        report.data.get("grades", {}).get(prop)
        if isinstance(report.data.get("grades"), dict)
        else None
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10:
        return None
    return float(value)


def cmd_climb_round(args: argparse.Namespace) -> int:
    state = _load_state(args.state)
    if state["rounds"] and decide(state).startswith("stop") and not args.past_stop:
        raise SystemExit(
            f"hill-climb: the climb has stopped ({decide(state)}); "
            "add --past-stop only when the user asked for another round"
        )
    reports = [read_report(args.dir, lens) for lens in _names(args.lenses, "lens")]
    grades: dict[str, list[float]] = {p: [] for p in state["properties"]}
    for report in reports:
        missing = [p for p in state["properties"] if _grade(report, p) is None]
        if report.status != "ok":
            print(f"- {report.lens}: not graded ({report.problem})")
            continue
        if missing:
            print(
                f"- {report.lens}: no 0-10 grade for {', '.join(missing)}; its other grades count"
            )
        for p in state["properties"]:
            value = _grade(report, p)
            if value is not None:
                grades[p].append(value)
    ungraded = [p for p, values in grades.items() if not values]
    if ungraded:
        print(f"round refused: no reviewer graded {', '.join(ungraded)}")
        return 1
    graded = [report for report in reports if report.status == "ok"]
    important = sum(group["severity"] == "important" for group in merge(graded))
    state["rounds"].append({
        "lenses": [report.lens for report in graded],
        "scores": {p: min(values) for p, values in grades.items()},
        "highs": {p: max(values) for p, values in grades.items()},
        "important": important,
        "past_stop": bool(args.past_stop),
    })
    args.state.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    rounds = state["rounds"]
    print("| property | " + " | ".join(f"r{n}" for n in range(1, len(rounds) + 1)) + " |")
    print("|---|" + "---|" * len(rounds))
    for p in state["properties"]:
        cells = [
            f"{r['scores'][p]:g}"
            + (f"-{r['highs'][p]:g}" if r["highs"][p] != r["scores"][p] else "")
            for r in rounds
        ]
        print(f"| {p} | {' | '.join(cells)} |")
    print(f"| important findings | {' | '.join(str(r['important']) for r in rounds)} |")
    print(f"\ndecision: {decide(state)}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="swarm.py")
    sub = root.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="merge lens reports; a missing report is not run")
    check.add_argument("--lenses", required=True)
    check.add_argument("--dir", type=Path, required=True)
    check.set_defaults(run=cmd_check)
    tally = sub.add_parser("tally", help="count council votes; a majority of all voters decides")
    tally.add_argument("--voters", required=True)
    tally.add_argument("--options", required=True)
    tally.add_argument("--dir", type=Path, required=True)
    tally.set_defaults(run=cmd_tally)
    init = sub.add_parser("climb-init", help="start a hill climb with its fixed properties")
    init.add_argument("--state", type=Path, required=True)
    init.add_argument("--properties", required=True)
    init.add_argument("--cap", type=int, default=CAP, choices=range(1, 11))
    init.set_defaults(run=cmd_climb_init)
    step = sub.add_parser("climb-round", help="record one graded round and print the stop decision")
    step.add_argument("--state", type=Path, required=True)
    step.add_argument("--lenses", required=True)
    step.add_argument("--dir", type=Path, required=True)
    step.add_argument("--past-stop", action="store_true")
    step.set_defaults(run=cmd_climb_round)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
