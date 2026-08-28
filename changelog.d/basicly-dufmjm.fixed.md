- **A dispatch starts on Windows when the agent is a `.cmd` shim.** `runner.run` handed a
  bare command name to `Popen`, and Windows resolves that only to `.exe`, so `claude.cmd` as
  npm installs it raised `WinError 2`. The runner now resolves the command through
  `shutil.which` against the dispatch environment's `PATH` before it spawns (basicly-dufmjm, basicly-xyx556).
