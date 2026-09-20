"""Timing and geometry constants for the play grammar (DESIGN §5).

Hand-set from general football knowledge. These are the ~20 numbers the
design says to fit once from Big Data Bowl frames; until that fit exists they
live here, in one place, so the fit is a drop-in replacement.
"""

FIELD_W = 53.333
CENTER_X = FIELD_W / 2
HASH_L, HASH_R = 23.58, 29.75
THIRD_X = {"left": 10.5, "middle": CENTER_X, "right": FIELD_W - 10.5}

# seconds from snap to release, by concept
T_THROW = {"screen": 1.35, "short": 2.25, "deep": 3.05}
DROP_DEPTH = {"screen": 1.5, "short": 4.0, "deep": 7.0}     # from under centre
GUN_DRIFT = 1.6                                            # extra yards from shotgun depth

BALL_SPEED = 19.0          # yd/s, pass
BALL_MIN_FLIGHT = 0.45
SPEED = {"WR": 8.6, "TE": 7.6, "RB": 8.3, "QB": 7.2, "OL": 4.5, "DL": 5.2, "LB": 7.4, "DB": 8.4, "K": 5.0}
CARRY_FACTOR = 0.86        # ball carriers weave, so net downfield speed is lower
ACCEL_TIME = 0.55          # seconds to reach top speed
CLOSING_SPEED = 8.8        # tackler converge
T_HANDOFF = {"gun": 0.85, "under": 1.05, "toss": 0.6}
T_SACK = 3.3
T_SCRAMBLE_BREAK = 2.6
FG_SNAP, FG_KICK = 0.75, 1.30
PUNT_SNAP, PUNT_KICK, PUNT_HANG = 0.80, 2.05, 4.3
KICK_HANG = 3.9
