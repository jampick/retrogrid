import { Field } from "./field";
import { banner, bannerVisible, boot, hideBanner, retune, sting, toggleMute } from "./fx";
import { Ghosts } from "./ghosts";
import { palette } from "./palette";
import type { ConsoleState, Frame, PlayFrame, Theme, Threat } from "./types";
import { Ui } from "./ui";

const q = new URLSearchParams(location.search);
const MINI = q.get("mini") === "1";
const $ = (id: string) => document.getElementById(id)!;
if (MINI) document.documentElement.dataset.mini = "1";

let ws: WebSocket;
let themes: Theme[] = [];
let systemTheme: Theme | null = null;
let choice = q.get("theme") ?? localStorage.getItem("ffb.theme") ?? "system";
let state: ConsoleState | null = null;
let pendingThreat: Threat | null = null;
const queue: PlayFrame[] = [];

const send = (m: object) => ws?.readyState === 1 && ws.send(JSON.stringify(m));

const field = new Field($("field") as HTMLCanvasElement);
const ghosts = new Ghosts($("ghosts") as HTMLCanvasElement);
const ui = new Ui({
  focusGame: (id) => send({ type: "focus_game", game_id: id }),
  focusPlay: (id) => send({ type: "focus_play", play_id: id }),
  toggleScope: () => send({ type: "scope", scope: state?.scope === "matchup" ? "league" : "matchup" }),
  speed: (n) => send({ type: "sim", action: "speed", value: n }),
  pause: () => send({ type: "sim", action: "pause" }),
  seek: (f) => send({ type: "sim", action: "seek", value: f }),
});

// ── theme ────────────────────────────────────────────────────────────────────
function resolveTheme(): Theme {
  if (choice === "system" && systemTheme) return systemTheme;
  return themes.find((t) => t.slug === choice) ?? systemTheme ?? themes.find((t) => t.slug === "neon")!;
}
function applyChoice(instant = false): void {
  const t = resolveTheme();
  if (palette.theme && palette.theme.slug === t.slug && JSON.stringify(palette.theme.roles) === JSON.stringify(t.roles)) return;
  retune(t, instant);
}
function choose(slug: string): void {
  choice = slug;
  if (!q.get("theme")) localStorage.setItem("ffb.theme", slug);
  applyChoice();
}

// ── pickers ──────────────────────────────────────────────────────────────────
type Item = { key: string; label: string; roles?: Theme["roles"]; current: boolean };
let pickerItems: Item[] = [], pickerIdx = 0, pickerPick: (k: string) => void = () => {};

function openPicker(title: string, items: Item[], pick: (k: string) => void): void {
  pickerItems = items; pickerPick = pick;
  pickerIdx = Math.max(0, items.findIndex((i) => i.current));
  const el = $("picker"); el.hidden = false;
  el.innerHTML = `<h2><span>${title}</span><em>[ESC]</em></h2><ul>` + items.map((it, i) =>
    `<li data-i="${i}" class="${it.current ? "cur" : ""}"><span>${it.label}</span>${it.roles
      ? `<span class="sw">${(["bg", "grid", "dim", "you", "them", "alert", "gain", "hot"] as const).map((r) => `<i style="background:${it.roles![r]}"></i>`).join("")}</span>` : ""}</li>`).join("") + "</ul>";
  el.querySelectorAll("li").forEach((li) => {
    li.onmouseenter = () => movePicker(Number(li.dataset.i) - pickerIdx);
    li.onclick = () => { pickerPick(pickerItems[Number(li.dataset.i)].key); closePicker(); };
  });
  movePicker(0);
}
function movePicker(d: number): void {
  pickerIdx = (pickerIdx + d + pickerItems.length) % pickerItems.length;
  $("picker").querySelectorAll("li").forEach((li, i) => li.classList.toggle("sel", i === pickerIdx));
  $("picker").querySelectorAll("li")[pickerIdx]?.scrollIntoView({ block: "nearest" });
}
function closePicker(): void { $("picker").hidden = true; }
const pickerOpen = () => !$("picker").hidden;

function themePicker(): void {
  const items: Item[] = [];
  if (systemTheme) items.push({ key: "system", label: `FOLLOW SYSTEM · ${systemTheme.name}`, roles: systemTheme.roles, current: choice === "system" });
  for (const t of themes) items.push({ key: t.slug, label: t.name + (t.mode === "light" ? " ◻" : ""), roles: t.roles, current: choice === t.slug });
  openPicker("THEME", items, choose);
}
function viewerPicker(): void {
  if (!state) return;
  openPicker("WHO AM I", state.viewers.map((v) => ({ key: v.team_key, label: `${v.owner} · ${v.name}`, current: v.team_key === state!.viewer.team_key })),
    (k) => { localStorage.setItem("ffb.viewer", k); send({ type: "viewer", team_key: k }); });
}

// ── frames ───────────────────────────────────────────────────────────────────
function onFrame(f: Frame): void {
  switch (f.type) {
    case "hello":
      themes = f.themes; systemTheme = f.system;
      applyChoice(!palette.theme);
      { const v = q.get("viewer") ?? localStorage.getItem("ffb.viewer"); if (v) send({ type: "viewer", team_key: v }); }
      break;
    case "theme":
      systemTheme = f.system;
      if (choice === "system") applyChoice();
      break;
    case "state":
      state = f;
      ui.render(f);
      ghosts.set("you", f.ghosts.you); ghosts.set("them", f.ghosts.them);
      break;
    case "play":
      if (!f.focus) break;
      if (f.settled) { queue.length = 0; field.show(f, true); }
      else if (f.alert) { queue.length = 0; field.show(f); }          // tapped or auto-directed
      else if (field.finished) field.show(f);
      else { queue.push(f); while (queue.length > 2) queue.shift(); }
      break;
    case "banner":
      pendingThreat = f.threat;
      banner(f.threat, state?.feeds.find((g) => g.game_id === f.threat.game_id)?.label ?? "", () => send({ type: "focus_play", play_id: f.threat.play_id }));
      break;
  }
}

function connect(): void {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = (e) => onFrame(JSON.parse(e.data));
  ws.onclose = () => setTimeout(connect, 1500);
}

// ── layout + loop ────────────────────────────────────────────────────────────
function layout(): void {
  const stage = $("stage").getBoundingClientRect();
  field.fit(stage.width - 40, stage.height - 16);
  ghosts.resize(window.innerWidth, window.innerHeight);
  const fw = 240 * field.scale;
  const margin = (stage.width - fw) / 2;
  ghosts.scale = Math.max(2, Math.min(5, Math.round(field.scale * 1.15)));
  const cy = stage.top + stage.height * 0.5;
  ghosts.anchors.you = { x: stage.left + margin * 0.42, y: cy };
  ghosts.anchors.them = { x: stage.right - margin * 0.42, y: cy };
}

let last = performance.now();
function frame(now: number): void {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  requestAnimationFrame(frame);
  if (!palette.theme) return;                 // nothing may draw before a palette exists
  if (field.finished && queue.length) field.show(queue.shift()!);
  field.tick(dt); ghosts.tick(dt);
  field.draw(); ghosts.draw();
}

window.addEventListener("resize", layout);
window.addEventListener("keydown", (e) => {
  if (pickerOpen()) {
    if (e.key === "Escape") closePicker();
    else if (e.key === "ArrowDown" || e.key === "j") movePicker(1);
    else if (e.key === "ArrowUp" || e.key === "k") movePicker(-1);
    else if (e.key === "Enter") { pickerPick(pickerItems[pickerIdx].key); closePicker(); }
    e.preventDefault(); return;
  }
  const k = e.key.toLowerCase();
  if (k === "t") themePicker();
  else if (k === "v") viewerPicker();
  else if (k === "l") send({ type: "scope", scope: state?.scope === "matchup" ? "league" : "matchup" });
  else if (k === " ") { send({ type: "sim", action: "pause" }); e.preventDefault(); }
  else if ("1234".includes(k)) send({ type: "sim", action: "speed", value: [1, 4, 15, 60][Number(k) - 1] });
  else if (k === "arrowright") send({ type: "sim", action: "skip", value: 300 });
  else if (k === "arrowleft") send({ type: "sim", action: "skip", value: -300 });
  else if (k === "a") send({ type: "auto" });
  else if (k === "m") sting(toggleMute() ? "hurt" : "help");
  else if (k === "enter" && bannerVisible() && pendingThreat) { send({ type: "focus_play", play_id: pendingThreat.play_id }); hideBanner(); }
  else if (k === "escape") hideBanner();
});
$("clockbox").addEventListener("dblclick", themePicker);

palette.subscribe(layout);
connect();
layout();
requestAnimationFrame(frame);
if (!MINI) void boot(["RETRO//FFB  TACTICAL FANTASY CONSOLE", "PHOSPHOR ............ OK", "FEED UPLINK ......... OK", "THREAT MATRIX ....... ARMED", "OPERATOR ............ RECOGNISED"]);
else $("boot").remove();
