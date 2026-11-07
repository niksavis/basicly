# Writing the question

A council answers one closed question. A good question has:

- one decision, stated in one sentence;
- 2 to 4 options, each with a short label the voters copy exactly;
- the facts every voter needs, with sources;
- the constraint that cannot change, such as a user rule or a budget.

Split a question with two decisions into two councils. Put an open question ("what should we do?") through `swarm` first, then a council on the options it produced.

## Tie and quorum

- The script counts a majority of all invited voters, not of the votes cast. A voter that did not return, or voted a label not in `--options`, counts against every option and shows as dissent.
- With no majority, the decision goes to the user with the vote table and each side's strongest counter.
