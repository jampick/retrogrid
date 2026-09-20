import { Field } from "./field";
import { banner, bannerVisible, boot, hideBanner, retune, sting, toggleMute } from "./fx";
import { Ghosts } from "./ghosts";
import { palette } from "./palette";
import { Radio } from "./radio";
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


// Dead time between snaps re-runs the play just seen — that one only, and it says REPLAY.
// Older plays are a deliberate act ([ and ]). AUTO-REPLAY off = the finished diagram just sits there.
const HISTORY = 8, LINGER_LIVE = 6, LINGER_REPLAY = 10;
const recent: PlayFrame[] = [];
let cycle = localStorage.getItem("ffb.cycle") !== "0";
let replayAt = -1;                      // index into recent; -1 = showing live
let idle = 0;
let redzone = (q.get("rz") ?? localStorage.getItem("ffb.redzone")) === "1";

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
  auto: () => send({ type: "auto" }),
  redzone: () => setRedzone(!state?.clock.redzone),
  cycle: () => toggleCycle(),
});

const radio = new Radio();
radio.onchange = () => ui.renderRadio(radio, state);
let favs: string[] = JSON.parse(localStorage.getItem("ffb.favs") ?? "[]");

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
let pickerMulti = false;                 // multi: [ENTER]/click toggles a row, [ESC] closes

function openPicker(title: string, items: Item[], pick: (k: string) => void, multi = false): void {
  pickerItems = items; pickerPick = pick; pickerMulti = multi;
  pickerIdx = Math.max(0, items.findIndex((i) => i.current));
  const el = $("picker"); el.hidden = false;
  el.innerHTML = `<h2><span>${title}</span><em>[ESC]</em></h2><ul>` + items.map((it, i) =>
    `<li data-i="${i}" class="${it.current ? "cur" : ""}"><span>${it.label}</span>${it.roles
      ? `<span class="sw">${(["bg", "grid", "dim", "you", "them", "alert", "gain", "hot"] as const).map((r) => `<i style="background:${it.roles![r]}"></i>`).join("")}</span>` : ""}</li>`).join("") + "</ul>";
  el.querySelectorAll("li").forEach((li) => {
    li.onmouseenter = () => movePicker(Number(li.dataset.i) - pickerIdx);
    li.onclick = () => pickRow(Number(li.dataset.i));
  });
  movePicker(0);
}
function movePicker(d: number): void {
  pickerIdx = (pickerIdx + d + pickerItems.length) % pickerItems.length;
  $("picker").querySelectorAll("li").forEach((li, i) => li.classList.toggle("sel", i === pickerIdx));
  $("picker").querySelectorAll("li")[pickerIdx]?.scrollIntoView({ block: "nearest" });
}
function closePicker(): void { $("picker").hidden = true; }
function pickRow(i: number): void {
  const it = pickerItems[i];
  pickerPick(it.key);
  if (!pickerMulti) { closePicker(); return; }
  it.current = !it.current;
  $("picker").querySelectorAll("li")[i].classList.toggle("cur", it.current);
}
const pickerOpen = () => !$("picker").hidden;

function themePicker(): void {
  const items: Item[] = [];
  if (systemTheme) items.push({ key: "system", label: `FOLLOW SYSTEM · ${systemTheme.name}`, roles: systemTheme.roles, current: choice === "system" });
  for (const t of themes) items.push({ key: t.slug, label: t.name + (t.mode === "light" ? " ◻" : ""), roles: t.roles, current: choice === t.slug });
  openPicker("THEME", items, choose);
}
function viewerPicker(): void {
  if (!state) return;
  openPicker("WHO AM I", state.viewers.map((v) => ({ key: v.team_key, label: `${v.owner} · ${v.name}`, current: v.team_key === state!.viewer?.team_key })),
    (k) => { localStorage.setItem("ffb.viewer", k); send({ type: "viewer", team_key: k }); });
}

function favPicker(): void {
  if (!state) return;
  openPicker("FOLLOW TEAMS", state.teams.map((t) => ({ key: t, label: t, current: favs.includes(t) })), (t) => {
    favs = favs.includes(t) ? favs.filter((x) => x !== t) : [...favs, t];
    localStorage.setItem("ffb.favs", JSON.stringify(favs));
    send({ type: "favs", teams: favs });
  }, true);
}

// ── live + replay ────────────────────────────────────────────────────────────
const DULL = /\/(flag|kneel|spike|hold)$|^fallback$/;

function present(f: PlayFrame): void {
  if (f.settled) recent.length = 0;                            // focus moved: old game's plays are stale
  const dup = recent.findIndex((r) => r.play_id === f.play_id);
  if (dup >= 0) recent.splice(dup, 1);
  recent.push(f); while (recent.length > HISTORY) recent.shift();
  replayAt = -1; idle = 0;
  // a catch-up frame is the last thing that happened, already over — not live, and it says so
  field.badge = f.settled ? "LAST PLAY" : "LIVE"; field.badgeKind = f.settled ? "last" : "live";
  field.show(f, f.settled);
}

/** step 0 = run the latest play again (the auto-replay); ±1 = walk the history by hand. */
function replay(step: number): void {
  if (!recent.length) return;
  const last = recent.length - 1;
  const i = step === 0 ? last : Math.min(last, Math.max(0, (replayAt < 0 ? last : replayAt) + step));
  if (step === 0 && DULL.test(recent[i].template)) { idle = 0; return; }      // nobody re-watches a kneel
  replayAt = i; idle = 0;
  field.badge = i === last ? "REPLAY" : `REPLAY -${last - i}`; field.badgeKind = "replay";
  field.show(recent[i]);
}

function backToLive(): void {
  if (replayAt < 0 || !recent.length) return;
  replayAt = -1; idle = 0; field.badge = "LAST PLAY"; field.badgeKind = "last";
  field.show(recent[recent.length - 1], true);
}

function setRedzone(on: boolean): void {
  redzone = on; localStorage.setItem("ffb.redzone", on ? "1" : "0");
  send({ type: "redzone", on });
}

function toggleCycle(): void { cycle = !cycle; localStorage.setItem("ffb.cycle", cycle ? "1" : "0"); cycleLabel(); if (!cycle) backToLive(); }
function cycleLabel(): void { ui.cycleText = cycle ? "ON" : "OFF"; const el = document.getElementById("cycle"); if (el) el.textContent = ui.cycleText; }

// ── frames ───────────────────────────────────────────────────────────────────
function onFrame(f: Frame): void {
  switch (f.type) {
    case "hello":
      themes = f.themes; systemTheme = f.system;
      applyChoice(!palette.theme);
      { const v = q.get("viewer") ?? localStorage.getItem("ffb.viewer"); if (v) send({ type: "viewer", team_key: v }); }
      { const f = q.get("favs"); if (f) favs = f.toUpperCase().split(","); if (favs.length) send({ type: "favs", teams: favs }); }
      if (redzone) send({ type: "redzone", on: true });
      { const x = q.get("ffb") ?? localStorage.getItem("ffb.layer"); if (x !== null) send({ type: "ffb", on: x === "1" }); }
      break;
    case "theme":
      systemTheme = f.system;
      if (choice === "system") applyChoice();
      break;
    case "state":
      // an older server (no NFL layer) sends none of these: it is the fantasy console, nothing more
      state = { ...f, mode: f.mode ?? "ffb", ffb_available: f.ffb_available ?? true, favs: f.favs ?? [], teams: f.teams ?? [],
                chatter: f.chatter ?? [], audio: f.audio ?? null };
      ui.render(state); ui.renderRadio(radio, state);
      ghosts.set("you", f.ghosts.you); ghosts.set("them", f.ghosts.them);
      break;
    case "play":
      if (!f.focus) break;
      if (f.settled || f.alert) { queue.length = 0; present(f); }     // catch-up, tapped or auto-directed
      else if (field.finished || replayAt >= 0) present(f);           // live always pre-empts a replay
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
  for (const side of ["you", "them"] as const) {             // name plate rides under its hologram
    const tag = $(`gt-${side}`);
    tag.style.left = `${ghosts.anchors[side].x - stage.left}px`;
    tag.style.top = `${stage.height * 0.5 + 48 * ghosts.scale + 10}px`;
  }
}

let last = performance.now();
function frame(now: number): void {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  requestAnimationFrame(frame);
  if (!palette.theme) return;                 // nothing may draw before a palette exists
  if (field.finished && queue.length) present(queue.shift()!);
  else if (field.finished && cycle && !pickerOpen()) {
    idle += dt;
    if (idle > (replayAt < 0 ? LINGER_LIVE : LINGER_REPLAY)) replay(0);
  }
  field.tick(dt); ghosts.tick(dt);
  field.draw(); ghosts.draw();
}

window.addEventListener("resize", layout);
window.addEventListener("keydown", (e) => {
  if (pickerOpen()) {
    if (e.key === "Escape") closePicker();
    else if (e.key === "ArrowDown" || e.key === "j") movePicker(1);
    else if (e.key === "ArrowUp" || e.key === "k") movePicker(-1);
    else if (e.key === "Enter" || e.key === " ") pickRow(pickerIdx);
    e.preventDefault(); return;
  }
  const k = e.key.toLowerCase();
  if (k === "t") themePicker();
  else if (k === "f") favPicker();
  else if (k === "v" && state?.mode === "ffb") viewerPicker();
  else if (k === "x" && state?.ffb_available) { const on = state.mode !== "ffb"; localStorage.setItem("ffb.layer", on ? "1" : "0"); send({ type: "ffb", on }); }
  else if (k === "tab") { e.preventDefault(); if (state?.mode === "ffb") { ui.rail = ui.rail === "lineup" ? "chatter" : "lineup"; localStorage.setItem("ffb.rail", ui.rail); ui.render(state); } }
  else if (k === "m") radio.toggle(state?.audio ?? null);
  else if (k === "h") radio.swap();
  else if (k === "-" || k === "=") radio.nudge(k === "=" ? 0.1 : -0.1);
  else if (k === "l" && state?.mode === "ffb") send({ type: "scope", scope: state?.scope === "matchup" ? "league" : "matchup" });
  else if (k === " ") { send({ type: "sim", action: "pause" }); e.preventDefault(); }
  else if ("1234".includes(k)) send({ type: "sim", action: "speed", value: [1, 4, 15, 60][Number(k) - 1] });
  else if (k === "arrowright") send({ type: "sim", action: "skip", value: 300 });
  else if (k === "arrowleft") send({ type: "sim", action: "skip", value: -300 });
  else if (k === "a") send({ type: "auto" });
  else if (k === "r") setRedzone(!state?.clock.redzone);
  else if (k === "c") toggleCycle();
  else if (k === "[" || k === ",") replay(-1);
  else if (k === "]" || k === ".") replay(1);
  else if (k === "\\" || k === "/") backToLive();
  else if (k === "n") sting(toggleMute() ? "hurt" : "help");
  else if (k === "enter" && bannerVisible() && pendingThreat) { send({ type: "focus_play", play_id: pendingThreat.play_id }); hideBanner(); }
  else if (k === "escape") hideBanner();
});
$("clockbox").addEventListener("dblclick", themePicker);

palette.subscribe(layout);
cycleLabel();
connect();
layout();
requestAnimationFrame(frame);
if (!MINI) void boot(["RETRO//GRID  TACTICAL GAMEDAY CONSOLE", "PHOSPHOR ............ OK", "FEED UPLINK ......... OK", "CROWD TAP ........... OK", "ACTION MATRIX ....... ARMED"]);
else $("boot").remove();
