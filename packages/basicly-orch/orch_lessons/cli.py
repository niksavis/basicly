from __future__ import annotations

import argparse
import sys
from pathlib import Path

from orch_lessons import log


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="orch")
    groups = top.add_subparsers(dest="group", required=True)
    lesson = groups.add_parser("lesson").add_subparsers(dest="action", required=True)
    add = lesson.add_parser("add")
    add.add_argument("--what", required=True)
    add.add_argument("--requirement", required=True)
    add.add_argument("--enforcement", required=True)
    add.add_argument("--record", required=True)
    close = lesson.add_parser("close")
    close.add_argument("id")
    close.add_argument("--change", required=True)
    many = groups.add_parser("lessons").add_subparsers(dest="action", required=True)
    init = many.add_parser("init")
    init.add_argument("folder", type=Path)
    export = many.add_parser("export")
    export.add_argument("--target", type=Path, required=True)
    export.add_argument("--out", required=True)
    groups.add_parser("doctor")
    return top


def run(argv: list[str], cwd: Path, environ: dict[str, str]) -> int:
    args = parser().parse_args(argv)
    if args.group == "lessons" and args.action == "init":
        print(f"lessons folder ready: {log.init(args.folder)}")
        return 0
    root = log.find_root(cwd, environ)
    if args.group == "doctor":
        old = log.stale(root)
        for lesson in old:
            print(f"lesson {lesson.id} open since {lesson.time.date()}: {lesson.requirement}")
        print(f"doctor: {len(old)} lesson(s) open longer than {log.STALE_AFTER.days} days")
        return 1 if old else 0
    scan = log.scanner(log.repo_root(cwd))
    if args.group == "lesson" and args.action == "add":
        writer = log.Writer(root, scan, log.member(environ))
        lesson_id = writer.add(args.what, args.requirement, args.enforcement, args.record)
        print(f"lesson {lesson_id} added")
        return 0
    if args.group == "lesson" and args.action == "close":
        log.Writer(root, scan, log.member(environ)).close(args.id, args.change)
        print(f"lesson {args.id} closed")
        return 0
    repo = log.repo_root(cwd)
    target = args.target.resolve()
    patch = log.export(root, scan, target, target.relative_to(repo).as_posix())
    if args.out == "-":
        sys.stdout.write(patch)
    else:
        Path(args.out).write_text(patch, encoding="utf-8")
        print(f"patch written: {args.out}")
    return 0


def main() -> int:
    try:
        return run(sys.argv[1:], Path.cwd(), log.environ())
    except log.RefusedError as refusal:
        print(f"orch: {refusal}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
