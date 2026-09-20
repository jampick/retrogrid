// RADIO: the focused game's flagship-station call, best effort (backend providers/audio.py).
// It tunes to the game in focus when switched on and then *stays* there while the
// view roams — AUTO-DIRECT cutting the commentary every big play would be unbearable.

import Hls from "hls.js";
import type { GameAudio, StreamInfo } from "./types";

export type RadioState = "off" | "tuning" | "on" | "dead";

export class Radio {
  state: RadioState = "off";
  game: GameAudio | null = null;                  // what it is pinned to
  side: "home" | "away" = (localStorage.getItem("ffb.radio.side") as "home" | "away") ?? "home";
  onchange: () => void = () => {};
  private el = new Audio();
  private hls: Hls | null = null;

  constructor() {
    this.el.preload = "none";
    this.el.volume = Number(localStorage.getItem("ffb.radio.vol") ?? "0.8");
    this.el.onplaying = () => this.set("on");
    this.el.onwaiting = () => this.state === "on" && this.set("tuning");
    this.el.onerror = () => this.state !== "off" && this.set("dead");
  }

  get stream(): StreamInfo | null { return this.game ? this.game[this.side] : null; }
  get volume(): number { return this.el.volume; }

  private set(s: RadioState): void { this.state = s; this.onchange(); }

  private stop(): void {
    this.hls?.destroy(); this.hls = null;
    this.el.pause(); this.el.removeAttribute("src"); this.el.load();
  }

  /** On: pin to `focus`. Off: silence. */
  toggle(focus: GameAudio | null): void {
    if (this.state !== "off") { this.stop(); this.game = null; this.set("off"); return; }
    this.tune(focus);
  }

  /** Re-pin to another game (or the other side's call) without switching off. */
  tune(game: GameAudio | null): void {
    this.stop();
    this.game = game;
    let s = this.stream;
    if (game && !s?.url) {                         // no stream for this call: try the other booth
      const other = this.side === "home" ? "away" : "home";
      if (game[other].url) { this.side = other; s = game[other]; }
    }
    if (!s?.url) { this.set("dead"); return; }
    this.set("tuning");
    const hlsUrl = s.kind === "hls" || /\.m3u8(\?|$)/.test(s.url);
    if (hlsUrl && Hls.isSupported()) {
      this.hls = new Hls({ lowLatencyMode: false });
      this.hls.on(Hls.Events.ERROR, (_e, d) => { if (d.fatal) { this.stop(); this.set("dead"); } });
      this.hls.loadSource(s.url); this.hls.attachMedia(this.el);
    } else this.el.src = s.url;
    void this.el.play().catch(() => this.set("dead"));
  }

  swap(): void {
    this.side = this.side === "home" ? "away" : "home";
    localStorage.setItem("ffb.radio.side", this.side);
    if (this.state !== "off") this.tune(this.game); else this.onchange();
  }

  nudge(d: number): void {
    this.el.volume = Math.max(0, Math.min(1, this.el.volume + d));
    localStorage.setItem("ffb.radio.vol", String(this.el.volume));
    this.onchange();
  }
}
