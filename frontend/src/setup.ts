// [Y] YAHOO LEAGUE: connect a fantasy league without a terminal (issue #26).
// Three steps, each one a state the server reports: keys saved, signed in, league picked.
// The secret goes up once and never comes back down.

const $ = (id: string) => document.getElementById(id)!;
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

interface League { league_key: string; name: string; teams?: string; team_key?: string; team_name?: string }
interface Status {
  app: boolean; client_id: string | null; from_env: boolean; redirect_uri: string; signed_in: boolean;
  league: (League & { season: number }) | null; stale_league: League | null; season: number;
  error: string | null; loaded: { name: string; week: number; kind: string } | null; reloading: boolean;
  links: { access: string; create: string }; leagues?: League[]; auth_url?: string;
}

export class Setup {
  private st: Status | null = null;
  private note = "";                       // the last thing that went wrong, or a hint
  private busy = "";
  private picking = false;                 // SWITCH LEAGUE: show the list even with a league chosen

  get open(): boolean { return !$("setup").hidden; }

  async show(): Promise<void> {
    $("setup").hidden = false;
    this.note = ""; this.picking = false;
    await this.call("GET", "");
  }

  close(): void { $("setup").hidden = true; }

  /** The engine finished reloading around a new league (or failed to). */
  reloaded(error: string | null): void {
    if (!this.open) return;
    this.note = error ?? "";
    void this.call("GET", "");
  }

  private async call(method: "GET" | "POST", path: string, body?: object, busy = ""): Promise<void> {
    this.busy = busy; if (busy) this.render();
    try {
      const r = await fetch(`/api/league${path}`, {
        method, headers: body ? { "content-type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
      });
      const j = await r.json() as Status;
      if (this.st?.leagues && !j.leagues && j.signed_in) j.leagues = this.st.leagues;
      this.st = j;
      if (!r.ok) this.note = j.error ?? `setup said ${r.status}`;
      else if (path) this.note = "";
    } catch {
      this.note = "the console's server didn't answer";
    }
    this.busy = "";
    this.render();
  }

  private render(): void {
    const s = this.st, el = $("setup");
    if (!s) { el.innerHTML = `<h2><span>YAHOO LEAGUE</span><em>[ESC]</em></h2><p class="dim">…</p>`; return; }
    const step = !s.app ? 1 : !s.signed_in ? 2 : (!s.league || this.picking) ? 3 : 4;
    const dots = [1, 2, 3].map((n) => `<b class="${n < step ? "done" : n === step ? "on" : ""}">${n < step ? "■" : n === step ? "▸" : "□"} ${["KEYS", "SIGN IN", "LEAGUE"][n - 1]}</b>`).join("");
    let body = "";
    if (step === 1) body = this.keys(s);
    else if (step === 2) body = this.signIn(s);
    else if (step === 3) body = this.pick(s);
    else body = this.connected(s);
    const note = this.busy ? `<p class="note alert">${esc(this.busy)}</p>` : this.note ? `<p class="note them">${esc(this.note)}</p>` : "";
    el.innerHTML = `<h2><span>YAHOO LEAGUE</span><em data-close>[ESC]</em></h2><nav>${dots}</nav>${body}${note}`;
    el.querySelector<HTMLElement>("[data-close]")!.onclick = () => this.close();
    this.wire(s);
  }

  private keys(s: Status): string {
    return `<div class="copy">
      <p>Yahoo only lets reviewed apps read fantasy leagues, so you make your own app once. It's free and read-only.</p>
      <ol>
        <li>Ask Yahoo for Fantasy API access at <a href="${s.links.access}" target="_blank" rel="noopener">${esc(s.links.access.replace("https://", ""))}</a>. Personal use is fine.</li>
        <li>Create an app at <a href="${s.links.create}" target="_blank" rel="noopener">${esc(s.links.create.replace("https://", ""))}</a>:
          client type <i>Confidential</i>, API permission <i>Fantasy Sports: Read</i>, redirect URI
          <code data-copy="${esc(s.redirect_uri)}" title="click to copy">${esc(s.redirect_uri)}</code></li>
        <li>If Yahoo asks for the new app's Client ID to finish your access request, send it. Yahoo turns the app on after that, which can take a few days.</li>
        <li>Paste the app's keys here. They stay on this computer.</li>
      </ol></div>
      <form data-form="app">
        <label>CLIENT ID<input name="client_id" autocomplete="off" spellcheck="false" placeholder="dj0yJmk9…"></label>
        <label>CLIENT SECRET<input name="client_secret" type="password" autocomplete="off" spellcheck="false"></label>
        <button>SAVE KEYS ▸</button>
      </form>`;
  }

  private signIn(s: Status): string {
    return `<div class="copy">
      <p>Open Yahoo's sign-in and allow read access. Yahoo then sends you to <code>${esc(s.redirect_uri)}?code=…</code>
      and that page won't load. That part is expected: copy the whole address from the address bar and paste it below.</p></div>
      <p><a class="btn" data-signin href="#">OPEN YAHOO SIGN-IN ▸</a></p>
      <form data-form="signin">
        <label>ADDRESS YAHOO SENT YOU TO<input name="pasted" autocomplete="off" spellcheck="false" placeholder="https://localhost:8443/callback?code=…"></label>
        <button>CONNECT ▸</button>
      </form>
      <p class="small dim">APP ${esc(s.client_id ?? "")}${s.from_env ? " · FROM .ENV" : ""} · <a href="#" data-forget>FORGET KEYS</a></p>`;
  }

  private pick(s: Status): string {
    if (!s.leagues) {
      if (!this.busy) void this.call("GET", "/leagues", undefined, "ASKING YAHOO FOR YOUR LEAGUES …");
      return `<p class="dim">…</p>`;
    }
    const rows = s.leagues.map((l) => `<li data-l="${esc(l.league_key)}" class="${l.league_key === s.league?.league_key ? "cur" : ""}">
        <span class="hot">${esc(l.name)}</span><span class="dim">${l.teams ? esc(l.teams) + " TEAMS" : ""}</span>
        <span class="you">${l.team_name ? "YOU: " + esc(l.team_name) : ""}</span></li>`).join("");
    return `<div class="copy"><p>Pick the league to follow. The console shows your matchup in it, and you can still watch any other team's with [V].</p></div>
      ${rows ? `<ul class="leagues">${rows}</ul>` : `<p class="them">This Yahoo login has no NFL leagues for ${s.season}.</p>`}
      ${s.league ? `<p class="small"><a href="#" data-back>◂ KEEP ${esc(s.league.name)}</a></p>` : ""}
      <p class="small dim"><a href="#" data-signout>SIGN OUT</a></p>`;
  }

  private connected(s: Status): string {
    const lg = s.league!;
    const state = s.reloading ? `<p class="alert">LOADING YOUR LEAGUE …</p>`
      : s.loaded?.kind === "yahoo" ? `<p class="gain">● ON THE CONSOLE · WEEK ${s.loaded.week}${lg.team_name ? " · YOU ARE " + esc(lg.team_name) : ""}</p>`
      : s.error ? `<p class="them">${esc(s.error)}</p><p><a class="btn" href="#" data-retry>TRY AGAIN ▸</a></p>`
      : `<p class="dim">Not on screen right now: this game day is from another season. It comes back with this season's games.</p>`;
    return `<div class="league"><div class="hot big">${esc(lg.name)}</div><div class="you">${esc(lg.team_name ?? "")}</div></div>
      ${state}
      <p class="small dim">[X] TURNS THE FANTASY LAYER ON AND OFF · [V] WATCH AS ANOTHER TEAM · [TAB] LINEUP / LEAGUE / CHATTER</p>
      <p class="small dim"><a href="#" data-switch>SWITCH LEAGUE</a> · <a href="#" data-signout>SIGN OUT</a> · <a href="#" data-forget>FORGET KEYS</a>
      · APP ${esc(s.client_id ?? "")}${s.from_env ? " (.ENV)" : ""}</p>`;
  }

  private wire(s: Status): void {
    const el = $("setup");
    const on = (sel: string, f: () => void) => el.querySelectorAll<HTMLElement>(sel).forEach((x) => (x.onclick = (e) => { e.preventDefault(); f(); }));
    el.querySelectorAll<HTMLElement>("[data-copy]").forEach((c) => (c.onclick = () => {
      void navigator.clipboard?.writeText(c.dataset.copy!); this.note = ""; c.classList.add("copied");
    }));
    const app = el.querySelector<HTMLFormElement>("form[data-form=app]");
    if (app) {
      app.onsubmit = (e) => {
        e.preventDefault();
        const f = new FormData(app);
        void this.call("POST", "/app", { client_id: f.get("client_id"), client_secret: f.get("client_secret") }, "SAVING …");
      };
      (app.querySelector("input") as HTMLInputElement).focus();
    }
    const sign = el.querySelector<HTMLFormElement>("form[data-form=signin]");
    if (sign) sign.onsubmit = (e) => {
      e.preventDefault();
      void this.call("POST", "/signin", { pasted: new FormData(sign).get("pasted") }, "SIGNING IN AND ASKING FOR YOUR LEAGUES …");
    };
    on("[data-signin]", async () => {
      const r = await fetch("/api/league/signin"); const j = await r.json();
      if (j.auth_url) window.open(j.auth_url, "_blank", "noopener");
      else { this.note = j.error ?? "no sign-in address"; this.render(); }
      el.querySelector<HTMLInputElement>("input[name=pasted]")?.focus();
    });
    el.querySelectorAll<HTMLElement>("li[data-l]").forEach((li) => (li.onclick = () => {
      this.picking = false;
      void this.call("POST", "/choose", { league_key: li.dataset.l }, "LOADING YOUR LEAGUE …");
    }));
    on("[data-switch]", () => { this.picking = true; this.render(); });
    on("[data-back]", () => { this.picking = false; this.render(); });
    on("[data-retry]", () => void this.call("POST", "/retry", {}, "ASKING YAHOO AGAIN …"));
    on("[data-signout]", () => { if (s.leagues) delete s.leagues; void this.call("POST", "/signout", {}, "SIGNING OUT …"); });
    on("[data-forget]", () => void this.call("POST", "/forget", {}, "FORGETTING THE KEYS …"));
  }
}
