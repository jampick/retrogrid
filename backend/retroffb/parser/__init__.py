"""Play-description parser: ESPN/NFL prose -> nflverse-vocabulary fields (DESIGN §7)."""
from .air_yards import estimate_air_yards
from .desc import ParsedDesc, apply_to_row, clean_name, parse_desc

__all__ = ["ParsedDesc", "parse_desc", "apply_to_row", "clean_name", "estimate_air_yards"]
