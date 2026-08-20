- **The pipe-status guard pairs a `$?` with the pipeline it actually terminates, instead of
  with any pipe anywhere in the same invocation.** `$?` holds the status of the command
  immediately before it, so a command running in between claims it - but the guard scanned
  every segment after a filter and fired if a `$?` appeared in any of them. The refused shape
  was therefore the ordinary multi-step block: run a filter, then run a redirected gate, then
  read *that* gate's status. Worse, the guard's own advice text recommends the redirect half of
  exactly that block, so the check refused the habit it was installed to teach.

  Two commands were refused verbatim while this fix was being written, and both are now
  fixtures rather than paraphrases: `... | head -5; <gate> > out.txt 2>&1; echo "exit=$?"` and
  `sed ... | head -20; npx markdownlint ...; echo $?`. The earlier session's commands stay
  unreconstructed for the reason the test module already gives - a guard written against a
  paraphrase is a guard against the paraphrase.

  **One refusal recorded as a false positive was not one, and the record now says so.** A
  `jq ... | sed ... | sort > out.tsv && echo done` was refused for `sort`, and that is correct:
  `&&` branches on the pipeline's status, which is `sort`'s, so a failing `jq` upstream leaves
  the chain proceeding on a lie. It is pinned as a true positive with the redirect sitting
  between the filter and the operator, because that is where a parser would plausibly lose it.
  The measured tally on the record was corrected from 5 false against 1 true down to the cases
  a verbatim command can be re-run for.

  The five fire conditions are otherwise unchanged, each shown to fail its own test when
  reverted: `$?` immediately after, `&&`/`||` after, an `if`/`while`/`until` condition, and
  `run_in_background`. Both directions were also exercised against the live hook rather than
  only in tests: the false-positive shape now runs, and `<gate> | tail -2; echo $?` is still
  refused (basicly-g8jxj3).
