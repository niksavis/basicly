- **The harness board is a wall layout that answers four questions, not a dump of the
  schema.** The previous render gave every schema key a fixed-height box its content
  overflowed and repeated the same freshness sentence on all ten, so the page did not
  say what is being built, where the loop is, what is waiting, or what is in the
  backlog. It now draws eight fixed rows at 1920x1080 with no scrollbar: a watch band
  in the page's only alarm colour, a loop row counting each of the seven phases and
  marking where the lanes are, fixed-size in-flight cards beside the ranked ready set,
  a footer carrying the backlog with a closed bar and a per-priority histogram plus
  gates, spend and health, an event ticker, and the verdict's whole section roster. A
  region that cannot draw everything says `+N more` naming what it dropped, the
  freshness reading is taken once for the page rather than once per panel, an absent
  section still reads `not emitted by this producer`, a bar is still refused unless
  both of its terms were measured, and the layout reflows to one column below 1280px
  (`basicly-rbnz49`).
