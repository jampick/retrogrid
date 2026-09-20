// Tactical field renderer (DESIGN §5). 240x256 internal buffer, vertical,
// nearest-neighbour upscale, then a light-model pass (bloom | ink bleed).

import { mixHex, palette, rgba } from "./palette";
import { drawText, textWidth } from "./pixelfont";
import type { Actor, Keyframe, PlayFrame, Side } from "./types";

export const BW = 240, BH = 256;
const PPY = 4.5;                    // pixels per yard
const HASH_X = [23.58, 29.75];
const PRE_ROLL = 0.9;               // seconds of set formation before the snap
const BEHIND = 15;                  // yards of field shown behind the LOS

function at(keys: Keyframe[], t: number): { x: number; y: number; z: number } {
  if (t <= keys[0].t) return { x: keys[0].x, y: keys[0].y, z: keys[0].z ?? 0 };
  for (let i = 1; i < keys.length; i++) {
    const b = keys[i];
    if (t <= b.t) {
      const a = keys[i - 1];
      const u = (t - a.t) / Math.max(1e-6, b.t - a.t);
      return { x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u, z: (a.z ?? 0) + ((b.z ?? 0) - (a.z ?? 0)) * u };
    }
  }
  const l = keys[keys.length - 1];
  return { x: l.x, y: l.y, z: l.z ?? 0 };
}

export class Field {
  private buf = document.createElement("canvas");
  private b: CanvasRenderingContext2D;
  private out: CanvasRenderingContext2D;
  private play: PlayFrame | null = null;
  private t = -PRE_ROLL;
  private camY = 20;
  private camTarget = 20;
  private wall = 0;                 // free-running seconds, for blinks
  scale = 3;
  badge = "";                       // "REPLAY 2/5" — anything on screen that is not the live play says so
  glow = true;                      // light-model pass; the contact sheet turns it off for density

  constructor(private canvas: HTMLCanvasElement) {
    this.buf.width = BW; this.buf.height = BH;
    this.b = this.buf.getContext("2d")!;
    this.out = canvas.getContext("2d")!;
  }

  /** Fit the largest integer scale into the host box. */
  fit(w: number, h: number): void {
    this.scale = Math.max(1, Math.floor(Math.min(w / BW, h / BH)));
    this.canvas.width = BW * this.scale; this.canvas.height = BH * this.scale;
    this.canvas.style.width = `${BW * this.scale}px`; this.canvas.style.height = `${BH * this.scale}px`;
  }

  show(play: PlayFrame, settled = false): void {
    this.play = play;
    this.t = settled ? play.duration + 30 : -PRE_ROLL;
    this.camY = this.camTarget = this.baseCam(play);
    if (settled) {                     // land where the live camera would have ended up
      const end = this.t;
      for (this.t = 0; this.t < play.duration; ) this.tick(0.1);
      this.t = end; this.camY = this.camTarget;
    }
  }

  get current(): PlayFrame | null { return this.play; }
  /** Seconds since the snap (negative during the pre-roll). */
  get elapsed(): number { return this.t; }
  seek(t: number): void { this.t = t; }
  get finished(): boolean { return !this.play || this.t >= this.play.duration; }

  private baseCam(p: PlayFrame): number {
    return Math.min(120 - BH / PPY + 1, Math.max(-1, p.los - BEHIND));
  }

  tick(dt: number): void {
    this.wall += dt;
    if (!this.play) return;
    this.t += dt;
    const ball = this.play.actors.find((a) => a.team === "ball");
    if (ball) {
      const p = at(ball.keys, Math.max(0, this.t));
      const top = this.camY + BH / PPY;
      if (p.y > top - 10) this.camTarget = p.y - BH / PPY + 10;
      else if (p.y < this.camY + 6) this.camTarget = p.y - 6;
      this.camTarget = Math.min(120 - BH / PPY + 1, Math.max(-1, this.camTarget));
    }
    this.camY += (this.camTarget - this.camY) * Math.min(1, dt * 3.5);
  }

  // ---- buffer-space helpers -------------------------------------------------
  private sx(x: number): number { return Math.round(x * PPY); }
  private sy(y: number): number { return Math.round(BH - (y - this.camY) * PPY); }

  private px(x: number, y: number, c: string): void {
    this.b.fillStyle = c; this.b.fillRect(x, y, 1, 1);
  }

  private line(x0: number, y0: number, x1: number, y1: number, c: string, dash = 0): void {
    const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0);
    const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy, n = 0;
    this.b.fillStyle = c;
    for (;;) {
      if (!dash || (n++ % (dash * 2)) < dash) this.b.fillRect(x0, y0, 1, 1);
      if (x0 === x1 && y0 === y1) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
  }

  private sideColor(side: Side): string {
    return side === "you" ? palette.role("you") : side === "them" ? palette.role("them") : palette.role("hot");
  }

  // ---- layers ---------------------------------------------------------------
  private drawTurf(): void {
    const grid = palette.role("grid"), dim = palette.role("dim"), bg = palette.role("bg");
    const minor = mixHex(bg, grid, 0.75), major = mixHex(grid, dim, 0.28), num = mixHex(bg, dim, 0.6);
    const y0 = Math.floor(this.camY) - 1, y1 = Math.ceil(this.camY + BH / PPY) + 1;
    for (let y = Math.max(0, y0); y <= Math.min(120, y1); y++) {
      const py = this.sy(y);
      const inField = y >= 10 && y <= 110;
      if (inField && y % 5 === 0) {
        this.line(0, py, BW - 1, py, y % 10 === 0 ? major : minor);
        if (y % 10 === 0 && y > 10 && y < 110) {
          const n = String(50 - Math.abs(60 - y));
          drawText(this.b, n, 10, py - 7, num);
          drawText(this.b, n, BW - 10 - textWidth(n), py - 7, num);
        }
      } else if (inField) {
        for (const hx of HASH_X) { const x = this.sx(hx); this.px(x, py, major); this.px(x + 1, py, major); }
        this.px(1, py, major); this.px(BW - 2, py, major);
      }
    }
    // end zones: hatch + team abbr
    for (const [a, z, key] of [[0, 10, "near"], [110, 120, "far"]] as const) {
      const top = this.sy(z), bot = this.sy(a);
      if (bot < 0 || top > BH) continue;
      this.b.save();
      this.b.beginPath(); this.b.rect(0, top, BW, bot - top); this.b.clip();
      for (let d = -BH; d < BW + BH; d += 6) this.line(d, bot, d + (bot - top), top, minor);
      const label = this.play?.endzones[key] ?? "";
      if (label) drawText(this.b, label, Math.round((BW - textWidth(label) * 4) / 2), Math.round((top + bot) / 2 - 10), major, 4);
      this.b.restore();
      const gl = this.sy(key === "near" ? 10 : 110);
      this.line(0, gl, BW - 1, gl, dim);
    }
    this.line(0, 0, 0, BH - 1, major); this.line(BW - 1, 0, BW - 1, BH - 1, major);
  }

  private drawMarkers(p: PlayFrame): void {
    if (!p.los_line) return;
    this.line(0, this.sy(p.los), BW - 1, this.sy(p.los), palette.role("dim"), 2);
    if (p.to_go != null && p.to_go < 110) this.line(0, this.sy(p.to_go), BW - 1, this.sy(p.to_go), palette.role("alert"), 1);
  }

  private drawTrail(a: Actor, t: number, fade: number): void {
    if (t <= 0 || a.keys.length < 2) return;
    const them = a.side === "them";
    let color: string;
    if (a.team === "ball") color = rgba(palette.role("hot"), 0.55 * fade);
    else if (a.involved) color = rgba(this.sideColor(a.side), 0.9 * fade);
    else color = rgba(mixHex(palette.role("grid"), palette.role("dim"), 0.35), 0.4 * fade);
    const dash = a.team === "ball" ? 1 : a.involved && them && palette.mono ? 2 : 0;
    let prev = at(a.keys, 0);
    const pts = a.keys.filter((k) => k.t > 0 && k.t < t).map((k) => ({ x: k.x, y: k.y, z: k.z ?? 0 }));
    pts.push(at(a.keys, t));
    for (const q of pts) {
      this.line(this.sx(prev.x), this.sy(prev.y) - Math.round(prev.z * PPY * 0.5),
                this.sx(q.x), this.sy(q.y) - Math.round(q.z * PPY * 0.5), color, dash);
      prev = q;
    }
  }

  private drawActor(a: Actor, t: number): { x: number; y: number } {
    const p = at(a.keys, t);
    const x = this.sx(p.x), y = this.sy(p.y);
    const b = this.b;
    if (a.team === "flag") { b.fillStyle = palette.role("alert"); b.fillRect(x - 1, y - 1, 3, 3); return { x, y }; }
    if (a.team === "ball") {
      const lift = Math.round(p.z * PPY * 0.5);
      if (lift > 0) { b.fillStyle = rgba(palette.role("dim"), 0.6); b.fillRect(x, y, 2, 1); }
      b.fillStyle = palette.role("hot"); b.fillRect(x - 1, y - 1 - lift, 2, 2);
      return { x, y };
    }
    const off = mixHex(palette.role("grid"), palette.role("dim"), 0.75);
    if (!a.involved) {
      b.fillStyle = off;
      if (a.team === "off") {            // O
        b.fillRect(x - 1, y - 2, 3, 1); b.fillRect(x - 1, y + 2, 3, 1);
        b.fillRect(x - 2, y - 1, 1, 3); b.fillRect(x + 2, y - 1, 1, 3);
      } else {                           // X
        for (let i = -2; i <= 2; i++) { b.fillRect(x + i, y + i, 1, 1); b.fillRect(x + i, y - i, 1, 1); }
      }
      return { x, y };
    }
    const c = this.sideColor(a.side);
    const hollow = palette.mono && a.side === "them";
    if (a.team === "off") {
      b.fillStyle = c;
      b.fillRect(x - 1, y - 3, 3, 7); b.fillRect(x - 3, y - 1, 7, 3); b.fillRect(x - 2, y - 2, 5, 5);
      b.fillStyle = hollow ? palette.role("bg") : palette.role("hot");
      if (hollow) b.fillRect(x - 1, y - 1, 3, 3); else b.fillRect(x, y, 1, 1);
    } else {
      b.fillStyle = c;
      for (let i = -3; i <= 3; i++) { b.fillRect(x + i, y + i, 1, 1); b.fillRect(x + i, y - i, 1, 1); b.fillRect(x + i + 1, y + i, 1, 1); b.fillRect(x + i + 1, y - i, 1, 1); }
    }
    return { x, y };
  }

  private drawTags(tags: { a: Actor; x: number; y: number }[]): void {
    const placed: { x: number; y: number; w: number }[] = [];
    for (const { a, x, y } of tags.sort((m, n) => m.y - n.y)) {
      const text = a.label!;
      const w = textWidth(text) + 4;
      let tx = x + 7, ty = y - 9;
      if (tx + w > BW - 2) tx = x - 7 - w;
      for (const p of placed) if (Math.abs(p.y - ty) < 8 && tx < p.x + p.w && p.x < tx + w) ty = p.y + 8;
      ty = Math.min(BH - 8, Math.max(2, ty));
      placed.push({ x: tx, y: ty, w });
      const c = this.sideColor(a.side);
      this.b.fillStyle = rgba(palette.role("bg"), 0.8); this.b.fillRect(tx - 1, ty - 1, w, 7);
      this.b.fillStyle = c; this.b.fillRect(tx - 1, ty - 1, 1, 7);
      drawText(this.b, text, tx + 2, ty, c);
      this.px(tx > x ? x + 4 : x - 4, y - 3, rgba(c, 0.7)); this.px(tx > x ? x + 5 : x - 5, y - 4, rgba(c, 0.7));
    }
  }

  private drawHud(p: PlayFrame | null): void {
    const dim = palette.role("dim"), hot = palette.role("hot");
    if (!p) {
      if ((this.wall * 1.2) % 1 < 0.6) {
        const s = "AWAITING FEED";
        drawText(this.b, s, Math.round((BW - textWidth(s)) / 2), 120, dim);
      }
      return;
    }
    this.b.fillStyle = rgba(palette.role("bg"), 0.75); this.b.fillRect(0, 0, BW, 9);
    drawText(this.b, p.label, 3, 2, hot);
    drawText(this.b, p.situation, BW - 3 - textWidth(p.situation), 2, dim);
    if (this.badge && (this.wall * 1.5) % 1 < 0.75) {
      const w = textWidth(this.badge);
      this.b.fillStyle = rgba(palette.role("bg"), 0.8); this.b.fillRect(BW - w - 7, 11, w + 6, 9);
      drawText(this.b, this.badge, BW - w - 4, 13, palette.role("alert"));
    }
    if (this.t >= p.duration && p.result) {
      const since = this.t - p.duration;
      const blink = since > 2.4 || (since * 5) % 1 < 0.6;
      if (blink) {
        const big = p.result.length <= 12 ? 2 : 1;
        const w = textWidth(p.result) * big;
        const c = p.result_kind === "neutral" ? hot : palette.role(p.result_kind === "good" ? "gain" : "alert");
        const y = BH - 16 - 5 * big;
        this.b.fillStyle = rgba(palette.role("bg"), 0.8);
        this.b.fillRect(Math.round((BW - w) / 2) - 3, y - 3, w + 6, 5 * big + 6);
        drawText(this.b, p.result, Math.round((BW - w) / 2), y, c, big);
      }
    }
  }

  draw(): void {
    const b = this.b, p = this.play;
    b.clearRect(0, 0, BW, BH);
    this.drawTurf();
    if (p) {
      this.drawMarkers(p);
      const t = Math.max(0, this.t);
      const fade = this.t < p.duration ? 1 : Math.max(0.28, 1 - (this.t - p.duration) / 25);
      for (const a of p.actors) if (!a.involved && a.team !== "ball" && a.team !== "flag") this.drawTrail(a, t, fade);
      for (const a of p.actors) if (a.involved || a.team === "ball") this.drawTrail(a, t, fade);
      const tags: { a: Actor; x: number; y: number }[] = [];
      const order = [...p.actors].sort((m, n) => Number(m.involved) - Number(n.involved) || (m.team === "ball" ? 1 : 0) - (n.team === "ball" ? 1 : 0));
      const set = this.t < 0 && (this.wall * 6) % 1 < 0.5;
      for (const a of order) {
        if ((a.team === "ball" || a.team === "flag") && this.t < 0) continue;
        if (a.team === "flag" && t < a.keys[0].t) continue;
        const pos = this.drawActor(a, t);
        if (a.involved && a.label && !(set && false)) tags.push({ a, ...pos });
      }
      this.drawTags(tags);
    }
    this.drawHud(p);
    this.composite();
  }

  private composite(): void {
    const o = this.out, s = this.scale, w = BW * s, h = BH * s;
    o.clearRect(0, 0, w, h);
    o.imageSmoothingEnabled = false;
    o.globalCompositeOperation = "source-over"; o.globalAlpha = 1; o.filter = "none";
    o.drawImage(this.buf, 0, 0, w, h);
    o.imageSmoothingEnabled = true;
    if (!this.glow) return;
    if (palette.light) {                       // PRINTOUT: ink bleed
      o.globalCompositeOperation = "multiply";
      o.filter = `blur(${Math.max(1, s * 0.5)}px)`; o.globalAlpha = 0.55;
      o.drawImage(this.buf, 0, 0, w, h);
    } else {                                   // PHOSPHOR: two-radius bloom
      o.globalCompositeOperation = "lighter";
      o.filter = `blur(${s * 1.3}px)`; o.globalAlpha = 0.7;
      o.drawImage(this.buf, 0, 0, w, h);
      o.filter = `blur(${s * 5}px)`; o.globalAlpha = 0.3;
      o.drawImage(this.buf, 0, 0, w, h);
    }
    o.filter = "none"; o.globalAlpha = 1; o.globalCompositeOperation = "source-over";
  }
}
