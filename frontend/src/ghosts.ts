// The ghost system (DESIGN §6): flanking holograms whose brightness is the
// live fantasy delta. Sprites are data (see sprites.ts), tinted per theme.

import { palette } from "./palette";
import { SP, loadSprite, proceduralBust, tint } from "./sprites";
import type { GhostInfo } from "./types";

type Slot = "you" | "them";

interface Loaded { data: ImageData | null; url: string | null }

export class Ghosts {
  private ctx: CanvasRenderingContext2D;
  private info: Record<Slot, GhostInfo | null> = { you: null, them: null };
  private loaded: Record<Slot, Loaded> = { you: { data: null, url: null }, them: { data: null, url: null } };
  private tinted: Record<Slot, HTMLCanvasElement | null> = { you: null, them: null };
  private heat: Record<Slot, number> = { you: 0, them: 0 };
  private flick: Record<Slot, number> = { you: 0, them: 0 };
  private nextFlick: Record<Slot, number> = { you: 2, them: 3.5 };
  private wall = 0;
  anchors: Record<Slot, { x: number; y: number }> = { you: { x: 0, y: 0 }, them: { x: 0, y: 0 } };
  scale = 3;

  constructor(private canvas: HTMLCanvasElement) {
    this.ctx = canvas.getContext("2d")!;
    palette.subscribe(() => { this.retint("you"); this.retint("them"); });
  }

  resize(w: number, h: number): void { this.canvas.width = w; this.canvas.height = h; }

  set(slot: Slot, g: GhostInfo | null): void {
    const prev = this.info[slot];
    this.info[slot] = g;
    if (!g) return;
    if (prev?.player_id !== g.player_id) { this.flick[slot] = 0.5; }    // handover glitch
    if (g.sprite !== this.loaded[slot].url || prev?.player_id !== g.player_id) this.load(slot, g.sprite);
  }

  private load(slot: Slot, url: string | null): void {
    this.loaded[slot] = { data: null, url };
    this.retint(slot);
    if (!url) return;
    void loadSprite(url).then((data) => {
      if (this.loaded[slot].url !== url) return;
      this.loaded[slot].data = data;
      this.retint(slot);
    });
  }

  private retint(slot: Slot): void {
    this.tinted[slot] = tint(this.loaded[slot].data ?? proceduralBust(), slot);
  }

  tick(dt: number): void {
    this.wall += dt;
    for (const s of ["you", "them"] as Slot[]) {
      const target = this.info[s]?.heat ?? 0;
      this.heat[s] += (target - this.heat[s]) * Math.min(1, dt * 1.5);
      this.flick[s] = Math.max(0, this.flick[s] - dt);
      this.nextFlick[s] -= dt;
      if (this.nextFlick[s] <= 0) {
        this.flick[s] = 0.05 + Math.random() * 0.06;
        // Hot players hold steady; cold ones sputter.
        this.nextFlick[s] = (2 + Math.random() * 4) * (0.6 + this.heat[s] * 2.2);
      }
    }
  }

  draw(): void {
    const c = this.ctx, W = this.canvas.width, H = this.canvas.height;
    c.clearRect(0, 0, W, H);
    for (const s of ["you", "them"] as Slot[]) {
      const img = this.tinted[s];
      if (!img || !this.info[s]) continue;
      const S = this.scale, size = SP * S, heat = this.heat[s];
      const flick = this.flick[s] > 0;
      const jitter = flick ? Math.round((Math.random() - 0.5) * S * 3) : 0;
      const x = Math.round(this.anchors[s].x - size / 2) + jitter, y = Math.round(this.anchors[s].y - size / 2);
      // Brightness IS meaning: a smudge at zero, burning at a monster day.
      let alpha = 0.07 + 0.2 * heat;
      if (flick) alpha *= 0.35;
      c.save();
      c.imageSmoothingEnabled = false;
      c.globalCompositeOperation = palette.light ? "multiply" : "lighter";
      if (palette.light) alpha *= 1.6;
      // channel split: you-tint slips left, them-tint slips right
      c.globalAlpha = alpha * 0.55;
      c.filter = "none";
      this.tintedCopy(c, img, x - S, y, size, palette.role("you"));
      this.tintedCopy(c, img, x + S, y, size, palette.role("them"));
      c.globalAlpha = alpha;
      c.drawImage(img, x, y, size, size);
      // soft body glow so the projection has air around it
      c.globalAlpha = alpha * 0.45; c.imageSmoothingEnabled = true; c.filter = `blur(${S * 5}px)`;
      c.drawImage(img, x, y, size, size);
      c.filter = "none";
      // scanline mask (dark) / halftone banding (light), plus the slow roll
      c.globalCompositeOperation = "destination-out";
      c.globalAlpha = palette.light ? 0.5 : 0.75;
      for (let r = 0; r < size + S * 8; r += S) if (((r / S) | 0) % 2 === 1) c.fillRect(x - S * 8, y - S * 4 + r, size + S * 16, Math.ceil(S / 2) + (palette.light ? 0 : 1));
      const roll = ((this.wall * 22) % (size * 1.6)) - size * 0.3;
      const gr = c.createLinearGradient(0, y + roll - S * 10, 0, y + roll + S * 10);
      gr.addColorStop(0, "rgba(0,0,0,0)"); gr.addColorStop(0.5, "rgba(0,0,0,0.55)"); gr.addColorStop(1, "rgba(0,0,0,0)");
      c.globalAlpha = 1; c.fillStyle = gr;
      c.fillRect(x - S * 8, y + roll - S * 10, size + S * 16, S * 20);
      c.restore();
    }
  }

  private scratch = document.createElement("canvas");
  private tintedCopy(c: CanvasRenderingContext2D, img: HTMLCanvasElement, x: number, y: number, size: number, color: string): void {
    const t = this.scratch; t.width = t.height = SP;
    const x2 = t.getContext("2d")!;
    x2.clearRect(0, 0, SP, SP);
    x2.drawImage(img, 0, 0);
    x2.globalCompositeOperation = "source-in"; x2.fillStyle = color; x2.fillRect(0, 0, SP, SP);
    c.drawImage(t, x, y, size, size);
  }
}
