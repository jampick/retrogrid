"""ThemeProvider implementations (DESIGN §3a, Delivery)."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from .resolver import NEON, Palette, resolve

BUNDLED_DIR = Path(__file__).parent / "bundled"
OMARCHY_STATE = Path.home() / ".local/state/omarchy/current"
USER_OVERRIDES = Path.home() / ".config/retrogrid/themes"


@dataclass
class Theme:
    slug: str
    name: str
    palette: Palette
    font: str | None = None
    source: str = "bundled"         # "bundled" | "omarchy" | "builtin"

    def to_dict(self) -> dict:
        return {"slug": self.slug, "name": self.name, "font": self.font,
                "source": self.source, **self.palette.to_dict()}


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def _title(slug: str) -> str:
    return slug.replace("-", " ").title()


def _overrides(theme_dir: Path | None, slug: str) -> dict[str, str]:
    for p in ((theme_dir / "retrogrid.toml") if theme_dir else None, USER_OVERRIDES / f"{slug}.toml"):
        if p and p.is_file():
            return tomllib.loads(p.read_text()).get("roles", {})
    return {}


NEON_THEME = Theme("neon", "NEON", NEON, source="builtin")


class BundledThemeProvider:
    """Vendored stock Omarchy palettes — for viewers who do not run Omarchy."""

    def __init__(self) -> None:
        self._themes: dict[str, Theme] = {"neon": NEON_THEME}
        for f in sorted(BUNDLED_DIR.glob("*.toml")):
            slug = f.stem
            pal = resolve(tomllib.loads(f.read_text()), _overrides(None, slug))
            self._themes[slug] = Theme(slug, _title(slug), pal)

    def all(self) -> list[Theme]:
        return list(self._themes.values())

    def get(self, slug: str) -> Theme | None:
        return self._themes.get(slug)


class OmarchyThemeProvider:
    """Follows the live system theme on an Omarchy host."""

    def __init__(self, state: Path = OMARCHY_STATE) -> None:
        self.state = state
        self._bump = asyncio.Event()

    @property
    def available(self) -> bool:
        return (self.state / "theme/colors.toml").is_file()

    def _font(self) -> str | None:
        if not shutil.which("omarchy"):
            return None
        try:
            out = subprocess.run(["omarchy", "font", "current"], capture_output=True,
                                 text=True, timeout=3).stdout.strip()
            return out or None
        except Exception:
            return None

    def current(self) -> Theme:
        theme_dir = self.state / "theme"
        colors = tomllib.loads((theme_dir / "colors.toml").read_text())
        name_file = self.state / "theme.name"
        name = name_file.read_text().strip() if name_file.is_file() else "System"
        slug = _slug(name)
        return Theme(slug, name, resolve(colors, _overrides(theme_dir, slug)),
                     font=self._font(), source="omarchy")

    def _stamp(self) -> tuple:
        out = []
        for p in (self.state / "theme.name", self.state / "theme/colors.toml"):
            try:
                st = p.stat()
                out.append((st.st_mtime_ns, st.st_ino))
            except FileNotFoundError:
                out.append(None)
        return tuple(out)

    def poke(self) -> None:
        """Called by the theme-set hook endpoint for an instant retune."""
        self._bump.set()

    async def watch(self, interval: float = 1.0) -> AsyncIterator[Theme]:
        last = self._stamp()
        while True:
            try:
                await asyncio.wait_for(self._bump.wait(), timeout=interval)
                self._bump.clear()
                await asyncio.sleep(0.3)      # let omarchy finish swapping the dir
            except asyncio.TimeoutError:
                pass
            now = self._stamp()
            if now != last and self.available:
                last = now
                try:
                    yield self.current()
                except Exception:
                    continue
