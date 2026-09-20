// Wire types. Mirrors backend/retroffb/console.py frames.

export type RoleName = "bg" | "rail" | "grid" | "you" | "them" | "alert" | "gain" | "hot" | "dim";

export interface Theme {
  slug: string;
  name: string;
  font: string | null;
  source: "bundled" | "omarchy" | "builtin";
  mode: "dark" | "light";
  blend: "additive" | "multiply";
  mono: boolean;
  roles: Record<RoleName, string>;
  notes: string[];
}

export type Side = "you" | "them" | "league" | null;
export type Mark = "help" | "hurt" | null;

export interface Feed {
  game_id: string;
  label: string;        // "KC@BUF"
  status: string;       // "Q3" "HT" "FINAL" "PRE"
  clock: string;
  score: string;        // "21-17"
  mark: Mark;
  focused: boolean;
}

export interface Threat {
  id: string;
  play_id: string;
  game_id: string;
  kind: "help" | "hurt";
  name: string;         // "ALLEN"
  delta: number;
  headline: string;
  lead_change: boolean;
}

export interface LineupCell { player_id: string; name: string; points: number; live: boolean }
export interface LineupRow { slot: string; you: LineupCell; them: LineupCell; losing: boolean }

export interface GhostInfo { player_id: string; name: string; points: number; heat: number; sprite: string | null }

export interface ActiveCard {
  player_id: string;
  side: Side;
  title: string;        // "T.KELCE"
  meta: string;         // "TE·KC·#87"
  statline: string;     // "5 REC · 62 YD · 1 TD"
  points: number;
  play_delta: number;
  play_text: string;    // "2Q 4:12  pass short right, 9yd, 1st down"
  extra: string;        // "TGT 7 · aDOT 8.2"
  sprite: string | null;
}

export interface ConsoleState {
  type: "state";
  clock: { label: string; sim: number; duration: number; speed: number; paused: boolean; auto: boolean };
  viewer: { team_key: string; owner: string };
  viewers: { team_key: string; owner: string; name: string }[];
  matchup: { you: { name: string; points: number }; them: { name: string; points: number } };
  feeds: Feed[];
  threats: Threat[];
  scope: "matchup" | "league";
  lineup: LineupRow[];
  ghosts: { you: GhostInfo | null; them: GhostInfo | null };
  active: ActiveCard | null;
}

export interface Keyframe { t: number; x: number; y: number; z?: number }   // z: ball height, yards
export interface Actor {
  id: string;
  role: string;                       // "QB" "WR" "CB" "BALL" ...
  team: "off" | "def" | "ball" | "flag";
  label: string | null;               // set only for involved players
  side: Side;                         // fantasy ownership relative to viewer
  involved: boolean;
  keys: Keyframe[];                   // field yards; x 0..53.3 across, y 0..120 along, offence moves +y
}

export interface PlayFrame {
  type: "play";
  play_id: string;
  game_id: string;
  label: string;                      // "KC@BUF"
  situation: string;                  // "2ND & 7 · BUF 45"
  desc: string;
  los: number;                        // y of line of scrimmage (field yards)
  to_go: number | null;               // y of first-down line
  los_line: boolean;                  // false for kicks
  duration: number;                   // seconds of animation
  actors: Actor[];
  result: string;                     // "+9 1ST DOWN" / "TOUCHDOWN"
  result_kind: "good" | "bad" | "neutral";   // for the offence
  endzones: { near: string; far: string };   // team abbrs painted in the end zones
  deltas: { player_id: string; name: string; points: number; side: Side }[];
  alert: boolean;
  focus: boolean;                     // server suggests showing it now
  settled: boolean;                   // catch-up: show the finished diagram, don't replay
  template: string;
}

export interface BannerFrame { type: "banner"; threat: Threat }
export interface HelloFrame { type: "hello"; system: Theme | null; themes: Theme[] }
export interface ThemeFrame { type: "theme"; system: Theme }
export type Frame = ConsoleState | PlayFrame | BannerFrame | HelloFrame | ThemeFrame;
