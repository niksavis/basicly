- **A gate reconciles a record's declared `depends on` against the `blocks` edges it has.**
  Two sources held one fact and nothing compared them, so an inverted edge read as correct
  from either side and held a ready lane unreachable. The check names the record, the
  declared id and the edges it does have, and refuses an empty population rather than
  reporting a vacuous pass (`basicly-9yyj6i`).
