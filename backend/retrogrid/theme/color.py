"""Colour maths for the role resolver: sRGB <-> OKLCH, WCAG contrast."""
from __future__ import annotations

import math

RGB = tuple[float, float, float]
LCH = tuple[float, float, float]


def parse_hex(h: str) -> RGB:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def to_hex(rgb: RGB) -> str:
    return "#" + "".join(f"{round(min(1, max(0, c)) * 255):02x}" for c in rgb)


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def luminance(rgb: RGB) -> float:
    r, g, b = (_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def rgb_to_oklch(rgb: RGB) -> LCH:
    r, g, b = (_lin(c) for c in rgb)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.cbrt(v) for v in (l, m, s))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return (L, math.hypot(A, B), math.degrees(math.atan2(B, A)) % 360)


def _oklch_to_rgb_raw(lch: LCH) -> RGB:
    L, C, h = lch
    A, B = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_ = L + 0.3963377774 * A + 0.2158037573 * B
    m_ = L - 0.1055613458 * A - 0.0638541728 * B
    s_ = L - 0.0894841775 * A - 1.2914855480 * B
    l, m, s = l_**3, m_**3, s_**3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return (r, g, b)


def oklch_to_rgb(lch: LCH) -> RGB:
    """Gamut-map by shedding chroma; hue and lightness are preserved."""
    L, C, h = lch
    L = min(1.0, max(0.0, L))
    lo, hi = 0.0, C
    raw = _oklch_to_rgb_raw((L, C, h))
    if all(-1e-4 <= c <= 1 + 1e-4 for c in raw):
        return tuple(_gam(min(1, max(0, c))) for c in raw)  # type: ignore[return-value]
    for _ in range(24):
        mid = (lo + hi) / 2
        raw = _oklch_to_rgb_raw((L, mid, h))
        if all(-1e-4 <= c <= 1 + 1e-4 for c in raw):
            lo = mid
        else:
            hi = mid
    raw = _oklch_to_rgb_raw((L, lo, h))
    return tuple(_gam(min(1, max(0, c))) for c in raw)  # type: ignore[return-value]


def hue_dist(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(x + (y - x) * t for x, y in zip(a, b))  # type: ignore[return-value]


def lab_dist(a: RGB, b: RGB) -> float:
    la, ca, ha = rgb_to_oklch(a)
    lb, cb, hb = rgb_to_oklch(b)
    ax, ay = ca * math.cos(math.radians(ha)), ca * math.sin(math.radians(ha))
    bx, by = cb * math.cos(math.radians(hb)), cb * math.sin(math.radians(hb))
    return math.sqrt((la - lb) ** 2 + (ax - bx) ** 2 + (ay - by) ** 2)


def push_contrast(fg: RGB, bg: RGB, floor: float) -> RGB:
    """Move fg along the lightness axis, away from bg, until it clears floor."""
    if contrast(fg, bg) >= floor:
        return fg
    L, C, h = rgb_to_oklch(fg)
    direction = 1 if luminance(bg) < 0.4 else -1
    for _ in range(60):
        L += 0.01 * direction
        if not 0 <= L <= 1:
            break
        out = oklch_to_rgb((L, C, h))
        if contrast(out, bg) >= floor:
            return out
    return oklch_to_rgb((min(1, max(0, L)), C, h))
