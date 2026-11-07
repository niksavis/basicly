# Comment ratchet

Strict mode, `[hooks.comments] policy = "no-comments"`, is the default and the goal.
When a repository explicitly selects `[hooks.comments] policy = "no-comment-ratchet"`,
leave unrelated legacy prose alone. Add no new prose.

The hook compares complete parsed comments and docstrings in HEAD and the staged index.
Rewritten or duplicated prose is new prose.

Run `python3 .basicly/core/hooks/comment_ratchet.py <path>` for the Git-backed check in a
basicly repository. The kit `check` command stays strict.
Remove the ratchet setting once the repository has no prose comments.
