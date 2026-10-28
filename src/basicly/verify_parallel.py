from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from . import usage, verify_log
from .config import VerifyCheck, VerifyConfig
from .verify_artifact import CheckResult, blocked_result

Runner = Callable[[VerifyCheck], CheckResult]

Annotation = tuple[str, str] | None


def start(repo_root: Path, config: VerifyConfig, run: Runner) -> ParallelChecks | None:
    if config.jobs <= 1:
        return None
    return ParallelChecks(repo_root, config.jobs, config.fail_fast, run)


def annotated(result: CheckResult, annotation: Annotation) -> CheckResult:
    if annotation is None:
        return result
    return replace(result, reuse_reason=annotation[0], input_fingerprint=annotation[1])


class ParallelChecks:
    def __init__(self, repo_root: Path, jobs: int, fail_fast: bool, run: Runner) -> None:
        self._root = repo_root
        self._fail_fast = fail_fast
        self._run = run
        self._pool = ThreadPoolExecutor(max_workers=jobs)
        self._stop = threading.Event()
        self._stopped_by: list[str] = []
        self._pending: list[tuple[int, VerifyCheck, Annotation, Future[CheckResult | None]]] = []

    def submit(self, index: int, check: VerifyCheck, annotation: Annotation) -> CheckResult:
        self._pending.append((index, check, annotation, self._pool.submit(self._task, check)))
        return blocked_result(check.name, check.command, "a check still running")

    def fill(self, results: list[CheckResult]) -> None:
        try:
            for index, check, annotation, future in self._pending:
                result = future.result()
                if result is None:
                    result = blocked_result(check.name, check.command, self._stopped_by[0])
                    print(f"==> {check.name} NOT RUN: {result.detail}", flush=True)
                    results[index] = result
                    continue
                results[index] = annotated(self._report(result), annotation)
                if results[index].reuse_reason:
                    print(f"{check.name}: {results[index].reuse_reason}", flush=True)
        finally:
            self._pool.shutdown(cancel_futures=True)

    def _task(self, check: VerifyCheck) -> CheckResult | None:
        if self._stop.is_set():
            return None
        result = self._run(check)
        if self._fail_fast and result.status == "fail":
            self._stopped_by.append(check.name)
            self._stop.set()
        return result

    def _report(self, result: CheckResult) -> CheckResult:
        print(f"==> {result.name}", flush=True)
        print(result.output, end="", flush=True)
        if result.status == "pass":
            usage.record_verify_check(self._root, result.name)
        if result.status != "fail" or result.detail:
            return result
        log = verify_log.write(self._root, result.name, result.output)
        return replace(result, detail=verify_log.pointer(log, self._root))
