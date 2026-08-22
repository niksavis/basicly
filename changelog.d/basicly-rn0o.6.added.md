- **A human can now act on a lane from the board, and the board still holds no authority of its
  own.** Every action the page offers is run by spawning the `basicly` CLI, which writes what
  that command has always written; the board decides nothing and may be removed without
  changing what any write means. `basicly board serve --no-actions` removes the route entirely.

  The anti-autopilot boundary is kept rather than worked around. The board never reads
  `.basicly/usage/checkpoint-confirms.json`, because a page that read it and offered a
  one-click approve would be relaying the confirm code to itself. It presents an empty box a
  human fills - deliberately more friction than a button.

  Three mitigations on the one `subprocess.run` behind it, each asserted in
  `tests/test_board_actions.py` rather than left to trust: the executable is resolved with
  `shutil.which` and is never a string, every field is matched against an id pattern that
  admits no leading `-` so a POST cannot smuggle a flag past argparse, and `shell` is never
  set on any path.
