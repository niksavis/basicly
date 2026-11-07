# Rounds

## Round 0

- Write the artifact version 1 and the property list with a line for what 10 means.
- Start the state file:

  ```sh
  python3 ${CLAUDE_SKILL_DIR}/../swarm/scripts/swarm.py climb-init --state DIR/climb.json --properties coverage,done-states,poka-yoke
  ```

## Each round

1. Pick 3 to 6 lenses. Use at least one lens not used in the last round.
2. Brief each reviewer with the current version, the property scale and the swarm brief. The json block holds `grades` for every property and `findings`; each finding's severity is important, nit or pre-existing, and the script refuses any other.
3. Collect replies in a new folder per round, `DIR/rN/LENS.md`.
4. Record the round:

   ```sh
   python3 ${CLAUDE_SKILL_DIR}/../swarm/scripts/swarm.py climb-round --state DIR/climb.json --lenses a,b,c --dir DIR/rN
   ```

5. Follow the printed decision. On continue, revise the artifact from the findings, and keep a short list of what changed.

## The stop rule

The script stops the climb at the first of:

- 2 rounds in a row with no important finding;
- no property rising 1.0 since the last round;
- the cap, 5 rounds by default; set 1 to 10 with `--cap N` on `climb-init`.

The score of a property is the lowest grade among the reviewers that graded it, so one strict reviewer can hold a climb back; the table shows `low-high` when they differ. A reviewer that misses a property still counts for the others. A property that no reviewer graded refuses the round.

After a stop, `climb-round` refuses another round. Add `--past-stop` to each round the user asks for after a stop; the state records it.

## After the climb

Give the user the grade table, the final version, what each round changed, and the open decisions with one recommendation each. Decisions come after the climb, never during it.
