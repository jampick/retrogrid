// DOM rails and cards. No frames anywhere: a "card" is type floating in light.

import { palette } from "./palette";
import { PixelLabel } from "./pixelfont";
import { loadSprite, proceduralBust, tint, SP } from "./sprites";
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
}

export class Ui {
  private logo = new PixelLabel(4, "hot");
  private youScore = new PixelLabel(4, "you");
  private themScore = new PixelLabel(4, "them");
  private delta = new PixelLabel(5, "gain");
  private gtYou = { name: new PixelLabel(3, "you"), pts: new PixelLabel(4, "you") };
  private gtThem = { name: new PixelLabel(3, "them"), pts: new PixelLabel(4, "them") };
  private cardName = new PixelLabel(4, "hot");
  private cardPts = new PixelLabel(5, "hot");
  private cardDelta = new PixelLabel(5, "gain");
  private bust = document.createElement("canvas");
  private bustKey = "";
  private seenThreats = new Set<string>();
  private state: ConsoleState | null = null;

  constructor(private h: UiHandlers) {
    $("logo").appendChild(this.logo.el);
    $("you-score").appendChild(this.youScore.el);
    $("them-score").appendChild(this.themScore.el);
    $("delta").appendChild(this.delta.el);
    $("gt-you").append(this.gtYou.name.el, this.gtYou.pts.el);
    $("gt-them").append(this.gtThem.name.el, this.gtThem.pts.el);
    this.logo.set("RETRO//FFB");
    this.bust.className = "bust"; this.bust.width = this.bust.height = SP;
    $("scope").onclick = () => h.toggleScope();
    $("scrub").onclick = (e) => h.seek(e.clientX / window.innerWidth);
    palette.subscribe(() => { this.repaint(); });
  }

  private repaint(): void {
    for (const l of [this.logo, this.youScore, this.themScore, this.delta, this.gtYou.name, this.gtYou.pts,
      this.gtThem.name, this.gtThem.pts, this.cardName, this.cardPts, this.cardDelta]) l.refresh();
    this.bustKey = "";
    if (this.state) this.render(this.state);
  }

  render(s: ConsoleState): void {
    this.state = s;
    this.status(s); this.feeds(s); this.threats(s); this.lineup(s);
    this.ghostTag(this.gtYou, s.ghosts.you); this.ghostTag(this.gtThem, s.ghosts.them);
    this.active(s.active);
  }

  private status(s: ConsoleState): void {
    const { you, them } = s.matchup;
    $("you-owner").textContent = you.name; $("them-owner").textContent = them.name;
    this.youScore.set(pts(you.points)); this.themScore.set(pts(them.points));
    const d = you.points - them.points;
    this.delta.set((d >= 0 ? "{" : "}") + pts(Math.abs(d)), d >= 0 ? "gain" : "them");
    $("clock").textContent = s.clock.label;
    $("live").textContent = s.clock.paused ? "■ HOLD" : "● LIVE";
    $("live").classList.toggle("held", s.clock.paused);
    const rates = [1, 4, 15, 60];
    $("transport").innerHTML = `<b class="${s.clock.auto ? "on" : ""}" data-auto title="auto-direct [A]">AUTO</b> SIM ` + rates.map((r, i) => `<b data-r="${r}" class="${s.clock.speed === r ? "on" : ""}" title="[${i + 1}]">${r}×</b>`).join("");
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
    $("feeds-count").textContent = `${live} LIVE`;
    $("feeds").innerHTML = s.feeds.map((f) => {
      const mark = f.mark === "hurt" ? `<span class="them">⚠</span>` : f.mark === "help" ? `<span class="you">▲</span>` : `<span></span>`;
      return `<li data-g="${f.game_id}" class="${f.focused ? "focused" : ""} ${f.status === "FINAL" ? "final" : ""}">
        <span class="alert">${f.focused ? "▸" : ""}</span><span class="lbl">${esc(f.label)}</span>
        <span class="dim">${esc(f.score)}</span><span class="st dim">${esc(f.status)} ${f.status.startsWith("Q") || f.status === "OT" ? esc(f.clock) : ""}</span>${mark}</li>`;
    }).join("");
    $("feeds").querySelectorAll("li").forEach((li) => (li.onclick = () => this.h.focusGame(li.dataset.g!)));
  }

  private threats(s: ConsoleState): void {
    $("scope").textContent = s.scope === "matchup" ? "◂MATCHUP▸" : "◂LEAGUE▸";
    $("threats").innerHTML = s.threats.map((t) => {
      const cls = t.kind === "hurt" ? "them" : "you";
      const fresh = !this.seenThreats.has(t.id);
      this.seenThreats.add(t.id);
      return `<li data-p="${t.play_id}" class="${fresh ? "fresh" : ""} ${t.lead_change ? "lead" : ""}">
        <span class="${cls}">${t.kind === "hurt" ? "⚠" : "▲"}</span><span class="${cls}">${esc(t.name)}</span>
        <span class="${cls}">${signed(t.delta)}</span><span class="hl">${t.lead_change ? "◆ LEAD CHANGE · " : ""}${esc(t.headline)}</span></li>`;
    }).join("") || `<li class="dim">— QUIET —</li>`;
    $("threats").querySelectorAll<HTMLElement>("li[data-p]").forEach((li) => (li.onclick = () => this.h.focusPlay(li.dataset.p!)));
    if (this.seenThreats.size > 400) this.seenThreats = new Set(s.threats.map((t) => t.id));
  }

  private lineup(s: ConsoleState): void {
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

  private ghostTag(tag: { name: PixelLabel; pts: PixelLabel }, g: GhostInfo | null): void {
    tag.name.set(g ? g.name : ""); tag.pts.set(g ? signed(g.points) : "");
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
      <div class="pts"><div><small>PTS</small><span class="p"></span></div><div><small>THIS PLAY</small><span class="d"></span></div></div>`;
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
