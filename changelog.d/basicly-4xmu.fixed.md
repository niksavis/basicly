A dispatched classify, decompose or lane run now carries the persona its phase declares.
`resolve_role` had exactly one caller, inside `_run_agent`, whose only call sites are build and
repair — so the two proposal dispatches and the supervised lane dispatch all ran on the default
runner unspecialised, and no recorded dispatch had ever reached an argv with `--agent` on it.
The work-type proposal now resolves classify's persona, the child-plan proposal decompose's, and
a lane build's. The phase is passed per call site rather than derived from the proposal's label,
so a third proposal cannot silently inherit no persona; and resolution still answers None for a
family that cannot select a role, so an un-upgraded consumer gets an unspecialised loop rather
than a flag its host would drop without a word.
