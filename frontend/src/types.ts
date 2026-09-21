// Wire types. Mirrors backend/retrogrid/console.py frames.

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
export type Mark = "help" | "hurt" | "hot" | null;      // hot: NFL mode, the game is where the action is

export interface Feed {
  game_id: string;
  label: string;        // "KC@BUF"
  status: string;       // "Q3" "HT" "FINAL" "PRE"
  clock: string;
  score: string;        // "21-17"
  mark: Mark;
  focused: boolean;
  fav: boolean;         // a followed team is playing
  rz?: boolean;         // a drive is inside the 20
}

export interface Threat {
  id: string;
  play_id: string;
  game_id: string;
  kind: "help" | "hurt" | "neutral" | "fav";   // neutral/fav: NFL mode ACTION rows
  name: string;         // "ALLEN" — or the action tag, "TD"
  team?: string;        // NFL mode: who it was good for
  delta: number | null; // fantasy points; null in NFL mode
  headline: string;
  lead_change: boolean;
  label?: string;       // REEL: the row's own game ("KC@BUF"); the feeds list is another week's finals
  current?: boolean;    // REEL: the item on screen
}

// REEL: the highlight show of finished weeks. Present on state frames only in that mode.
export interface ReelState {
  title: string;        // "TOP 10 · WK 2"
  number: string;       // "#7" in a countdown
  index: number; total: number;
  at: string;           // "4/10" within the segment
  phase: "card" | "play" | "result" | "replay";
  week: number; season: number;
  segments: { title: string; count: number; start: number; current: boolean }[];
}
export interface ReelTag { week: number; number: string; rank: number; score: number; tag: string; headline: string; wpa: number | null; replay: boolean }
/** What a highlight loses when it leaves its game: shown before the snap. */
export interface ReelCard { type: "reel_card"; segment: string; number: string; week: number; label: string; score: string; clock: string; situation: string; seconds: number }

export interface LineupCell { player_id: string; name: string; points: number; live: boolean }
export interface LineupRow { slot: string; you: LineupCell; them: LineupCell; losing: boolean }

export interface GhostInfo { player_id: string; name: string; meta: string; points: number; heat: number; sprite: string | null }

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

export interface ChatterLine { id: string; game_id: string; source: string; author: string; text: string; team: string | null }
export interface StreamInfo { team: string; station: string; url: string | null; kind: "hls" | "direct" | null }
export interface GameAudio { game_id: string; nfl_plus: string; home: StreamInfo; away: StreamInfo }

export interface ConsoleState {
  type: "state";
  mode: "nfl" | "ffb";                // nfl: real scoreboard + ACTION; ffb: the fantasy layer on top
  ffb_available: boolean;             // a league is configured server-side
  clock: { label: string; sim: number; duration: number; speed: number; paused: boolean; auto: boolean; live: boolean;
           redzone?: boolean; riding?: boolean };      // RED ZONE mode on · committed to a drive inside the 20
  viewer: { team_key: string; owner: string } | null;
  viewers: { team_key: string; owner: string; name: string }[];
  // ffb: fantasy matchup. nfl: the focused game, away on the left — null with nothing in focus.
  matchup: { you: { name: string; points: number }; them: { name: string; points: number }; status: string } | null;
  favs: string[];
  teams: string[];                    // every team on the slate
  chatter: ChatterLine[];             // focused game, oldest first
  audio: GameAudio | null;            // focused game
  feeds: Feed[];
  threats: Threat[];
  scope: "matchup" | "league" | "action";
  lineup: LineupRow[];
  ghosts: { you: GhostInfo | null; them: GhostInfo | null };
  active: ActiveCard | null;
  reel?: ReelState;
}

export interface Keyframe { t: number; x: number; y: number; z?: number }   // z: ball height, yards
export interface Actor {
  id: string;
  role: string;                       // "QB" "WR" "CB" "BALL" ...
  team: "off" | "def" | "ball" | "flag";
  label: string | null;               // set only for involved players
  side: Side;                         // ffb: fantasy ownership relative to viewer. nfl: away = you, home = them
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
  reel?: ReelTag;
}

export interface BannerFrame { type: "banner"; threat: Threat }
export interface HelloFrame { type: "hello"; system: Theme | null; themes: Theme[] }
export interface ThemeFrame { type: "theme"; system: Theme }
export type Frame = ConsoleState | PlayFrame | BannerFrame | HelloFrame | ThemeFrame | ReelCard;
