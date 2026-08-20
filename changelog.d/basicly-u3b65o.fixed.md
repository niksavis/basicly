- **A push that would race a landing is refused naming the contention, instead of dying on a
  stash.** `pre-commit` stashes the unstaged tree before it runs the pre-push stage and
  restores it after. A landing writing the ledger inside that window changes the tree under
  the stash, so the restore conflicts and the push aborts with `Stashed changes conflicted
  with hook auto-fixes`. The commits are intact, and the message names a git mechanism rather
  than the fault, so an operator reads a local mistake instead of two engine operations
  racing - the third surface of one class, after `basicly-kjc5.63` on the base checkout
  commit. On every surface the text named git and never contention, so it was diagnosed
  wrongly every time.

  The refusal runs **before the stash exists**, so there is nothing to conflict, and it says
  the three things the stash message withheld: which pid holds the tree, that the git text the
  operator is about to see is about the stash and not the fault, and that their commits are
  unaffected. The signal is deterministic rather than a guess: the ledger's lock is a file
  whose existence is the lock, carrying its holder's pid, and both the lock's name and the
  liveness rule are read from the kit's own `events.py` rather than respelled - a second
  spelling of either is the drift that module documents as the defect this design keeps paying
  for.

  **It fails quiet in every ambiguous case, and that is deliberate.** No kit installed, no
  lock file, a lock it cannot parse, a pid that is gone, and a pid the platform cannot judge -
  Windows has no stdlib liveness probe, because `os.kill(pid, 0)` there calls
  `TerminateProcess` and would kill the process it was asking about. Refusing on the hook's
  own uncertainty would make it look like contention, and would block every push on that
  platform for as long as a stale lock file sat on disk. Each of the three liveness answers is
  injected as test data rather than raced, so the verdict is a property of the fixture and not
  of whichever machine ran it.

  Two defects in this change were caught only by running the real hook end to end rather than
  by its unit tests: a parenthesised `except` clause the house form forbids where nothing
  binds, and a dynamically loaded module typed as `object`, which broke pyright on every
  attribute it reached (basicly-u3b65o).
