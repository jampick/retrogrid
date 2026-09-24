// DOM rails and cards. No frames anywhere: a "card" is type floating in light.

import { palette } from "./palette";
import { PixelLabel } from "./pixelfont";
import { loadSprite, proceduralBust, tint, SP } from "./sprites";
import type { Radio } from "./radio";
import type { ActiveCard, ConsoleState, GhostInfo, Side } from "./types";

const $ = (id: string) => document.getElementById(id)!;
const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));
const pts = (n: number) => n.toFixed(1);
const signed = (n: number) => (n >= 0 ? "+" : "") + n.toFixed(1);
const sideCls = (s: Side) => (s === "you" ? "you" : s === "them" ? "them" : "hot");

export interface UiHandlers {
  focusGame(id: string): void;
  focusPlay(id: string): void;
  toggleScope(): void;
  speed(n: number): void;
  pause(): void;
  seek(frac: number): void;
  auto(): void;
  redzone(): void;
  cycle(): void;
  skip(dir: number): void;
  reelJump(index: number): void;
}

export class Ui {
  private logo = new PixelLabel(4, "hot");
  private youScore = new PixelLabel(4, "you");
  private themScore = new PixelLabel(4, "them");
  private delta = new PixelLabel(5, "gain");
  private gtYou = { name: new PixelLabel(3, "you"), meta: new PixelLabel(2, "dim"), pts: new PixelLabel(4, "you") };
  private gtThem = { name: new PixelLabel(3, "them"), meta: new PixelLabel(2, "dim"), pts: new PixelLabel(4, "them") };
  private cardName = new PixelLabel(4, "hot");
  private cardPts = new PixelLabel(5, "hot");
  private cardDelta = new PixelLabel(5, "gain");
  private bust = document.createElement("canvas");
  private bustKey = "";
  private seenThreats = new Set<string>();
  private seenChatter = new Set<string>();
  rail: "lineup" | "chatter" = localStorage.getItem("ffb.rail") === "chatter" ? "chatter" : "lineup";   // ffb mode only
  private state: ConsoleState | null = null;

  constructor(private h: UiHandlers) {
    $("logo").appendChild(this.logo.el);
    $("you-score").appendChild(this.youScore.el);
    $("them-score").appendChild(this.themScore.el);
    $("delta").appendChild(this.delta.el);
    $("gt-you").append(this.gtYou.name.el, this.gtYou.meta.el, this.gtYou.pts.el);
    $("gt-them").append(this.gtThem.name.el, this.gtThem.meta.el, this.gtThem.pts.el);
    this.logo.set("RETRO//GRID");
    this.bust.className = "bust"; this.bust.width = this.bust.height = SP;
    $("scope").onclick = () => h.toggleScope();
    $("scrub").onclick = (e) => h.seek(e.clientX / window.innerWidth);
    palette.subscribe(() => { this.repaint(); });
  }

  private repaint(): void {
    for (const l of [this.logo, this.youScore, this.themScore, this.delta, this.gtYou.name, this.gtYou.pts,
      this.gtThem.name, this.gtThem.pts, this.gtYou.meta, this.gtThem.meta, this.cardName, this.cardPts, this.cardDelta]) l.refresh();
    this.bustKey = "";
    if (this.state) this.render(this.state);
  }

  render(s: ConsoleState): void {
    this.state = s;
    const root = document.documentElement;
    if (root.dataset.mode !== s.mode) { root.dataset.mode = s.mode; this.logo.set(s.mode === "ffb" ? "RETRO//GRID" : "RETRO//GRID"); }
    root.dataset.rail = s.mode === "ffb" ? this.rail : "chatter";
    root.dataset.reel = s.reel ? "1" : "0";
    this.status(s); this.feeds(s); this.threats(s); this.lineup(s); this.chatter(s); this.keys(s);
    this.ghostTag(this.gtYou, s.ghosts.you); this.ghostTag(this.gtThem, s.ghosts.them);
    this.active(s.active);
  }

  private status(s: ConsoleState): void {
    const nfl = s.mode === "nfl";
    const you = s.matchup?.you ?? { name: "", points: 0 }, them = s.matchup?.them ?? { name: nfl ? "NO FEED IN FOCUS" : "", points: 0 };
    const fmt = (n: number) => (nfl ? String(n) : pts(n));
    $("you-owner").textContent = you.name; $("them-owner").textContent = them.name;
    this.youScore.set(s.matchup ? fmt(you.points) : ""); this.themScore.set(s.matchup ? fmt(them.points) : "");
    const d = you.points - them.points;
    if (nfl) this.delta.set(s.matchup?.status ?? "", "hot");             // the game clock, not a fantasy margin
    else this.delta.set((d >= 0 ? "{" : "}") + pts(Math.abs(d)), d >= 0 ? "gain" : "them");
    $("clock").textContent = s.clock.label;
    $("live").textContent = s.clock.paused ? "■ HOLD" : s.reel ? "▶ REEL" : "● ON AIR";
    $("live").classList.toggle("held", s.clock.paused);
    if (s.reel) {                                                        // a rundown, not a clock: step through it
      $("transport").innerHTML = `<b data-d="-1" title="previous play [←]">◂ PREV</b> ${s.reel.index + 1}/${s.reel.total} <b data-d="1" title="next play [→]">NEXT ▸</b>`;
      $("transport").querySelectorAll<HTMLElement>("b[data-d]").forEach((b) => (b.onclick = () => this.h.skip(Number(b.dataset.d))));
      ($("scrub").firstElementChild as HTMLElement).style.width = `${(100 * s.clock.sim) / Math.max(1, s.clock.duration)}%`;
      this.tug(you.points, them.points);
      return;
    }
    const rates = [1, 4, 15, 60];
    $("transport").innerHTML = `<b class="${s.clock.auto ? "on" : ""}" data-auto title="auto-direct: cut to big plays [A]">AUTO</b> `
      + `<b class="rz ${s.clock.redzone ? "on" : ""} ${s.clock.riding ? "riding" : ""}" data-rz title="red zone: ride any drive inside the 20 until it resolves [R]">${s.clock.redzone ? "☑" : "☐"} RED ZONE</b> ` + (s.clock.live ? "" : "SIM ") + (s.clock.live ? [] : rates).map((r, i) => `<b data-r="${r}" class="${s.clock.speed === r ? "on" : ""}" title="[${i + 1}]">${r}×</b>`).join("");
    ($("transport").querySelector("b[data-auto]") as HTMLElement).onclick = () => this.h.auto();
    ($("transport").querySelector("b[data-rz]") as HTMLElement).onclick = () => this.h.redzone();
    $("transport").querySelectorAll<HTMLElement>("b[data-r]").forEach((b) => (b.onclick = () => this.h.speed(Number(b.dataset.r))));
    ($("scrub").firstElementChild as HTMLElement).style.width = `${(100 * s.clock.sim) / Math.max(1, s.clock.duration)}%`;
    this.tug(you.points, them.points);
  }

  /** Tug-of-war bar: LED segments, your light pushing against theirs. */
  private tug(a: number, b: number): void {
    const c = $("tugbar") as HTMLCanvasElement;
    const w = (c.width = Math.max(100, Math.floor(c.clientWidth / 2))), h = (c.height = 9);
    const x = c.getContext("2d")!;
    const n = Math.floor(w / 4), share = a + b > 0 ? a / (a + b) : 0.5;
    const split = Math.round(n * share);
    for (let i = 0; i < n; i++) {
      const mine = i < split;
      const edge = Math.abs(i - split + (mine ? 1 : 0));
      x.globalAlpha = Math.max(0.25, 1 - edge / (n * 0.9));
      x.fillStyle = palette.role(mine ? "you" : "them");
      if (!mine && palette.mono) { x.fillRect(i * 4, 1, 3, 1); x.fillRect(i * 4, h - 2, 3, 1); x.fillRect(i * 4, 1, 1, h - 2); x.fillRect(i * 4 + 2, 1, 1, h - 2); }
      else x.fillRect(i * 4, 1, 3, h - 2);
    }
    x.globalAlpha = 1; x.fillStyle = palette.role("hot"); x.fillRect(split * 4 - 1, 0, 1, h);
    c.style.filter = palette.light ? "none" : `drop-shadow(0 0 5px ${palette.role(share >= 0.5 ? "you" : "them")})`;
  }

  private feeds(s: ConsoleState): void {
    const live = s.feeds.filter((f) => !["FINAL", "PRE"].includes(f.status)).length;
    $("feeds-title").textContent = s.reel ? "FINALS" : "FEEDS";
    $("feeds-count").textContent = s.reel ? `WK ${s.reel.week}` : `${live} LIVE`;
    $("feeds").innerHTML = s.feeds.map((f) => {
      const mark = f.mark === "hurt" ? `<span class="them">⚠</span>` : f.mark === "help" ? `<span class="you">▲</span>`
        : f.mark === "hot" ? `<span class="alert">⚡</span>` : `<span></span>`;
      const rz = f.rz ? `<b class="rz">RZ</b> ` : "";
      return `<li data-g="${f.game_id}" class="${f.focused ? "focused" : ""} ${f.status === "FINAL" ? "final" : ""} ${f.fav ? "fav" : ""}">
        <span class="alert">${f.focused ? "▸" : ""}</span><span class="lbl">${esc(f.label)}</span>
        <span class="dim">${esc(f.score)}</span><span class="st dim">${rz}${esc(f.status)} ${f.status.startsWith("Q") || f.status === "OT" ? esc(f.clock) : ""}</span>${mark}</li>`;
    }).join("");
    $("feeds").querySelectorAll("li").forEach((li) => (li.onclick = () => this.h.focusGame(li.dataset.g!)));
  }

  private threats(s: ConsoleState): void {
    const nfl = s.mode === "nfl";
    $("board-title").textContent = s.reel ? s.reel.title : nfl ? "ACTION" : "THREATS";
    $("scope").textContent = s.reel ? s.reel.at : nfl ? (s.favs.length ? `★ ${s.favs.join(" ")}` : "★ FOLLOW [F]") : s.scope === "matchup" ? "◂MATCHUP▸" : "◂LEAGUE▸";
    $("threats").innerHTML = s.threats.map((t) => {
      if (t.delta === null) {                                   // ACTION row: tag, team, what happened
        const fresh = !this.seenThreats.has(t.id); this.seenThreats.add(t.id);
        const cls = t.kind === "fav" ? "gain" : "hot";
        return `<li data-p="${t.play_id}" class="${fresh && !s.reel ? "fresh" : ""} ${t.lead_change ? "lead" : ""} ${t.current ? "current" : ""}">
          <span class="alert">${s.reel ? (t.current ? "▸" : "") : "⚡"}</span><span class="${t.current ? "hot" : cls}">${esc(t.name)}${t.team ? " · " + esc(t.team) : ""}</span>
          <span class="dim">${esc(t.label ?? s.feeds.find((f) => f.game_id === t.game_id)?.label ?? "")}</span><span class="hl">${t.lead_change ? "◆ LEAD CHANGE · " : ""}${esc(t.headline)}</span></li>`;
      }
      const cls = t.kind === "hurt" ? "them" : "you";
      const fresh = !this.seenThreats.has(t.id);
      this.seenThreats.add(t.id);
      return `<li data-p="${t.play_id}" class="${fresh ? "fresh" : ""} ${t.lead_change ? "lead" : ""}">
        <span class="${cls}">${t.kind === "hurt" ? "⚠" : "▲"}</span><span class="${cls}">${esc(t.name)}</span>
        <span class="${cls}">${signed(t.delta ?? 0)}</span><span class="hl">${t.lead_change ? "◆ LEAD CHANGE · " : ""}${esc(t.headline)}</span></li>`;
    }).join("") || `<li class="dim">— QUIET —</li>`;
    $("threats").querySelectorAll<HTMLElement>("li[data-p]").forEach((li) => (li.onclick = () => this.h.focusPlay(li.dataset.p!)));
    if (this.seenThreats.size > 400) this.seenThreats = new Set(s.threats.map((t) => t.id));
  }

  /** The crowd: newest at the bottom, a team's own sub in that team's light. */
  private chatter(s: ConsoleState): void {
    $("chatter-title").textContent = s.reel ? "RUNDOWN" : "CHATTER";
    if (s.reel) {                                               // midweek there is no crowd: the rail is the show's running order
      $("chatter-src").textContent = `${s.reel.season}`;
      $("chatter").innerHTML = s.reel.segments.map((g) =>
        `<li data-i="${g.start}" class="seg ${g.current ? "current" : ""}"><span class="src ${g.current ? "alert" : "dim"}">${g.current ? "▸" : ""}</span><span class="txt ${g.current ? "hot" : ""}">${esc(g.title)} <em class="dim">${g.count}</em></span></li>`).join("");
      $("chatter").querySelectorAll<HTMLElement>("li[data-i]").forEach((li) => (li.onclick = () => this.h.reelJump(Number(li.dataset.i))));
      return;
    }
    const g = s.feeds.find((f) => f.focused);
    const [away, home] = g ? g.label.split("@") : ["", ""];
    $("chatter-src").textContent = g ? g.label : "";
    $("chatter").innerHTML = s.chatter.map((c) => {
      const fresh = !this.seenChatter.has(c.id); this.seenChatter.add(c.id);
      const cls = s.mode === "nfl" ? (c.team === away ? "you" : c.team === home ? "them" : "") : "";
      return `<li class="${fresh ? "fresh" : ""}"><span class="src ${cls || "dim"}">${esc(c.source)}</span><span class="txt">${esc(c.text)}</span></li>`;
    }).join("") || `<li class="dim">— ${g ? "CROWD QUIET" : "NO FEED"} —</li>`;
    if (this.seenChatter.size > 2000) this.seenChatter = new Set(s.chatter.map((c) => c.id));
  }

  renderRadio(r: Radio, s: ConsoleState | null): void {
    const el = $("radio");
    const label = (id?: string) => s?.feeds.find((f) => f.game_id === id)?.label ?? "";
    const st = r.stream;
    if (r.state === "off") { el.className = "dim"; el.innerHTML = `♪ RADIO OFF <em>[M]</em>`; return; }
    const vol = "▮".repeat(Math.round(r.volume * 5)).padEnd(5, "▯");
    if (r.state === "dead") {
      el.className = "them";
      el.innerHTML = `♪ NO SIGNAL · ${esc(st?.station || st?.team || "")} <a class="dim" href="${r.game?.nfl_plus ?? "#"}" target="_blank" rel="noopener">NFL+ ▸</a> <em>[H] OTHER BOOTH</em>`;
      return;
    }
    el.className = r.state === "on" ? "gain" : "alert";
    el.innerHTML = `♪ ${r.state === "on" ? "" : "TUNING · "}${esc(st?.station ?? "")} · ${esc(st?.team ?? "")} CALL · ${esc(label(r.game?.game_id))} <em>${vol}</em>`;
  }

  private keys(s: ConsoleState): void {
    const ffb = s.mode === "ffb";
    const k = s.reel ? [
      "[T] THEME · [F] FOLLOW TEAMS",
      "[←→] PREV / NEXT PLAY · [SPACE] HOLD",
      "CLICK A ROW TO JUMP · [N] MUTE CUES",
    ].join("<br>") : [
      "[T] THEME · [F] FOLLOW TEAMS",
      `[A] AUTO-DIRECT ${s.clock.auto ? "ON" : "OFF"} · [R] RED ZONE ${s.clock.redzone ? "ON" : "OFF"}`,
      "[M] RADIO · [H] OTHER BOOTH · [-][=] VOL · [N] MUTE CUES",
      s.clock.live ? "" : "[SPACE] HOLD · [1-4] RATE · [←→] SKIP",
      `[C] AUTO-REPLAY <span id="cycle">${this.cycleText}</span> · [ [ ] ] STEP · [/] BACK`,
      s.ffb_available ? `[X] FANTASY LAYER ${ffb ? "ON" : "OFF"}` + (ffb ? " · [V] VIEWER · [L] SCOPE · [TAB] LINEUP/CHATTER" : "") : "",
    ].filter(Boolean).join("<br>");
    if ($("keys").dataset.k !== k) { $("keys").dataset.k = k; $("keys").innerHTML = k; }
  }
  cycleText = "";

  private lineup(s: ConsoleState): void {
    if (s.mode === "nfl" || !s.matchup) { $("lineup").innerHTML = ""; return; }
    const rows = s.lineup.map((r) => {
      const yl = r.you.points >= r.them.points;
      return `<li><span class="slot">${r.slot}</span>
        <span class="you ${yl ? "" : "lose"} ${r.you.live ? "" : "idle"}">${esc(r.you.name)}</span>
        <span class="pts you ${yl ? "" : "lose"}">${pts(r.you.points)}</span><span></span>
        <span class="pts them ${yl ? "lose" : ""}">${pts(r.them.points)}</span>
        <span class="tn them ${yl ? "lose" : ""} ${r.them.live ? "" : "idle"}">${esc(r.them.name)}</span>
        <span class="them">${r.losing ? "⚠" : ""}</span></li>`;
    });
    const { you, them } = s.matchup;
    rows.push(`<li class="total"><span class="slot">TOT</span><span></span><span class="pts you">${pts(you.points)}</span><span></span>
      <span class="pts them">${pts(them.points)}</span><span></span><span></span></li>`);
    $("lineup").innerHTML = rows.join("");
  }

  private ghostTag(tag: { name: PixelLabel; meta: PixelLabel; pts: PixelLabel }, g: GhostInfo | null): void {
    tag.name.set(g ? g.name : ""); tag.meta.set(g ? g.meta : ""); tag.pts.set(g ? signed(g.points) : "");
  }

  private active(a: ActiveCard | null): void {
    const el = $("active");
    if (!a) { el.innerHTML = `<span></span><span class="dim">NO ACTIVE TRACK</span>`; return; }
    const cls = sideCls(a.side);
    const role = a.side === "you" ? "you" : a.side === "them" ? "them" : "hot";
    this.cardName.set(a.title, role);
    this.cardPts.set(pts(a.points), role);
    // colour by what it means for the viewer, not by sign
    const good = a.side === "them" ? a.play_delta < 0 : a.play_delta >= 0;
    this.cardDelta.set(a.play_delta ? signed(a.play_delta) : "", a.side ? (good ? "gain" : "them") : "hot");
    el.innerHTML = `<span class="b"></span>
      <div class="who"><span class="n"></span><span class="dim">${esc(a.meta)}</span></div>
      <div class="mid"><div class="${cls}">${esc(a.statline)}</div><div class="hot">▸ ${esc(a.play_text)}</div><div class="dim">${esc(a.extra)}</div></div>
      <div class="pts"><div><small>${this.state?.mode === "nfl" ? "FPTS" : "PTS"}</small><span class="p"></span></div><div><small>THIS PLAY</small><span class="d"></span></div></div>`;
    el.querySelector(".b")!.replaceWith(this.bust);
    el.querySelector(".n")!.replaceWith(this.cardName.el);
    el.querySelector(".p")!.replaceWith(this.cardPts.el);
    el.querySelector(".d")!.replaceWith(this.cardDelta.el);
    const key = `${a.player_id}|${a.side}|${palette.theme.slug}`;
    if (key !== this.bustKey) {
      this.bustKey = key;
      const paint = (d: ImageData) => {
        if (this.bustKey !== key) return;
        const x = this.bust.getContext("2d")!; x.clearRect(0, 0, SP, SP);
        x.drawImage(tint(d, a.side === "you" ? "you" : a.side === "them" ? "them" : "hot"), 0, 0);
      };
      paint(proceduralBust());
      if (a.sprite) void loadSprite(a.sprite).then((d) => d && paint(d));
    }
  }
}
