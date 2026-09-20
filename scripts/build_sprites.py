#!/usr/bin/env python
"""Headshot -> data sprite batch (DESIGN §6). Offline; re-run weekly.

Output is *data, not colour*: a 96x96 PNG where
    R = tone index (0..3 -> 0,85,170,255)     G = region (255 jersey, 0 skin/hair)
    B = silhouette / feature edge (255)       A = mask
The console palette-maps it at draw time, so one sprite serves every theme
and both sides of a matchup.

Usage: build_sprites.py [--all]    (default: rostered players only)
"""
from __future__ import annotations

import argparse
import asyncio
import colorsys
import io
import sys
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from retroffb.providers.directory import NflversePlayerDirectory   # noqa: E402
from retroffb.providers.slate import load_slate                    # noqa: E402
from retroffb.providers.league import make_league  # noqa: E402

OUT = ROOT / "data" / "sprites"
RAW = ROOT / "data" / "headshots"
SIZE = 96


def to_sprite(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    bbox = img.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    # square canvas, bust anchored to the bottom edge so shoulders bleed off it
    side = int(min(img.width, img.height * 1.12))          # head-and-shoulders: let the arms fall off-frame
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, side - img.height))
    rgb = ImageEnhance.Contrast(canvas.convert("RGB")).enhance(1.55)      # contrast BEFORE downsample
    rgb = rgb.resize((SIZE, SIZE), Image.LANCZOS)
    alpha = canvas.getchannel("A").resize((SIZE, SIZE), Image.LANCZOS)

    a = np.asarray(alpha, dtype=np.float32) / 255
    px = np.asarray(rgb, dtype=np.float32) / 255
    mask = a > 0.5
    lum = 0.2126 * px[..., 0] + 0.7152 * px[..., 1] + 0.0722 * px[..., 2]

    # hue split: skin is warm and moderately saturated; jersey is whatever else sits low in frame
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in px.reshape(-1, 3)]).reshape(SIZE, SIZE, 3)
    hue, sat = hsv[..., 0] * 360, hsv[..., 1]
    skin = ((hue < 50) | (hue > 340)) & (sat > 0.15) & (sat < 0.8) & (lum > 0.12)
    rows = np.arange(SIZE)[:, None] / SIZE
    jersey = mask & ~skin & (rows > 0.62)
    jersey |= mask & (rows > 0.86)                                       # collar line and below is always kit

    # 4-tone posterize, equalised per region so both keep their own contrast
    tone = np.zeros((SIZE, SIZE), dtype=np.uint8)
    for region in (jersey, mask & ~jersey):
        if region.sum() < 8:
            continue
        qs = np.quantile(lum[region], [0.25, 0.5, 0.78])
        tone[region] = np.digitize(lum[region], qs).astype(np.uint8)

    # edges: silhouette + the strongest interior features (brow, jaw, beard line, numerals)
    sil = mask & ~np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))).astype(bool)
    g = np.asarray(Image.fromarray((lum * 255).astype(np.uint8)).filter(ImageFilter.FIND_EDGES), dtype=np.float32)
    inner = mask & (g > np.quantile(g[mask], 0.93)) if mask.any() else mask
    edge = sil | (inner & ~jersey)

    out = np.zeros((SIZE, SIZE, 4), dtype=np.uint8)
    out[..., 0] = tone * 85
    out[..., 1] = jersey * 255
    out[..., 2] = edge * 255
    out[..., 3] = mask * 255
    return Image.fromarray(out, "RGBA")


def defence_sprite(team: str) -> Image.Image:
    """Team defences have no face: a shield with the abbreviation cut into it."""
    big = Image.new("L", (SIZE * 4, SIZE * 4), 0)
    d = ImageDraw.Draw(big)
    s = SIZE * 4
    d.polygon([(s * .14, s * .08), (s * .86, s * .08), (s * .86, s * .52), (s * .5, s * .96), (s * .14, s * .52)], fill=255)
    shield = np.asarray(big.resize((SIZE, SIZE), Image.LANCZOS)) > 128
    txt = Image.new("L", (SIZE, SIZE), 0)
    font = ImageFont.load_default(size=30)
    td = ImageDraw.Draw(txt)
    w = td.textlength(team, font=font)
    td.text(((SIZE - w) / 2, 24), team, fill=255, font=font)
    letters = (np.asarray(txt) > 128) & shield
    sil = shield & ~np.asarray(Image.fromarray((shield * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(5))).astype(bool)
    rows = np.arange(SIZE)[:, None]
    out = np.zeros((SIZE, SIZE, 4), dtype=np.uint8)
    out[..., 0] = np.where(letters, 255, np.where((rows // 6) % 2 == 0, 85, 170)) * shield
    out[..., 1] = shield * 255
    out[..., 2] = (sil | (letters & ~np.asarray(Image.fromarray((letters * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))).astype(bool))) * 255
    out[..., 3] = shield * 255
    return Image.fromarray(out, "RGBA")


async def fetch(client: httpx.AsyncClient, sem: asyncio.Semaphore, pid: str, url: str) -> bytes | None:
    cache = RAW / f"{pid}.png"
    if cache.is_file():
        return cache.read_bytes()
    async with sem:
        try:
            r = await client.get(url, follow_redirects=True, timeout=20)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                cache.write_bytes(r.content)
                return r.content
        except httpx.HTTPError:
            pass
    return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="rosters of today's LIVE league (data/live) instead of the sim slate")
    ap.add_argument("--all", action="store_true", help="every player who appears in the slate, not just rostered")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    slate_dir = ROOT / "data" / ("live" if args.live else "slate")
    slate = load_slate(slate_dir)
    directory = NflversePlayerDirectory.from_data_dir(week=slate.week, season=slate.season)
    league = make_league(slate_dir, directory, slate.week, slate.season)
    ids = {s.player_id for t in league.league().teams for s in league.roster(t.key, slate.week).slots}
    if args.all:
        for p in slate.plays:
            ids |= {i for i in (p.passer_id, p.receiver_id, p.rusher_id, p.kicker_id) if i}
    todo = [i for i in sorted(ids) if not (OUT / f"{i}.png").is_file()]
    made = missing = 0
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 RetroFFB sprite batch"}) as client:
        async def one(pid: str) -> None:
            nonlocal made, missing
            pl = directory.player(pid)
            if pid.startswith("DEF-"):
                defence_sprite(pid[4:]).save(OUT / f"{pid}.png")
                made += 1
                return
            raw = await fetch(client, sem, pid, pl.headshot) if pl and pl.headshot else None
            if not raw:
                missing += 1                         # console falls back to the wireframe bust
                return
            try:
                to_sprite(Image.open(io.BytesIO(raw))).save(OUT / f"{pid}.png")
                made += 1
            except Exception as e:                   # noqa: BLE001
                print(f"  {pid}: {e}")
                missing += 1
        await asyncio.gather(*(one(i) for i in todo))
    print(f"sprites: {made} made, {missing} missing, {len(ids) - len(todo)} cached")


if __name__ == "__main__":
    asyncio.run(main())
