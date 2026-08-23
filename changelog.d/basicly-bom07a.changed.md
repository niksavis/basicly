- The context-occupancy meter moved to its own module below `loop` and `supervise`, so
  the engine's declared `loop -> supervise` import cycle is gone and the layering
  contract no longer carries its exemption.
