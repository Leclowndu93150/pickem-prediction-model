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

The matchup model layers:

1. Elo seeded from world rank + VRS points.
2. Roster Rating 3.0 mean.
3. Margin-aware Elo replay of every pre-cutoff map (Glicko-style
   margin multiplier).
4. Recent-form exponential decay (legacy signal).
5. H2H Bayesian prior.
6. VRS forecast prior from the latest match payload.
7. Per-map win-rate edge for BO3 simulation with realistic veto.

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
