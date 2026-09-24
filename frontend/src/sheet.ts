// Grammar contact sheet (DESIGN §8 Validation): N seeded-random plays, compiled
// and looping side by side. Human eyeball is the metric; [F] flags a bad one
// into data/grammar_flags.json so the next grammar pass has a worklist.

import { Field } from "./field";
import { palette } from "./palette";
import type { PlayFrame, Theme } from "./types";

type SheetPlay = PlayFrame & { family: string };
interface Sample { plays: SheetPlay[]; families: Record<string, number>; pool: number }
interface Cell { play: SheetPlay; field: Field; el: HTMLElement; visible: boolean }

const q = new URLSearchParams(location.search);
const $ = (id: string) => document.getElementById(id)!;
const HOLD = 1.6;                       // seconds the finished diagram lingers before the loop

let family = q.get("family") ?? "all";
let seed = Number(q.get("seed") ?? 0);
let n = Number(q.get("n") ?? 28);
let speed = Number(q.get("speed") ?? 1);
let settled = q.get("settled") === "1";
const grep = q.get("grep") ?? "";          // narrow the pool to descriptions containing this text
const reel = Number(q.get("reel") ?? 0);   // 1: the reel's picks for the newest cached week, in rank order. N>1: week N
let paused = false;
let cells: Cell[] = [];
let zoom: Cell | null = null;
let flags: Record<string, { template: string; note: string }> = {};

const seen = new IntersectionObserver((es) => {
  for (const e of es) { const c = cells.find((k) => k.el === e.target); if (c) c.visible = e.isIntersecting; }
}, { rootMargin: "200px" });

function url(): void {
  const p = new URLSearchParams({ family, seed: String(seed), n: String(n), speed: String(speed), settled: settled ? "1" : "0" });
  if (grep) p.set("grep", grep);
  if (reel) p.set("reel", String(reel));
  history.replaceState(null, "", `?${p}`);
}

function esc(s: string): string { return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!)); }

function caption(p: SheetPlay): string {
  if (p.reel) {
    const wpa = p.reel.wpa === null ? "no wpa" : `wpa ${(p.reel.wpa * 100).toFixed(0)}%`;
    return `<b>${esc(p.reel.number)} · ${p.reel.score.toFixed(1)} · ${esc(p.reel.tag)}</b><span>${esc(p.reel.headline)}</span><p>${esc(p.label)} · ${esc(p.situation)} · ${wpa}<br>${esc(p.desc)}</p>`;
  }
  return `<b>${esc(p.template)}</b><span>${esc(p.result)}</span><p>${esc(p.desc)}</p>`;
}

function mark(c: Cell): void {
  const f = flags[c.play.play_id];
  c.el.classList.toggle("flagged", !!f);
  c.el.querySelector("i")!.textContent = f ? `⚑ ${f.note}` : "";
}

function start(c: Cell): void {
  c.field.show(c.play, settled);
}

async function load(): Promise<void> {
  url();
  const only = q.get("play");
  const r = await fetch(`/api/plays/sample?n=${n}&seed=${seed}&family=${family}` + (only ? `&play=${encodeURIComponent(only)}` : "")
    + (grep ? `&grep=${encodeURIComponent(grep)}` : "") + (reel ? `&reel=${reel}` : ""));
  const data: Sample = await r.json();
  flags = await (await fetch("/api/plays/flags")).json();
  const total = Object.entries(data.families).filter(([k]) => k !== "td").reduce((a, [, v]) => a + v, 0);
  $("families").innerHTML = [["all", total] as const, ...Object.entries(data.families)].map(([k, v]) =>
    `<button data-f="${k}" class="${k === family ? "on" : ""}">${k}<em>${v}</em></button>`).join("");
  $("families").querySelectorAll("button").forEach((b) => b.onclick = () => { family = b.dataset.f!; seed = 0; void load(); });
  for (const c of cells) seen.unobserve(c.el);
  const grid = $("grid"); grid.innerHTML = "";
  cells = data.plays.map((play) => {
    const el = document.createElement("figure");
    el.innerHTML = `<canvas></canvas><figcaption>${caption(play)}<i></i></figcaption>`;
    grid.appendChild(el);
    const field = new Field(el.querySelector("canvas")!);
    field.glow = false; field.fit(240, 256);
    const c: Cell = { play, field, el, visible: true };
    el.onclick = () => openZoom(c);
    seen.observe(el); start(c); mark(c);
    return c;
  });
  status();
}

function status(): void {
  $("status").textContent = `seed ${seed} · ${cells.length} plays · ${speed}x · ${settled ? "DIAGRAM" : paused ? "PAUSED" : "LOOP"} · ${Object.keys(flags).length} flagged`;
}

// ── zoom ─────────────────────────────────────────────────────────────────────
let zfield: Field | null = null;
function openZoom(c: Cell): void {
  zoom = c;
  const z = $("zoom"); z.hidden = false;
  zfield = new Field($("zcanvas") as HTMLCanvasElement);
  zfield.fit(innerWidth * 0.5, innerHeight - 40);
  zfield.show(c.play, settled);
  zinfo();
}
function zinfo(): void {
  if (!zoom) return;
  const p = zoom.play, f = flags[p.play_id];
  const inv = p.actors.filter((a) => a.involved).map((a) => `${a.role}${a.label ? " " + a.label : ""}`).join(" · ");
  $("zinfo").innerHTML = `<h1>${esc(p.template)}</h1><h2>${esc(p.label)} · ${esc(p.situation)} · ${esc(p.result)}</h2>
    <p>${esc(p.desc)}</p><p class="k">${esc(inv)}</p><p class="k">${esc(p.play_id)} · ${p.duration}s · ${p.actors.length} actors · ${p.family}</p>
    <p class="${f ? "flag" : "k"}">${f ? "⚑ FLAGGED: " + esc(f.note || "(no note)") : ""}</p>
    <p class="keys">[F] flag/unflag · [N] flag with note · [←/→] prev/next · [,/.] step · [SPACE] pause · [ESC] close</p>`;
}
function closeZoom(): void { zoom = null; zfield = null; $("zoom").hidden = true; }
function stepZoom(d: number): void {
  if (!zoom) return;
  const i = cells.indexOf(zoom);
  openZoom(cells[(i + d + cells.length) % cells.length]);
}

async function flag(c: Cell, note: string | null): Promise<void> {
  const on = note !== null || !flags[c.play.play_id];
  flags = await (await fetch("/api/plays/flag", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ play_id: c.play.play_id, template: c.play.template, desc: c.play.desc, note: note ?? "", on }),
  })).json();
  mark(c); zinfo(); status();
}

function noteBox(c: Cell): void {
  const box = $("note") as HTMLInputElement;
  box.hidden = false; box.value = flags[c.play.play_id]?.note ?? ""; box.focus();
  box.onkeydown = (e) => {
    e.stopPropagation();
    if (e.key === "Enter") { void flag(c, box.value.trim()); box.hidden = true; }
    if (e.key === "Escape") box.hidden = true;
  };
}

addEventListener("keydown", (e) => {
  const k = e.key.toLowerCase();
  if (k === "escape") closeZoom();
  else if (k === "r") { seed = e.shiftKey ? Math.max(0, seed - 1) : seed + 1; void load(); }
  else if (k === "s") { settled = !settled; cells.forEach(start); if (zoom) zfield?.show(zoom.play, settled); url(); status(); }
  else if (k === " ") { paused = !paused; e.preventDefault(); status(); }
  else if (k === "1" || k === "2" || k === "3") { speed = { "1": 0.5, "2": 1, "3": 2 }[k]!; url(); status(); }
  else if (zoom && k === "f") void flag(zoom, null);
  else if (zoom && k === "n") { e.preventDefault(); noteBox(zoom); }
  else if (zoom && k === "arrowright") stepZoom(1);
  else if (zoom && k === "arrowleft") stepZoom(-1);
  else if (zoom && zfield && (k === "," || k === ".")) { paused = true; zfield.seek(Math.max(0, zfield.elapsed) + (k === "." ? 0.1 : -0.1)); zfield.tick(0); status(); }
});

function loopField(f: Field, play: PlayFrame, dt: number): void {
  if (!paused && !settled) {
    f.tick(dt);
    if (f.elapsed > play.duration + HOLD) f.show(play);
  } else f.tick(0);
  f.draw();
}

let last = performance.now();
function frame(now: number): void {
  const dt = Math.min(0.1, (now - last) / 1000) * speed; last = now;
  if (zoom && zfield) loopField(zfield, zoom.play, dt);
  else for (const c of cells) if (c.visible) loopField(c.field, c.play, dt);
  requestAnimationFrame(frame);
}

(async () => {
  const { system, themes }: { system: Theme | null; themes: Theme[] } = await (await fetch("/api/themes")).json();
  const want = q.get("theme");
  palette.apply(themes.find((t) => t.slug === want) ?? system ?? themes.find((t) => t.slug === "neon") ?? themes[0]);
  await load();
  requestAnimationFrame(frame);
})();
