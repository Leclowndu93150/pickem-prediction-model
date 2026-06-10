# Pickem Prediction Model

A Monte-Carlo pickem predictor for Counter-Strike Major Swiss stages,
built on top of a Python wrapper for the HLTV mobile API.

The library is reverse-engineered from the official HLTV Android app
(`org.hltv.android`). It impersonates a mobile-app TLS fingerprint with
`curl_cffi` so requests are not rejected with HTTP 403.

Two packages live in this repo:

- `hltv_api/` - typed wrapper around ~67 mobile-API endpoints, with a
  disk cache and optional rotating proxy pool.
- `pickem/` - Swiss-format Monte-Carlo simulator and ticket optimizer
  for the HLTV Major pickem challenge (2x 3-0, 6x advance, 2x 0-3).

## Results so far

### Backtest

Running the model at the pickem deadline of each event (cutoff = first
match start time), then scoring the top ticket against the actual
Swiss final records:

| Event | Model | VRS baseline | Rank baseline | Cleared 5/10 |
|---|---:|---:|---:|---|
| PGL Astana 2026 (backtest) | 6/10 | 6/10 | 7/10 | yes |
| IEM Cologne Major 2026 Stage 1 (live) | 7/10 | 6/10 | 5/10 | yes |
| IEM Cologne Major 2026 Stage 2 (live) | 7/10 | 5/10 | 5/10 | yes |
| **Average** | **6.7/10** | **5.7/10** | **5.7/10** | 3/3 |

Baselines:

- **VRS:** top-2 by VRS points -> 3-0, next 6 -> advance, bottom 2 -> 0-3.
- **Rank:** same idea using HLTV world rank.

Notes and caveats:

- The Cologne Stage 1 and Stage 2 numbers are from **live tickets
  actually submitted on HLTV** before the pickem deadlines, not from
  the backtest script. The submitted Stage 2 ticket scored 7/10; the
  cache used in the bundled backtest reproduces 5/10 because the
  carryover-records handling slightly differs from how the live
  prediction was run. PGL Astana 2026 is a pure backtest.
- 3 events is a tiny sample. The model beats both baselines by
  about 1/10 on average. Real significance would need 20+ stages.
- All three events are within ~5 weeks of "today" (June 2026), so
  today's HLTV team/player data is a reasonable proxy for the team
  data that existed at each cutoff. Backtests on older events
  (Austin Major 2025, IEM Cologne 2025) showed worse numbers,
  mostly because rosters and rankings have drifted since.
- A blend-weight sweep (`pure_elo`, `vrs_only_*`, `small_blends`,
  `tiny_blends`) produced the **same** correct counts on these 3
  events. The blends mostly affect the optimizer's confidence
  (`P(>=5)`), not which ticket it picks for this small sample.

### Stage 3 prediction (IEM Cologne Major 2026, R1 begins 2026-06-11)

Top ticket from 20,000 Monte-Carlo runs with the Stage 2 carryover
records folded in:

| Slot | Team | Marginal P |
|---|---|---:|
| 3-0 | Vitality | 38% |
| 3-0 | Spirit | 38% |
| advance | Falcons | P(3-1 or 3-2) = 54% |
| advance | Natus Vincere | 58% |
| advance | FURIA | 51% |
| advance | Aurora | 51% |
| advance | G2 | 47% |
| advance | The MongolZ | 36% |
| 0-3 | 9z | 36% |
| 0-3 | B8 | 32% |

Model says `P(>=5 correct) = 46.5%`, `E[correct] = 4.4 / 10`. The
distribution: 24% chance of exactly 5 correct, 14% of 6, 6% of 7,
2% of 8.

Reproduce with:

```bash
HLTV_CACHE_DIR=cache python3 stage3.py --cache-dir cache --n-sims 20000
```

### Performance note

The full simulation pipeline (build strengths -> Monte Carlo -> ticket
optimization) takes **roughly 10 minutes per event** at 20,000 sims
when running against the bundled cache. Most of that time is in the
ticket enumeration (`pickem/optimize.best_tickets`) rather than the
Swiss simulator itself, because every candidate ticket is scored
against every saved sim.

If you want a faster turnaround during exploration, drop `--n-sims`
to 5,000-10,000. The optimal ticket is usually identical; only the
confidence numbers move.

## Glossary

Counter-Strike, statistics, and CS-specific HLTV terms used throughout
this repo.

### Competitive format

- **Major.** Valve-sponsored Counter-Strike championship. 32 teams
  total across three Swiss stages plus an 8-team playoff bracket. The
  most prestigious tournament in CS.
- **Swiss bracket.** Tournament format where teams play opponents
  with the same win-loss record each round. Three wins advance, three
  losses eliminate. No team plays the same opponent twice in the same
  stage.
- **BO1 / BO3.** Best-of-1 (single map) and best-of-3 (first team to
  win 2 maps). In Stages 1 and 2, advancement and elimination matches
  (records 2-x or x-2) are BO3 and everything else is BO1. In Stage 3,
  every match is BO3.
- **Initial seed.** Pre-event team ordering used by the Swiss
  pairing rules. Comes from a team's position in the global VRS.
- **R1 pairing.** First-round pairing, fixed by Valve's rulebook:
  seed 1 vs seed 9, 2 vs 10, ..., 8 vs 16.
- **Buchholz score (Difficulty Score).** Sum of wins minus losses of
  every opponent a team has already faced. Used to break ties when
  two teams have the same W-L record. Higher Buchholz = harder
  schedule = higher seed.
- **Veto.** Map pick/ban process before a series. For BO3 the
  standard Major sequence is `A ban -> B ban -> A pick -> B pick ->
  A ban -> B ban -> decider`, where the loser of the last surviving
  ban picks side on the decider.

### Pickem

- **Pickem.** HLTV's prediction game for the Major. Each player
  submits one ticket per stage.
- **Ticket.** A set of 10 picks for a stage: 2 teams to go 3-0,
  6 teams to "advance" (finish 3-1 or 3-2), and 2 teams to go 0-3.
  A team placed in the advance slot that goes 3-0 instead counts
  as **wrong**.
- **Medal threshold.** Getting at least 5 of the 10 picks correct
  clears the threshold and earns the stage's diamond coin.
- **P(>=5).** The model's estimated probability that the chosen
  ticket gets at least 5 picks correct. The optimizer maximizes this
  by default.
- **EV (expected value).** Average HLTV-scoring points the ticket
  would earn across all simulated brackets. HLTV scoring is
  5 / 5 / 2 points per correct 3-0 / 0-3 / advance pick (max 32).

### Rating systems and signals

- **HLTV world rank.** HLTV's official 1-N team ranking. Hand-curated
  by HLTV editors using results, head-to-head, lineup stability, and
  similar inputs. Updated weekly.
- **VRS (Valve Regional Standings).** Valve's own algorithmic team
  ranking, published as a points score (typically 0-2500, higher is
  better) plus a regional position. Used to allocate Major invites
  and to seed Major stages. The model uses both the global VRS
  position and the latest VRS points.
- **VRS forecast.** Block on each HLTV match payload showing how
  many points each side would gain/lose under several outcomes.
  Treated here as HLTV's own implied win-probability signal.
- **Rating 3.0.** HLTV's per-player performance rating (typical
  range 0.80-1.40). Combines K/D, multikills, KAST, ADR, opening
  duels, and impact rounds.
- **Rating trend.** "Rising" / "Falling" / "Stable" flag on each
  player's stats block. Comparing the player's recent month to
  their season average.
- **3-month rating (`ratingPast3Months`).** The player's Rating 3.0
  averaged over the last 90 days. More responsive to recent form
  than the season-long rating.
- **Firepower, opening, clutching, utility, ...** HLTV's per-player
  role scores (0-100). Each captures a different aspect of how the
  player produces value (entry kills, opening duels, 1-vs-N clutches,
  etc.).
- **Map pool / map comfort.** Per-team statistics on each active
  competitive map: win rate, CT/T side balance, how often the team
  picks vs bans the map. Combined into a "comfort" score during BO3
  simulation so the veto picks plausibly favorable maps.
- **H2H (head-to-head).** Direct history between two teams. Smoothed
  by a Bayesian prior so 2-0 doesn't look like a 100% lock when only
  two games have been played.

### Modeling

- **Elo.** Rating system that gives every team a score; the gap in
  scores converts to a win probability via a logistic formula
  (`P(A wins) = 1 / (1 + 10**((R_B - R_A) / 400))`). Originally from
  chess; we use it for map-level CS results.
- **Glicko-style margin multiplier.** Modification to plain Elo that
  scales each rating update by the score margin. A 16-3 stomp moves
  Elo more than a 16-14 squeaker. Named after the Glicko system that
  popularized this idea.
- **K-factor.** Knob on Elo that controls how fast ratings move.
  Higher K = faster reaction to new results but more noise. We use
  K=24 per map.
- **Bayesian prior.** Default belief assumed in the absence of data.
  For H2H we start at 50/50 with a prior "weight" of 2 phantom games,
  so a 2-0 record only nudges the prior to ~67%, not 100%.
- **Monte Carlo simulation.** Repeated random trials. We simulate
  the full 16-team Swiss bracket tens of thousands of times, sampling
  every match outcome from the matchup model, and tally how often
  each team finished 3-0, 3-1, 3-2, etc.
- **Marginal probability.** Per-team probability of a given outcome,
  averaged over all simulations. "Vitality goes 3-0 in 38% of sims"
  is a marginal.
- **Joint expectation.** Score of a ticket computed by replaying the
  saved per-simulation outcomes, not by multiplying marginals. This
  correctly handles correlations like "two teams that meet in the
  bracket can't both 3-0."
- **Cutoff.** Datetime at which the model freezes its inputs. Used
  to prevent look-ahead leakage when backtesting: no data published
  after the cutoff feeds the model.
- **Calibration.** Whether the model's claimed probabilities match
  reality. A well-calibrated 30% forecast is right 30% of the time.
- **Backtest.** Re-running the model against historical events whose
  outcomes are already known, scoring its tickets against actuals.
- **Baseline.** A trivially simple alternative model used for
  comparison. We compare against a VRS baseline (top-2 to 3-0, etc.)
  and a rank baseline.
- **Blend weight.** Mixing weight between the Elo win probability
  and a side signal (form / H2H / VRS forecast / map pool). A
  `vrs_blend=0.12` means the final probability is 88% Elo + 12% VRS.
- **Carryover.** When a team qualifies from Stage 1 to Stage 2 (or
  Stage 2 to Stage 3) with a 3-0, 3-1, or 3-2 record, the model
  treats that record as evidence of strength entering the next
  stage (a small Elo boost and a form floor).

### Infrastructure

- **`curl_cffi`.** Python HTTP client that impersonates browser TLS
  fingerprints. Needed because HLTV rejects "plain" Python clients
  with HTTP 403.
- **Proxy pool.** Rotating list of HTTP proxies. The client picks
  one per request, cools down proxies that fail, and falls back to
  the least-recently-failed proxy under heavy load.
- **Disk cache.** Local JSON file per HLTV response, keyed on
  `(method, path, sorted params)`. Each entry has a per-endpoint
  TTL so live feeds refresh quickly while finished-event data stays
  cached forever.
- **Bulk fetch.** `bulk_teams` / `bulk_players` / `bulk_matches`
  run cache-aware parallel fetches over a thread pool (128 workers
  when a proxy pool is configured).

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/Leclowndu93150/pickem-prediction-model.git
cd pickem-prediction-model
pip install -r requirements.txt
```

`requirements.txt`:

```
curl_cffi>=0.14
fastapi>=0.128
numpy>=2.0
pydantic>=2.0
uvicorn[standard]>=0.40
```

`fastapi`, `pydantic`, and `uvicorn` are only needed if you want to
serve the pickem predictor as a web API. The core client works with
just `curl_cffi` (plus `numpy` for the ticket optimizer).

## Quickstart: the API client

```python
from hltv_api import HLTVClient

client = HLTVClient()

# Typed accessors return rich objects:
team = client.get_team(7020)             # Spirit
print(team.name, team.world_rank, [p.nick for p in team.roster])

match = client.get_match(2394156)
print(match.team1_name, match.team1_score, "-", match.team2_score, match.team2_name)
for m in match.maps:
    print(" ", m.name, m.team1_score, m.team2_score)

# Raw accessors return the unparsed JSON if you need it:
raw = client.event(9028)
```

Roughly every public, no-auth endpoint of the mobile API is wrapped.
See `hltv_api/client.py` for the full surface (frontpage, matches,
events, teams, players, rankings, search, forum, fantasy, articles).

### Caching

GET responses are cached on disk so re-running scripts doesn't hammer
the API. Default location is `~/.cache/hltv`.

The repo ships with a pre-seeded `cache/` directory containing every
team, player, and match needed to run the model against the current
IEM Cologne Major 2026 (event 8301, Stages 1 and 2 played, Stage 3
upcoming) and two backtest targets (IEM Atlanta 2026 and PGL Astana
2026). If you don't have proxies and don't want to hit HLTV, point
the client at the bundled cache:

```python
from hltv_api import HLTVClient, DiskCache
client = HLTVClient(cache=DiskCache(base_dir="cache"), proxy_pool=False)
```

The CLIs auto-detect a `./cache` directory via the `HLTV_CACHE_DIR`
environment variable:

```bash
HLTV_CACHE_DIR=cache python -m pickem.run --event-id 9028 --cutoff 2026-06-02T10:30:00Z
```

To refresh or extend the bundled cache (needs proxies for any volume
of fetching):

```bash
python prefetch.py --cache-dir cache --workers 128
```

```python
from hltv_api import HLTVClient, DiskCache

# Use a custom cache directory
client = HLTVClient(cache=DiskCache(base_dir="/var/cache/hltv"))

# Disable the cache entirely
client = HLTVClient(cache=False)

# Inspect cache stats
print(client.cache.stats)   # {"hits": ..., "misses": ..., "writes": ...}
```

Per-endpoint TTLs are set in `hltv_api/cache.py`:

- Live feeds (frontpage, matches): 60 seconds
- Event details: 1 hour
- Finished events feed, articles, ranking: 1 day

### Proxy support

If HLTV starts rate-limiting you, point the client at a pool of HTTP
proxies. The pool round-robins, cools down failed proxies for 60
seconds, and falls back to the least-recently-failed proxy when
nothing is healthy.

Supported proxy line formats:

```
host:port
host:port:username:password
user:pass@host:port
http://user:pass@host:port
socks5://user:pass@host:port
```

Lines starting with `#` and blank lines are ignored.

Pass a `ProxyPool` explicitly:

```python
from hltv_api import HLTVClient
from hltv_api.proxy import ProxyPool

pool = ProxyPool.from_file("proxies.txt")
client = HLTVClient(proxy_pool=pool)
```

Or drop a proxy list at `~/.config/hltv/webshare_proxies.txt` and it
will be picked up automatically:

```python
client = HLTVClient()   # auto-loads ~/.config/hltv/webshare_proxies.txt if present
```

Disable auto-loading:

```python
client = HLTVClient(proxy_pool=False)
```

Pool statistics:

```python
print(client.proxy_pool.stats)
# {"size": 50, "uses": 1024, "failures": 3, "cooling_down": 1}
```

### Authenticated requests

For the few endpoints that need a logged-in session (fantasy team
edits, forum posting, user settings):

```python
client = HLTVClient(session_id="<PHPSESSID>", autologin="<autologin>")
# or:
client.login("username", "password")
```

The session cookies are stored on the underlying `curl_cffi` session.

## Pickem predictor

`pickem/` simulates a 16-team Major Swiss stage and finds the highest
expected-value pickem ticket. The simulator implements Valve's actual
Swiss rules:

- 3 wins advance, 3 losses eliminate.
- Match format depends on the stage:
  - Stages 1 and 2: advancement and elimination matches (records
    2-x or x-2) are BO3, all other matches are BO1.
  - Stage 3: every match is BO3.
  - The simulator auto-detects the format from the event's `minMaps`
    field in the API, so you don't have to flag the stage manually.
- Round 1 pairs by initial seed: 1v9, 2v10, ..., 8v16.
- Within a record bucket, teams sort by current W-L, then Buchholz
  (sum of opponent W - L), then initial seed. The highest seed plays
  the lowest available seed without producing a rematch.
- In the 6-team round-4+ bucket, the priority table from the Major
  Supplemental Rulebook is applied.

The matchup model layers, grouped by where they enter the pipeline:

**Base Elo seeding** (`_elo_from_rank`):

1. HLTV world rank (`~2000` for #1, drops ~15 per position, floor 1200).
2. VRS points (Valve Regional Standings, centred at 1500).

**Pre-map adjustments to base Elo** (`build_team_strengths`):

3. Roster Rating 3.0 mean (`+500 * (avg_rating - 1.00)`).
4. Previous-stage carryover for Stages 2/3 (`+70` Elo for a 3-0
   carry, `+38` for 3-1, `+18` for 3-2).
5. Big-event pedigree: time-decayed placement history at past
   LANs, tier-weighted (Major > IEM/PGL/BLAST/ESL > qualifier), capped
   at `+55 / -20` Elo.
6. Player trend: rising vs falling roster share from
   `PlayerStats.ratingTrend` (`+30 * net`).
7. 3-month rating delta: `+250 * (3mo_avg - season_avg)`.
8. Post-match style stats (opening kills, multi-kills, pistol rounds,
   flash assists, clutches). Off by default behind a flag; the
   aggregate is not yet predictive enough.

**Margin-aware Elo map replay** (`_walk_maps_for_elo`):

9. Glicko-style margin multiplier applied to every pre-cutoff map.
   16-3 stomps move Elo more than 16-14 squeakers; OT losses barely
   move it.

**Recent-form signal** (`_recent_form_*`):

10. Exponentially-weighted recent W/L. Preferred source is the
    MatchScreen `formMatches` block (snapshot at match time, avoids
    leaking today's roster into old backtests); falls back to
    `team.recentMatches`.
11. Form is floored if the team carried into the stage with a
    qualifying record (3-0 -> 0.85, 3-1 -> 0.72, 3-2 -> 0.62).

**Per-matchup blend** (`matchup_p_bo1`):

12. H2H Bayesian prior (smoothed with a 0.5 prior, weight 2.0).
13. VRS forecast prior pulled from each team's latest match
    payload (HLTV's own implied model).
14. Per-map win-rate edge using `_map_comfort`: combines win rate,
    played count, pick%, ban%, and CT/T side balance, shrunk toward
    0.5 on low samples.

**BO3 simulation with veto** (`simulate_bo3_with_veto`):

15. Realistic Major veto: A ban -> B ban -> A pick -> B pick ->
    A ban -> B ban -> decider. Each team bans the opponent's
    strongest map and picks its own strongest remaining map.
16. The BO3 series is played map-by-map, with `matchup_p_bo1`
    re-evaluated per map using the team's per-map comfort, so a
    favourite on Inferno but underdog on Nuke gets that mix
    correctly.

**Simulator inputs** (`simulate_swiss_with_sims`):

17. Exact initial stage seeds from HLTV's web simulator when
    available, otherwise derived from Elo.
18. Actual Round 1 pairings from the API (or HLTV's web simulator)
    when published, otherwise random within the 0-0 bucket.
19. Buchholz tiebreak (sum of opponent W - L) and the 6-team
    priority table from Valve's Major Supplemental Rulebook.

### CLI

```bash
python -m pickem.run --event-id <id> --cutoff 2026-06-02T10:30:00Z --n-sims 50000
```

`--cutoff` is the pickem deadline. Only matches that started before
this timestamp feed the model, so backtests don't peek at future
results. If the event is finished, the top ticket is also scored
against the actual final records.

JSON output (for piping into a frontend):

```bash
python -m pickem.json_run --event-id <id> --n-sims 30000
```

### As a library

```python
from datetime import datetime, timezone
from hltv_api import HLTVClient
from pickem.model import build_team_strengths
from pickem.swiss import simulate_swiss_with_sims
from pickem.optimize import best_tickets

client = HLTVClient()
event = client.get_event(9029)
cutoff = datetime(2026, 6, 6, 10, 30, tzinfo=timezone.utc)

strengths = build_team_strengths(client, event.team_ids, cutoff)
records, sims = simulate_swiss_with_sims(strengths, n_sims=50_000, seed=42)
tickets = best_tickets(records, sims=sims, top_k=5, objective="p_at_least_5")

for t in tickets:
    print(t)
```

The optimizer scores tickets against the saved per-sim outcomes (not
marginal probabilities), so it correctly handles correlations like
"two teams that meet in the bracket can't both go 3-0."

### Web API

A FastAPI app is included for serving predictions:

```bash
uvicorn pickem.api:app --reload
```

Endpoints:

- `GET /events/{event_id}/status` - current readiness, team list, R1 pairings.
- `POST /events/{event_id}/predict` - kick off a background prediction job.
- `POST /events/{event_id}/predict/sync` - run synchronously and return the result.
- `GET /jobs/{job_id}` - poll a background job.

## Probes

`probe.py` exercises every read-only raw endpoint; `probe_typed.py`
does the same for the typed accessors. Useful for smoke-testing that
HLTV hasn't changed shapes:

```bash
python probe_typed.py
python probe.py probe_report.json
```

## Layout

```
hltv_api/
  client.py     # HTTPS client, ~67 endpoints, retry + proxy + cache
  cache.py      # disk JSON cache with per-endpoint TTLs
  models.py     # typed wrappers (Team, Player, Match, Event, ...)
  derived.py    # Elo, form, H2H, map-pool, role-balance helpers
  proxy.py      # rotating proxy pool

pickem/
  model.py            # TeamStrength + Elo + matchup probabilities
  swiss.py            # Monte Carlo Swiss simulator
  optimize.py         # numpy-vectorised ticket scorer
  run.py              # CLI runner
  api.py              # FastAPI service
  json_run.py         # JSON CLI
  service.py          # core predict_event / event_status logic
  history.py          # archive completed events to data/events/
  archive_event.py    # CLI: archive a single event
  simulator_config.py # load exact seeds/pairings from HLTV's web simulator
```

## Notes

This is an unofficial reverse-engineered client. HLTV hasn't published
the mobile API, and the endpoint shapes can change without warning.
The TLS impersonation and User-Agent rotation are needed because the
public endpoints reject anything that doesn't look like a real mobile
app.

Be a good citizen: cache aggressively, throttle bulk fetches, and
don't redistribute the cached payloads.

## License

MIT. See `LICENSE`.
