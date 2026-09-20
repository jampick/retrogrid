"""CHATTER: what the crowd is saying about each game (right rail).

Two sources behind one seam, like everything else here:

* :class:`StubChatter` — canned reactions keyed to the plays themselves, so SIM
  SUNDAY has a crowd and the rail can be demoed any day.
* :class:`RedditChatter` — r/nfl and the team subreddits' game threads. Reddit's
  JSON API is walled (403 without an approved OAuth app), but the Atom feeds are
  not, at **one request a minute per IP**. So: one combined
  ``/r/nfl+<team subs>/comments/.rss`` poll, bucketed per game by the entry
  title ("/u/x on Game Thread: Away (W-L) @ Home (W-L)") and its subreddit.
  At peak that is a sample of the thread, not all of it — fine for a rail.
"""
from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from xml.etree import ElementTree

from ..models import Game, PlayRow

log = logging.getLogger("retrogrid.chatter")
UA = "RetroGrid/0.1 (personal hobby app)"
KEEP = 60                          # lines remembered per game
MAX_LEN = 160

# abbr: (city, nickname, subreddit)
TEAMS: dict[str, tuple[str, str, str]] = {
    "ARI": ("Arizona", "Cardinals", "AZCardinals"), "ATL": ("Atlanta", "Falcons", "falcons"),
    "BAL": ("Baltimore", "Ravens", "ravens"), "BUF": ("Buffalo", "Bills", "buffalobills"),
    "CAR": ("Carolina", "Panthers", "panthers"), "CHI": ("Chicago", "Bears", "CHIBears"),
    "CIN": ("Cincinnati", "Bengals", "bengals"), "CLE": ("Cleveland", "Browns", "Browns"),
    "DAL": ("Dallas", "Cowboys", "cowboys"), "DEN": ("Denver", "Broncos", "DenverBroncos"),
    "DET": ("Detroit", "Lions", "detroitlions"), "GB": ("Green Bay", "Packers", "GreenBayPackers"),
    "HOU": ("Houston", "Texans", "Texans"), "IND": ("Indianapolis", "Colts", "Colts"),
    "JAX": ("Jacksonville", "Jaguars", "Jaguars"), "KC": ("Kansas City", "Chiefs", "KansasCityChiefs"),
    "LA": ("Los Angeles", "Rams", "LosAngelesRams"), "LAC": ("Los Angeles", "Chargers", "Chargers"),
    "LV": ("Las Vegas", "Raiders", "raiders"), "MIA": ("Miami", "Dolphins", "miamidolphins"),
    "MIN": ("Minnesota", "Vikings", "minnesotavikings"), "NE": ("New England", "Patriots", "Patriots"),
    "NO": ("New Orleans", "Saints", "Saints"), "NYG": ("New York", "Giants", "NYGiants"),
    "NYJ": ("New York", "Jets", "nyjets"), "PHI": ("Philadelphia", "Eagles", "eagles"),
    "PIT": ("Pittsburgh", "Steelers", "steelers"), "SEA": ("Seattle", "Seahawks", "Seahawks"),
    "SF": ("San Francisco", "49ers", "49ers"), "TB": ("Tampa Bay", "Buccaneers", "buccaneers"),
    "TEN": ("Tennessee", "Titans", "Tennesseetitans"), "WAS": ("Washington", "Commanders", "Commanders"),
}
SUB_TEAM = {sub.lower(): abbr for abbr, (_, _, sub) in TEAMS.items()}
_NICK = {abbr: re.compile(rf"\b{re.escape(nick)}\b", re.I) for abbr, (_, nick, _) in TEAMS.items()}


@dataclass
class ChatterLine:
    id: str
    game_id: str
    source: str                    # "r/nfl" "r/buffalobills"
    author: str
    text: str
    team: str | None = None        # set when it came from a team's own crowd

    def to_dict(self) -> dict:
        return asdict(self)


class ChatterBox:
    """Per-game ring of recent lines. The engine owns one; sources push into it."""

    def __init__(self) -> None:
        self.lines: dict[str, deque[ChatterLine]] = {}
        self._seen: set[str] = set()

    def push(self, line: ChatterLine) -> bool:
        if line.id in self._seen:
            return False
        self._seen.add(line.id)
        if len(self._seen) > 20000:                       # a long Sunday; ids only ever grow
            self._seen = {l.id for q in self.lines.values() for l in q}
        self.lines.setdefault(line.game_id, deque(maxlen=KEEP)).append(line)
        return True

    def recent(self, game_id: str | None, n: int = 14) -> list[dict]:
        return [l.to_dict() for l in list(self.lines.get(game_id or "", ()))[-n:]]

    def reset(self) -> None:
        self.lines.clear()
        self._seen.clear()


# ── Reddit ─────────────────────────────────────────────────────────────────
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_URL = re.compile(r"https?://\S+")
_GAME_THREAD = re.compile(r"\bgame\s*(day)?\s*thread\b", re.I)
_NOT_LIVE = re.compile(r"\b(post|pre)[\s-]*game\b|\bhub\b", re.I)


def clean(body_html: str) -> str:
    """Reddit's HTML comment body → one line of plain text ('' = not worth showing)."""
    text = html.unescape(_TAGS.sub(" ", html.unescape(body_html)))
    text = _WS.sub(" ", _URL.sub("", text)).strip()
    if not text or text in ("[deleted]", "[removed]"):
        return ""
    return text if len(text) <= MAX_LEN else text[: MAX_LEN - 1].rstrip() + "…"


def teams_in(title: str) -> set[str]:
    return {abbr for abbr, rx in _NICK.items() if rx.search(title)}


def route(subreddit: str, title: str, games: Iterable[Game]) -> tuple[str, str | None] | None:
    """Which game a comment belongs to: (game_id, team crowd) or None.

    r/nfl: the post title has to name both teams (game threads and highlights
    alike). A team subreddit: any game-thread post is that team's game."""
    games = list(games)
    by_team = {t: g.id for g in games for t in (g.home, g.away)}
    team = SUB_TEAM.get(subreddit.lower())
    if team:
        if team in by_team and _GAME_THREAD.search(title) and not _NOT_LIVE.search(title):
            return by_team[team], team
        return None
    if _NOT_LIVE.search(title):
        return None
    named, thread = teams_in(title), bool(_GAME_THREAD.search(title))
    hit = [g.id for g in games if ({g.home, g.away} <= named if thread else {g.home, g.away} & named)]
    return (hit[0], None) if len(hit) == 1 else None


def parse_feed(xml: str, games: Iterable[Game]) -> list[ChatterLine]:
    """Atom comments feed → routed lines, oldest first."""
    games = list(games)
    out: list[ChatterLine] = []
    for e in ElementTree.fromstring(xml).findall("a:entry", _ATOM):
        cid = e.findtext("a:id", "", _ATOM)
        title = e.findtext("a:title", "", _ATOM)
        cat = e.find("a:category", _ATOM)
        sub = cat.get("term", "") if cat is not None else ""
        author = (e.findtext("a:author/a:name", "", _ATOM) or "").removeprefix("/u/")
        if not cid or author.lower() in ("automoderator", "nflgdtbot", "nfl_mod"):
            continue
        where = route(sub, title.split(" on ", 1)[-1], games)
        text = clean(e.findtext("a:content", "", _ATOM))
        if where and text:
            out.append(ChatterLine(cid, where[0], f"r/{sub}", author, text, where[1]))
    return out[::-1]


class RedditChatter:
    """One combined feed, once a minute, for as long as any game is on."""

    def __init__(self, box: ChatterBox, games_now, every: float = 65.0) -> None:   # noqa: ANN001
        self.box, self.games_now, self.every = box, games_now, every

    def url(self, games: Iterable[Game]) -> str:
        subs = ["nfl"] + sorted({TEAMS[t][2] for g in games for t in (g.home, g.away) if t in TEAMS})
        return f"https://www.reddit.com/r/{'+'.join(subs)}/comments/.rss?limit=100"

    def fetch(self, url: str) -> tuple[int, str, float]:
        """(status, body, seconds until Reddit will answer again). urllib on purpose:
        Reddit 403s httpx's client fingerprint but serves the stdlib's."""
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read().decode("utf-8", "replace"), float(r.headers.get("x-ratelimit-reset") or 0)
        except urllib.error.HTTPError as e:
            return e.code, "", float(e.headers.get("x-ratelimit-reset") or 0)

    async def run(self) -> None:
        while True:
            wait = self.every
            games = [g for g in self.games_now() if g.status in ("live", "half")]
            if games:
                try:
                    status, body, reset = await asyncio.to_thread(self.fetch, self.url(games))
                    wait = max(wait, reset + 2)
                    if status == 200:
                        new = sum(self.box.push(l) for l in parse_feed(body, games))
                        log.info("chatter: +%d lines across %d games", new, len(games))
                    else:
                        log.warning("chatter: reddit said %s", status)
                        wait *= 2 if status in (403, 429) else 1
                except Exception as e:                        # noqa: BLE001 — the rail going quiet is not an outage
                    log.warning("chatter: %s", e)
            await asyncio.sleep(wait)


# ── stub crowd ─────────────────────────────────────────────────────────────
_HANDLES = ("gridiron_ghost", "4thAndLong", "null_route", "pylon_cam", "xX_blitz_Xx", "hashmark", "TurfToe99",
            "checkdown_charlie", "coverZero", "sidelineOracle", "snapcount", "redzone_rat", "twoMinuteDrill",
            "longsnapper4mvp", "PAT_enjoyer", "motion_man", "flagOnThePlay", "hardcount")
_LINES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {   # tag: (the team it favoured, the other crowd)
    "TD": (("LETS GOOOOO", "{who} is HIM", "that's my {pos}", "inject it", "SIX", "we are so back", "{who} cannot be guarded"),
           ("nobody within 5 yards of him. cool.", "fire the DC", "I've seen enough", "pain.", "tackling is optional apparently")),
    "INT": (("BALLHAWK", "thank you for the gift", "momentum!!", "defense is cooking"),
            ("what was that throw", "he threw it right to him", "bench him", "I'm going to be sick")),
    "FUM": (("BALL OUT", "scoop it!!", "huge turnover"), ("HOLD ON TO THE BALL", "two hands. TWO.", "of course")),
    "SAFETY": (("SAFETY lol", "two points and the ball"), ("a safety. in this economy.", "never seen a team do this")),
    "FG": (("points are points", "automatic", "kicker szn"), ("bend don't break I guess", "held them to 3, fine")),
    "MISS": (("doink!", "thank you kicker", "wide. lol"), ("WE NEED A KICKER", "doink. of course doink.")),
    "BLOCK": (("BLOCKED", "special teams ace!!"), ("how do you let that get blocked", "special teams is a disaster")),
    "4TH": (("ballsy call, love it", "analytics win again", "MOVE THOSE CHAINS"), ("get off the field challenge", "4th down and we let them have it")),
    "STOP": (("STUFFED", "not today", "turnover on downs baby"), ("why not just kick it", "that playcall on 4th was criminal")),
    "2PT": (("two!!", "gutsy"), ("soft coverage on a 2pt, ok",)),
    "BIG": (("{who} is cooking", "chunk play!", "he's got wheels", "what a play by {who}"), ("missed tackles everywhere", "who is covering {who}??", "busted coverage again")),
    "SACK": (("SACK LUNCH", "got him!", "pressure all day"), ("o-line is a turnstile", "he had 0.4 seconds to throw")),
}
_NEUTRAL = ("refs are having a day", "this game is a rock fight", "can we get a commercial break (we will)", "great game so far",
            "announcers haven't said one correct thing", "clock management speedrun", "run. the. ball.", "that spot was generous",
            "I need this one for my parlay", "who's watching on a side monitor at work", "hold on every play, never called")


class StubChatter:
    """A crowd written from the play stream. Deterministic per play (seeded on
    play_id), so a rebuild after a seek says the same things."""

    def __init__(self, box: ChatterBox, directory=None) -> None:      # noqa: ANN001
        self.box, self.directory = box, directory

    def react(self, p: PlayRow, tag: str | None, team: str | None) -> None:
        rng = random.Random(p.play_id)
        _, _, away, home = p.game_id.split("_")
        pl = self.directory.player(p.td_player_id or p.receiver_id or p.rusher_id or p.passer_id) if self.directory else None
        who = (pl.short.split(".", 1)[-1] if pl and pl.short else "he").upper() if pl else "he"
        pos = pl.position if pl else "guy"
        lines: list[tuple[str, str | None, str]] = []
        if tag in _LINES and team:
            glad, sad = _LINES[tag]
            other = home if team == away else away
            lines.append((f"r/{TEAMS.get(team, ('', '', team))[2]}", team, rng.choice(glad)))
            lines.append((f"r/{TEAMS.get(other, ('', '', other))[2]}", other, rng.choice(sad)))
            if rng.random() < 0.7:
                lines.append(("r/nfl", None, rng.choice(glad + sad)))
        elif rng.random() < 0.12:
            lines.append(("r/nfl", None, rng.choice(_NEUTRAL)))
        said = {l.text for l in list(self.box.lines.get(p.game_id, ()))[-8:]}
        for i, (src, crowd, text) in enumerate(lines):
            if text.format(who=who, pos=pos) in said:         # a crowd repeats itself, but not back to back
                continue
            self.box.push(ChatterLine(f"{p.play_id}:{i}", p.game_id, src, rng.choice(_HANDLES),
                                      text.format(who=who, pos=pos), crowd))
