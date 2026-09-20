# RETRO//FFB

A cyberpunk fantasy football threat console.

Watches every live NFL game, ranks every play by its impact on *your*
fantasy matchup, and renders the ones that matter as lo-res neon tactical
diagrams — with holographic projections of the players who are currently
winning or losing you the week.

Built for a private 12-person Yahoo league. Everyone opens the same console
and sees it oriented around their own matchup.

**Status:** design complete (Omarchy theme alignment added, DESIGN §3a); build in progress.

→ **[docs/DESIGN.md](docs/DESIGN.md)** — the full design: screen, ghost
system, play grammar, data contracts, build order.

## Quick orientation

- Real 10Hz NFL tracking data is unobtainable. The lo-res aesthetic makes
  that irrelevant — see DESIGN §2.
- Phases 0–7 of the build need **no API credentials**. Real nflverse play
  data (public file downloads) behind stubbed providers, plus a synthetic
  league. Yahoo and ESPN adapters land in Phase 8.
- `SIM SUNDAY` — a replay clock that fires canned plays on a simulated
  timeline — is the demo spine and the permanent test harness.
