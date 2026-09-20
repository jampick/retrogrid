"""Role resolver + Omarchy provider (DESIGN §3a)."""
import asyncio
import tomllib

import pytest

from retroffb.theme import color as c
from retroffb.theme.provider import BUNDLED_DIR, BundledThemeProvider, OmarchyThemeProvider
from retroffb.theme.resolver import ROLES, resolve

THEMES = sorted(BUNDLED_DIR.glob("*.toml"))


@pytest.mark.parametrize("path", THEMES, ids=lambda p: p.stem)
def test_every_stock_theme_resolves_legibly(path):
    pal = resolve(tomllib.loads(path.read_text()))
    assert set(pal.roles) == set(ROLES)
    bg = c.parse_hex(pal.roles["bg"])
    for role in ("you", "them", "alert", "gain", "hot"):
        assert c.contrast(c.parse_hex(pal.roles[role]), bg) >= 4.4, role
    assert c.contrast(c.parse_hex(pal.roles["dim"]), bg) >= 2.9
    assert 1.2 <= c.contrast(c.parse_hex(pal.roles["grid"]), bg) <= 1.9
    assert pal.blend == ("multiply" if pal.mode == "light" else "additive")
    if not pal.mono:                       # two-family rule: ally and enemy hues stay apart
        you, them = (c.rgb_to_oklch(c.parse_hex(pal.roles[r]))[2] for r in ("you", "them"))
        assert c.hue_dist(you, them) >= 55


def test_names_lie_retro82():
    """Retro 82's `magenta` is teal; the resolver must not pick it for THEM."""
    pal = resolve(tomllib.loads((BUNDLED_DIR / "retro-82.toml").read_text()))
    assert pal.roles["them"] == "#f85525" and not pal.mono


def test_monochrome_falls_back_to_stroke_style():
    assert resolve(tomllib.loads((BUNDLED_DIR / "white.toml").read_text())).mono


def test_override_pins_a_role():
    colors = tomllib.loads((BUNDLED_DIR / "nord.toml").read_text())
    assert resolve(colors, {"them": "#ff0000"}).roles["them"] == "#ff0000"
    assert resolve(colors, {"them": "red"}).roles["them"] == colors["red"]


def test_bundled_has_neon_and_stock():
    slugs = {t.slug for t in BundledThemeProvider().all()}
    assert {"neon", "retro-82", "tokyo-night", "catppuccin-latte"} <= slugs


def test_omarchy_watch_sees_theme_swap(tmp_path):
    (tmp_path / "theme").mkdir()
    (tmp_path / "theme/colors.toml").write_text((BUNDLED_DIR / "nord.toml").read_text())
    (tmp_path / "theme.name").write_text("Nord")
    prov = OmarchyThemeProvider(state=tmp_path)
    assert prov.current().slug == "nord"

    async def go():
        agen = prov.watch(interval=0.05)
        task = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.1)
        (tmp_path / "theme/colors.toml").write_text((BUNDLED_DIR / "gruvbox.toml").read_text())
        (tmp_path / "theme.name").write_text("Gruvbox")
        return await asyncio.wait_for(task, 3)

    assert asyncio.run(go()).slug == "gruvbox"
