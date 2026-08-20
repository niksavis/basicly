- **The worktree concurrency cap counts checkouts instead of records, so a session whose
  directory is gone stops holding a slot.** Both routes to a record outliving its checkout
  were hit on 2026-08-20: `basicly worktree cleanup` without `--force` keeps the record when
  the branch survives, and a plain `git worktree remove` tells the engine nothing at all. The
  refusal then read `worktree concurrency cap reached (5/5)` with **three** worktrees on disk,
  and `basicly worktree list` marked the other two `(stale: dir missing)` while they went on
  blocking every provision. A stale record occupies no checkout and contends for no gate, so
  it now counts for nothing.

  **The refusal also names what to reclaim.** The old message named only the cap, which makes
  raising the cap the cheapest reading — and that is what an operator did instead of freeing a
  slot. It now reads `cap reached (1/1 live)` followed by the records whose checkout is
  already gone and the `basicly worktree cleanup <name> --force` that clears them. That
  message had been hand-written separately at both places the cap is evaluated, `basicly
  worktree create` and the loop's build advance; both now compose it from one place, so the
  two cannot drift again and neither can disagree with the count (basicly-gtoqu9).
