# Pickem Prediction Model

Forecasts for the Swiss stages of Counter-Strike Majors, built to pick a ticket for Valve's Major Pick'Em (two 3-0 teams, six teams that advance 3-1 or 3-2, two 0-3 teams).

The model rates every player from recent matches, builds each team from the five players it will actually field, plays the match-ups in a round-by-round match engine, simulates the Swiss stage 100,000 times with Valve's pairing rules, and chooses the ticket with the highest chance of five or more correct picks.

## Current forecast: PGL Major Singapore 2026, Stage 1

Made on 2026-11-20 with matches up to 2026-11-19. Stage 1 starts on 2026-11-24. Full output: [`predictions/pgl-major-singapore-2026-stage-1.md`](predictions/pgl-major-singapore-2026-stage-1.md).

| Slot | Teams |
|---|---|
| 3-0 | FaZe, 100 Thieves |
| Advance (3-1 or 3-2) | Ninjas in Pyjamas, PARIVISION, HEROIC, Liquid, Eternal Fire, 9z |
| 0-3 | Wildcard, Chinggis Warriors |

The model gives this ticket a 41% chance of five or more correct, and expects 4.2 of 10. Picking straight down the seeding gets 35% under the same simulations. The second 3-0 slot is close between 100 Thieves and PARIVISION, and the last advance slot between 9z, DENDELE and M80.

| Seed | Team | 3-0 | 3-1 | 3-2 | Advance | 2-3 | 1-3 | 0-3 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 4 | FaZe | 27% | 28% | 21% | **76%** | 14% | 8% | 2% |
| 2 | PARIVISION | 23% | 30% | 22% | **75%** | 14% | 8% | 3% |
| 7 | 100 Thieves | 24% | 28% | 21% | **73%** | 15% | 9% | 2% |
| 5 | Liquid | 21% | 28% | 23% | **72%** | 15% | 10% | 3% |
| 1 | Ninjas in Pyjamas | 22% | 28% | 22% | **72%** | 16% | 9% | 3% |
| 3 | HEROIC | 22% | 27% | 22% | **71%** | 16% | 10% | 3% |
| 6 | Eternal Fire | 14% | 23% | 23% | **60%** | 20% | 15% | 5% |
| 8 | 9z | 10% | 19% | 21% | **51%** | 23% | 19% | 7% |
| 10 | DENDELE | 8% | 18% | 22% | **48%** | 22% | 20% | 10% |
| 9 | M80 | 8% | 18% | 21% | **47%** | 22% | 21% | 10% |
| 13 | BC.Game | 6% | 15% | 20% | **41%** | 22% | 23% | 14% |
| 14 | FlyQuest | 5% | 12% | 18% | **36%** | 23% | 25% | 16% |
| 16 | Lynn Vision | 5% | 9% | 15% | **29%** | 23% | 27% | 21% |
| 11 | Imperial | 4% | 10% | 15% | **29%** | 24% | 29% | 18% |
| 12 | Wildcard | 1% | 4% | 8% | **14%** | 19% | 34% | 33% |
| 15 | Chinggis Warriors | 1% | 2% | 4% | **6%** | 13% | 32% | 49% |

Stages 2 and 3 and the playoffs get their forecasts once their fields are set (see [Usage](#usage)).

## How accurate is it

Short version: top-level Counter-Strike is hard to predict. The model picks the winner of a series between top-50 teams about 61% of the time, the same as a forecast from ranking points, with slightly better probabilities. A good Pick'Em ticket in a Major stage has a 30-45% chance of reaching five correct, and the model says so.

Every number below is out of sample: ratings use only matches played before the match or stage being predicted.

### Single series

Every series from 2026-05-01 to 2026-11-19, predicted with ratings rebuilt each week.

| Forecast | Top-50 teams (829 series) | | Top-100 teams (1,830 series) | |
|---|---:|---:|---:|---:|
| | Log-loss | Winner picked | Log-loss | Winner picked |
| Coin flip | 0.693 | 50.0% | 0.693 | 50.0% |
| HLTV rank, scale fitted on the same matches | 0.661 | 59.3% | 0.662 | 61.0% |
| Valve ranking points, scale fitted on the same matches | 0.659 | 61.9% | 0.650 | 63.0% |
| This model | 0.660 | 60.7% | 0.642 | 63.0% |

The two ranking rows had their scale fitted on the matches they are scored on, which flatters them. With the scale fitted only on earlier matches, ranking points score 0.668 (top 50) and 0.652 (top 100) against 0.665 and 0.641 for the model on the same held-out matches. Blending the model with ranking points or HLTV rank did not improve it.

The model's probabilities are honest. Across top-100 series, favourites it rated 50-60% won 55.0% (predicted 54.9%), 60-70% won 67.1% (64.4%), 70-80% won 74.9% (74.3%) and 80-90% won 87.5% (83.4%).

### How far back to look

Rosters change, so the tempting choice is a short window. The data says otherwise. Because ratings are per player and a team is built from its current five, roster changes are already handled, and a longer window simply measures each player better.

Weight is full for the first number of days and falls to zero at the second. Log-loss on the 829 top-50 series:

| Window | Log-loss |
|---|---:|
| 30 → 180 days (used) | 0.658 |
| 180 days, flat | 0.658 |
| 60 → 180 days | 0.661 |
| 90 → 180 days | 0.662 |
| 90 days, flat | 0.664 |
| 30 → 120 days | 0.666 |
| 30 → 90 days | 0.671 |
| 60 days, flat | 0.687 |
| 30 → 60 days | 0.691 |
| 30 days, flat | 0.711 |

One month of data is worse than a coin flip, because a handful of maps per player is mostly noise and the model trusts it. The 180-day rows differ by less than the run-to-run noise (about 0.002). The 180-day rows and 30 → 90 days are the mean of two runs at 1,000 simulations per match; the others are single runs at 300.

### Best-of-ones

The engine is well calibrated on best-of-threes but too timid on best-of-ones: favourites it rated around 59% won 66-70%. Real results show why. A favourite wins a best-of-one about as often as it wins its own map pick in a best-of-three, not as often as an average map, because the veto leaves the underdog nowhere to hide.

| Map (top-100 teams, since December 2025) | Favourite wins at a 100-point ranking gap | At a 300-point gap |
|---|---:|---:|
| Best-of-one | 60.3% | 70.4% |
| Best-of-three, favourite's pick | 60.9% | 69.9% |
| Best-of-three, decider | 55.1% | 62.4% |
| Best-of-three, underdog's pick | 51.8% | 61.4% |

So best-of-one odds are stretched by a factor of 1.6 on the log-odds. The fit on 455 best-of-ones between top-200 teams gives 1.57 ± 0.22. On those best-of-ones the factor lowers log-loss from 0.630 to 0.621; fitted only on earlier matches each time, it still lowers it, to 0.624. It matters in Stages 1 and 2, where the first rounds are best-of-one.

### Whole stages

Fourteen 16-team Swiss stages from February to October 2026, each predicted from its first-round pairings only, with ratings as of the stage start and the lineups that played. Seeds are approximated by HLTV rank. "Seeding" is the ticket that picks straight down that order.

| Stage | Model | Seeding | Model's own expectation |
|---|---:|---:|---:|
| PGL Cluj-Napoca 2026 | 5 | 5 | 4.7 |
| CCT Season 3 Europe Series 16 | 4 | 4 | 3.8 |
| CCT Season 3 Europe Series 17 | 3 | 2 | 3.7 |
| ESL Pro League Season 23 Stage 1 | 2 | 4 | 4.9 |
| ESL Pro League Season 23 Stage 2 | 6 | 5 | 4.4 |
| PGL Bucharest 2026 | 5 | 2 | 4.5 |
| PGL Astana 2026 | 5 | 4 | 4.9 |
| BetBoom Storm Season 3 | 6 | 5 | 4.1 |
| IEM Cologne Major 2026 Stage 1 | 6 | 5 | 3.9 |
| IEM Cologne Major 2026 Stage 2 | 5 | 4 | 4.0 |
| IEM Cologne Major 2026 Stage 3 | 3 | 2 | 3.8 |
| XSE Pro League Guangzhou 2026 | 5 | 4 | 3.9 |
| ESL Pro League Season 24 | 5 | 5 | 5.0 |
| PGL Masters Bucharest 2026 | 3 | 3 | 4.3 |
| **Total** | **63** | **54** | 59.9 |
| **Stages with 5 or more** | **9 of 14** | **5 of 14** | |

Caveats:

- Fourteen stages is a small sample. One pick either way moves a stage across the line.
- The window and the best-of-one factor were chosen on matches from the same months, so this table is a little kinder than a truly blind test.
- With HLTV's official seeds instead of the rank approximation, the three Cologne Major stages come out at 6, 5 and 2 for the model (13) against 4, 7 and 3 for the seeding (14).
- The previous version of this model, a hand-built Elo blend, scored 7, 7 and 5 on those three stages with tickets submitted live, and 6 on a PGL Astana backtest. It needed the HLTV app API, which no longer answers, so it cannot be re-run or tested on other stages.

### Pairing rules

The Swiss simulator was checked against HLTV's record of the Cologne Major: starting from the official seeds and feeding in the real results, it reproduces all 99 real pairings across the three stages (Buchholz order, seed tie-breaks and the six-team priority table included).

```bash
python -m pickem.backtest pairings --event 8301
```

### The match engine against real matches

The engine plays rounds: buys, duels, trades, plants, defuses, clutches, overtime. It is the same engine as in my CS2 manager game, compiled here as a small C++ library. The comparison below uses real match-ups with their real lineups and ratings as of match day.

Round behaviour, against 45 parsed demos of tier-1 maps (each match-up simulated 300 times):

| | Demos | Engine |
|---|---:|---:|
| CT round share | 53.6% | 52.8% |
| Rounds with a bomb plant | 50.7% | 49.7% |
| T wins after planting | 75.8% | 78.9% |
| First-kill side wins the round | 69.1% | 70.7% |
| Kills per round, both teams | 6.55 | 6.57 |
| Headshot share of kills | 50.0% | 47.3% |
| CT wins by defuse | 22.9% | 19.9% |
| CT wins on time | 11.1% | 13.1% |
| First kill goes to CT | 61.8% | 55.6% |
| Pistol winner wins the next round | 70.5% | 78.6% |
| Deaths that were traded | 20.0% | 15.0% |
| T wins by detonation | 38.5% | 26.7% |

Map statistics, against 1,909 real maps between top-50 teams (each simulated 20 times):

| | Real | Engine |
|---|---:|---:|
| CT round share | 53.8% | 52.7% |
| Rounds per map | 21.86 | 21.40 |
| Maps going to overtime | 13.2% | 11.4% |
| Close maps (13-10, 13-11 or overtime) | 36.1% | 33.0% |
| One-sided maps (13-4 or wider) | 14.4% | 18.3% |
| Kills per player per round | 0.65 | 0.66 |
| Damage per round | 71.7 | 74.2 |
| KAST | 71.5% | 69.2% |
| Multi-kill rounds per player per round | 0.16 | 0.16 |
| Clutches won per map | 2.69 | 2.89 |
| Headshot share of kills | 51.4% | 46.8% |
| Deaths that were traded | 20.3% | 15.1% |
| Assists per player per round | 0.23 | 0.11 |
| Flash assists per player per round | 0.05 | 0.02 |

The totals that decide maps are close: side balance, round counts, kills, clutches, multi-kills. The engine under-produces assists, trades and bomb detonations, and its maps are slightly more one-sided than real ones.

## How it works

1. **Data.** `pickem/update.py` reads public HLTV pages (results, match pages, rankings, event pages) at about one page per second and stores matches, maps and per-map player lines in SQLite.
2. **Player ratings.** `pickem/ratings.py` weights each map by age (full weight for 30 days, falling to zero at 180), adjusts kills, deaths, damage, KAST and rating for opponent strength, shrinks small samples toward the average, and turns the result into eleven skills per player (aim, AWP, entry, trading, utility, clutch, positioning, game sense, leadership, composure, consistency) plus a role.
3. **Teams.** A team is the five players it will field, a chemistry value from how many recent maps at least four of them played together, and a skill per map from the team's recent map results. A new signing brings their own rating with them.
4. **Match engine.** `native/engine.cpp` simulates a veto and every round of every map. `pickem/engine.py` loads it and builds the best-of-one, best-of-three and best-of-five win tables for the field.
5. **Best-of-one factor.** `pickem/calibration.py` stretches best-of-one odds as described above.
6. **Swiss stage.** `pickem/swiss.py` plays the stage with Valve's rules, keeping every result already played and every pairing already announced.
7. **Picks.** `pickem/picks.py` searches for the ticket with the highest chance of the goal (five correct by default) across the simulated stages, so it accounts for picks that depend on each other. `pickem/bracket.py` does the same for the playoff bracket.

The ratings and the engine are ports from the game and were checked against it: the ratings reproduce the game's 3,014 players and 221 teams exactly from the same data, and across 90 match-ups the compiled engine agrees with the game's within sampling noise.

## Usage

Requires Python 3.10+ and a C++ compiler (`g++` or `clang++`; set `CXX` to choose). The engine library is compiled on first use.

```bash
git clone https://github.com/Leclowndu93150/pickem-prediction-model.git
cd pickem-prediction-model
pip install -r requirements.txt
```

The match database is not part of this repository. `data/hltv.sqlite` is created empty on first use. `pickem.update` reads matches newer than the last one stored, or the last 30 days on an empty database (`--since 2026-06-01` goes further back, one page per match). The forecasts in `predictions/` were made with a database covering November 2025 to November 2026; a shorter history gives weaker ratings.

```bash
# read new results, rankings and lineups from HLTV
python -m pickem.update --event 9303

# forecast a stage (the event page id from hltv.org/events/<id>/...)
python -m pickem.predict --event 9303 --update

# later stages and the playoffs, once their teams are set
python -m pickem.predict --event 9304 --update
python -m pickem.predict --event 8302 --update
python -m pickem.predict --event 8302 --playoffs --update
```

Run it again during a stage and it keeps the results so far and simulates the rest. Output goes to `predictions/` as Markdown and JSON; `--json` prints the JSON instead.

```bash
# out-of-sample series test, Swiss stage backtest, a Major with official seeds
python -m pickem.backtest matches --start 2026-05-01 --top 50 --sims 1000
python -m pickem.backtest stages --start 2026-02-01
python -m pickem.backtest event --event 8301

# engine against real matches and demos
python -m pickem.realism
```

A FastAPI app serves the same forecast:

```bash
uvicorn pickem.api:app
```

- `GET /events/{event_id}/status` - which stages are ready, teams and results so far.
- `POST /events/{event_id}/predict` - start a forecast in the background.
- `POST /events/{event_id}/predict/sync` - run one and return it.
- `GET /jobs/{job_id}` - poll a background forecast.

`PICKEM_DATA` moves the database to another folder.

## Data source

Until November 2026 this project read the HLTV phone app's API through `hltv_api/`. That API now answers every request with `401 integrity_required`: it wants proof from the phone's operating system that the request comes from the real app. This project does not try to get around that. `hltv_api/` and the `probe*.py` scripts are kept for reference and no longer work.

The model now reads the public website instead. Match pages carry kills, deaths, damage, KAST, round swing and rating per player per map, which are the inputs that matter most. They do not carry assists, opening duels, multi-kills, clutches or flash assists, so for matches after 2026-10-30 those rates rest on earlier data.

Be a good citizen: keep the delay between pages, and do not redistribute what you collect.

## Glossary

- **Major.** Valve-sponsored championship: 32 teams, three 16-team Swiss stages, then an 8-team playoff.
- **Swiss stage.** Teams play opponents with the same record. Three wins advance, three losses eliminate, nobody meets twice.
- **Best-of-one / best-of-three.** In Stages 1 and 2, matches that can advance or eliminate a team are best-of-three and the rest are best-of-one. In Stage 3 every match is best-of-three.
- **Seed.** A team's starting position. Round one is seed 1 v 9, 2 v 10, and so on.
- **Buchholz.** The sum of a team's opponents' wins minus losses. It orders teams with the same record before pairing.
- **Veto.** The map pick-and-ban before a series.
- **Pick'Em ticket.** Ten picks for a stage: two teams to go 3-0, six to advance 3-1 or 3-2, two to go 0-3. A team picked to advance that goes 3-0 counts as wrong.
- **Five correct.** The threshold that earns the stage's coin.
- **HLTV rank.** HLTV's weekly world ranking.
- **Valve ranking points (VRS).** Valve's own ranking, used for Major invites and seeding.
- **Rating 3.0, ADR, KAST, round swing.** HLTV's per-player numbers: overall rating, damage per round, share of rounds with a kill, assist, survival or trade, and how much a player moved their team's chance of winning rounds.
- **Log-loss.** A score for probabilities: lower is better, 0.693 is a coin flip. It punishes confident misses.
- **Out of sample.** Predicted using only data from before the thing being predicted.
- **Monte Carlo.** Simulating many times and counting outcomes.

## Layout

```
pickem/
  web.py          public HLTV pages -> matches, rankings, event state
  update.py       bring the database up to date
  store.py        SQLite schema
  ratings.py      player skills, roles, chemistry, map skill
  engine.py       loads the match engine, builds win tables
  calibration.py  best-of-one factor
  swiss.py        Swiss stage with Valve's pairing rules
  picks.py        ticket search
  bracket.py      playoff bracket picks
  model.py        rankings, map pool, team assembly
  predict.py      forecast command
  backtest.py     series, stage and pairing tests
  realism.py      engine against real matches and demos
  api.py          FastAPI service
native/           match engine (C++)
predictions/      published forecasts
data/             demo summary; the database lives here locally
hltv_api/         old client for the HLTV app API (no longer works)
```

## License

MIT. See `LICENSE`.
