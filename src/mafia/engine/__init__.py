"""Mode-independent game engine: event log, visibility, seat requests, deterministic replay.

Nothing in this package performs I/O. Time and randomness are injected, so the same event log
always folds into the same state.
"""
