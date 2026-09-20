"""Resolve an Omarchy colors.toml into the console's eight colour roles.

DESIGN §3a. Theme colour names lie (Retro 82's `magenta` is teal, `white`
has no hues), so roles are *resolved* in OKLCH, never looked up by name.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import color as c

ROLES = ("bg", "rail", "grid", "you", "them", "alert", "gain", "hot", "dim")

YOU_PREF = ("cyan", "blue", "accent", "green")
THEM_PREF = ("magenta", "red", "orange", "yellow")
ALERT_PREF = ("yellow", "orange", "accent")

MIN_CONTRAST = 3.0      # eligibility against bg
MIN_CHROMA = 0.04
ACCEPT_SEP = 75.0       # first conventional pair this far apart wins
MIN_SEP = 60.0          # below this the theme is treated as monochrome
TEXT_FLOOR = 4.5
DIM_FLOOR = 3.0


@dataclass
class Palette:
    mode: str                      # "dark" | "light"
    blend: str                     # "additive" | "multiply"
    mono: bool                     # True -> ally/enemy split by stroke style
    roles: dict[str, str]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _variants(colors: dict[str, str], name: str) -> list[str]:
    return [k for k in (name, f"bright_{name}") if k in colors]


def _eligible(colors: dict[str, str], key: str, bg: c.RGB, floor: float = MIN_CONTRAST) -> bool:
    rgb = c.parse_hex(colors[key])
    return c.contrast(rgb, bg) >= floor and c.rgb_to_oklch(rgb)[1] >= MIN_CHROMA


def _candidates(colors, prefs, bg, floor: float = MIN_CONTRAST) -> list[str]:
    out = []
    for name in prefs:
        for key in _variants(colors, name):
            if _eligible(colors, key, bg, floor):
                out.append(key)
                break
    return out


def _pick_pair(colors, bg) -> tuple[str, str, float] | None:
    yous, thems = _candidates(colors, YOU_PREF, bg), _candidates(colors, THEM_PREF, bg)
    best = None
    for y in yous:
        for t in thems:
            if colors[y].lower() == colors[t].lower():
                continue
            sep = c.hue_dist(c.rgb_to_oklch(c.parse_hex(colors[y]))[2],
                             c.rgb_to_oklch(c.parse_hex(colors[t]))[2])
            if sep >= ACCEPT_SEP:
                return y, t, sep
            if best is None or sep > best[2]:
                best = (y, t, sep)
    return best if best and best[2] >= MIN_SEP else None


def _grid(colors, bg: c.RGB, fg: c.RGB) -> c.RGB:
    g = c.parse_hex(colors.get("selection") or colors.get("muted") or colors["background"])
    for _ in range(40):
        r = c.contrast(g, bg)
        if r > 1.8:
            g = c.mix(g, bg, 0.12)
        elif r < 1.3:
            g = c.mix(g, fg, 0.06)
        else:
            break
    return g


def resolve(colors: dict[str, str], overrides: dict[str, str] | None = None) -> Palette:
    colors = {k: v for k, v in colors.items() if isinstance(v, str) and v.startswith("#")}
    mode = "light" if c.luminance(c.parse_hex(colors["background"])) > 0.4 else "dark"
    notes: list[str] = []

    bg = c.parse_hex(colors["background"])
    fg = c.parse_hex(colors.get("bright_foreground") or colors["foreground"])
    hot = c.push_contrast(fg, bg, 7.0)
    dim_src = colors.get("dark_foreground") or colors.get("muted") or colors["foreground"]
    dim = c.push_contrast(c.parse_hex(dim_src), bg, DIM_FLOOR)
    if c.contrast(dim, bg) > c.contrast(hot, bg) * 0.75:    # dim must stay dim
        dim = c.mix(dim, bg, 0.35)
        dim = c.push_contrast(dim, bg, DIM_FLOOR)
    rail = c.parse_hex(colors.get("dark_background") or colors["background"])
    grid = _grid(colors, bg, fg)

    pair = _pick_pair(colors, bg)
    mono = pair is None
    if mono:
        notes.append("monochrome: ally/enemy split by stroke style")
        you = them = gain = hot
        acc = colors.get("accent")
        alert = c.parse_hex(acc) if acc and _eligible(colors, "accent", bg) else hot
    else:
        yk, tk, sep = pair
        notes.append(f"you={yk} them={tk} sep={sep:.0f}deg")
        you, them = c.parse_hex(colors[yk]), c.parse_hex(colors[tk])
        alert = _pick_alert(colors, bg, you, them, tk, notes)
        gain = _pick_gain(colors, bg, you, them, mode, notes)

    roles = {
        "bg": bg, "rail": rail, "grid": grid, "hot": hot, "dim": dim,
        "you": c.push_contrast(you, bg, TEXT_FLOOR),
        "them": c.push_contrast(them, bg, TEXT_FLOOR),
        "alert": c.push_contrast(alert, bg, TEXT_FLOOR),
        "gain": c.push_contrast(gain, bg, TEXT_FLOOR),
    }
    out = {k: c.to_hex(v) for k, v in roles.items()}
    for k, v in (overrides or {}).items():
        if k in ROLES:
            out[k] = colors.get(v, v)
            notes.append(f"override {k}")
    return Palette(mode=mode, blend="multiply" if mode == "light" else "additive",
                   mono=mono, roles=out, notes=notes)


def _pick_alert(colors, bg, you, them, them_key, notes) -> c.RGB:
    th = c.rgb_to_oklch(them)
    yh = c.rgb_to_oklch(you)
    scored = []
    for key in _candidates(colors, ALERT_PREF, bg, floor=1.6):   # pushed to TEXT_FLOOR later
        if colors[key].lower() == colors[them_key].lower():
            continue
        lch = c.rgb_to_oklch(c.parse_hex(colors[key]))
        if c.hue_dist(lch[2], yh[2]) < (75 if key == "accent" else 40):
            continue
        far = c.hue_dist(lch[2], th[2]) >= 30
        scored.append((far, abs(lch[0] - th[0]), key))
    if not scored:
        notes.append("alert: synthesized from them")
        step = 0.16 if th[0] < 0.7 else -0.16
        return c.oklch_to_rgb((th[0] + step, th[1] * 0.8, (th[2] + 35) % 360))
    for far, dl, key in scored:             # preference order first
        if far or dl >= 0.12:
            notes.append(f"alert={key}")
            return c.parse_hex(colors[key])
    _, dl, key = max(scored, key=lambda s: s[1])
    L, C, h = c.rgb_to_oklch(c.parse_hex(colors[key]))
    L += (0.12 - dl) * (1 if L >= th[0] else -1)
    notes.append(f"alert={key} (lightness nudged off them)")
    return c.oklch_to_rgb((L, C, h))


def _pick_gain(colors, bg, you, them, mode, notes) -> c.RGB:
    th = c.rgb_to_oklch(them)[2]
    for key in _variants(colors, "green"):
        rgb = c.parse_hex(colors[key])
        lch = c.rgb_to_oklch(rgb)
        if (lch[1] >= MIN_CHROMA and c.hue_dist(lch[2], th) >= MIN_SEP
                and c.lab_dist(c.push_contrast(rgb, bg, TEXT_FLOOR), you) >= 0.08):
            notes.append(f"gain={key}")
            return rgb
    L, C, h = c.rgb_to_oklch(you)
    lifted = c.oklch_to_rgb((L + (0.07 if mode == "dark" else -0.07), C, h))
    fg = c.parse_hex(colors.get("bright_foreground") or colors["foreground"])
    if c.lab_dist(lifted, fg) < 0.10:       # would read as plain hot text
        notes.append("gain=you (glyph carries it)")
        return you
    notes.append("gain: you, lifted")
    return lifted


NEON = Palette(
    mode="dark", blend="additive", mono=False,
    roles={"bg": "#05060a", "rail": "#030408", "grid": "#0e2a33", "you": "#22e0f0",
           "them": "#ff2b8a", "alert": "#ffb020", "gain": "#39ff88",
           "hot": "#eafcff", "dim": "#4a6a78"},
    notes=["built-in"],
)
