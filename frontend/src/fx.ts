// Console-level effects: RETUNE, boot, banner, and alert audio (DESIGN §3a, §4).

import { palette } from "./palette";
import type { Theme, Threat } from "./types";

const crt = () => document.getElementById("crt")!;

/** A theme change is an event, not a repaint. */
export function retune(theme: Theme, instant = false): void {
  if (instant || !palette.theme) { palette.apply(theme); return; }
  const el = crt();
  el.classList.remove("retune"); void el.offsetWidth; el.classList.add("retune");
  sting("retune");
  setTimeout(() => palette.apply(theme), 120);
  setTimeout(() => el.classList.remove("retune"), 280);
}

export async function boot(lines: string[]): Promise<void> {
  const el = document.getElementById("boot")!;
  const pre = document.createElement("pre");
  el.appendChild(pre);
  for (const l of lines) {
    pre.textContent += l + "\n";
    await new Promise((r) => setTimeout(r, 110));
  }
  await new Promise((r) => setTimeout(r, 260));
  el.classList.add("off");
  document.getElementById("console")!.classList.add("cold");
  setTimeout(() => el.remove(), 600);
}

// ── banner: alert-and-tap, never seizes the view ────────────────────────────
let bannerTimer = 0;
export function banner(t: Threat, label: string, onTap: () => void): void {
  const el = document.getElementById("banner")!;
  const hurt = t.kind === "hurt";
  el.className = hurt ? "them" : "you";
  if (t.lead_change) el.className = "alert";
  const sign = t.delta >= 0 ? "+" : "";
  el.innerHTML = `${hurt ? "⚠ THREAT" : "▲ ASSIST"} · ${t.name} ${sign}${t.delta.toFixed(1)} · ${t.headline} · ${label}<span class="k">[ENTER] VIEW</span>`;
  if (t.lead_change) el.innerHTML = `◆ LEAD CHANGE · ` + el.innerHTML;
  el.onclick = () => { onTap(); hideBanner(); };
  requestAnimationFrame(() => el.classList.add("on"));
  clearTimeout(bannerTimer);
  bannerTimer = window.setTimeout(hideBanner, 7000);
  sting(t.lead_change ? "lead" : hurt ? "hurt" : "help");
}
export function hideBanner(): void { document.getElementById("banner")!.classList.remove("on"); }
export function bannerVisible(): boolean { return document.getElementById("banner")!.classList.contains("on"); }

// ── audio: alert cues only ──────────────────────────────────────────────────
let ac: AudioContext | null = null;
export let muted = localStorage.getItem("ffb.muted") === "1";
export function toggleMute(): boolean { muted = !muted; localStorage.setItem("ffb.muted", muted ? "1" : "0"); return muted; }

const STINGS: Record<string, [number, number][]> = {   // [semitones from A3, start s]
  help: [[0, 0], [7, 0.08], [12, 0.16]],
  hurt: [[12, 0], [6, 0.09], [1, 0.18]],
  lead: [[0, 0], [12, 0.07], [7, 0.14], [19, 0.21], [24, 0.30]],
  retune: [[-12, 0], [24, 0.05]],
};

export function sting(kind: keyof typeof STINGS): void {
  if (muted) return;
  try {
    ac ??= new AudioContext();
    if (ac.state === "suspended") void ac.resume();
    const t0 = ac.currentTime + 0.01;
    for (const [semi, at] of STINGS[kind]) {
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = kind === "hurt" ? "sawtooth" : "square";
      o.frequency.value = 220 * 2 ** (semi / 12);
      g.gain.setValueAtTime(0, t0 + at);
      g.gain.linearRampToValueAtTime(0.06, t0 + at + 0.008);
      g.gain.exponentialRampToValueAtTime(0.0008, t0 + at + 0.22);
      o.connect(g).connect(ac.destination);
      o.start(t0 + at); o.stop(t0 + at + 0.25);
    }
  } catch { /* audio is garnish */ }
}
