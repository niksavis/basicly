- **A landing now states what it took: the branch tip and how many commits came with it.**
  `basicly worktree merge` and every landing behind it reported only the merge commit it
  produced — `merged harness/feat into main @ d605fb4` — which says nothing about the commits
  that were merged. On 2026-08-20 an agent finished, reported its commit, was resumed by a
  follow-up message, committed another 92 lines, and the landing took the moved tip; nothing
  in the output said the tip had moved, and the commit was recovered only because a
  `git diff <branch> main` was run by hand before cleanup. The report now reads
  `merged harness/feat @ 1a2b3c4d5e6f (3 commit(s)) into main @ d605fb4`, so the one
  irreversible step in the loop names the thing it consumed rather than only the thing it
  produced.

  The count is read **before** the merge, because afterwards it is zero for every branch, and
  a count git cannot answer is reported as `an uncounted number of commits` rather than as
  `0`: this exists so a landing can state what it took, and a number nothing measured is the
  same false report the change closes (basicly-aim1qi).
