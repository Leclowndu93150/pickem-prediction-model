import argparse
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .backtest import WEEK, moment
from .engine import MAPS, Engine
from .model import Rankings, engine_team
from .ratings import Data, Ratings, Window
from .store import DATA, open_db

DEMOS = Path(__file__).resolve().parent.parent / "data" / "demo_rounds.json"


def merge(total, stats):
    for key, value in stats.items():
        if isinstance(value, dict):
            merge(total.setdefault(key, {}), value)
        elif isinstance(value, list):
            total[key] = [a + b for a, b in zip(total.get(key, [0] * len(value)), value)]
        else:
            total[key] = total.get(key, 0) + value
    return total


def simulate_jobs(engine, jobs, sims, threads=None):
    def run(job):
        teams, map_id, seed = job
        return engine.map_stats(teams[0], teams[1], map_id, seed, sims)

    total = {}
    with ThreadPoolExecutor(threads or max(1, (os.cpu_count() or 2) // 2)) as workers:
        for stats in workers.map(run, jobs):
            merge(total, stats)
    return total


def share(top, bottom):
    return 100.0 * top / bottom if bottom else float("nan")


def round_rows(real, sim):
    ct_wins_real = real["ct_eliminations"] + real["defuses"] + real["timeouts"]
    ct_wins_sim = sim["ct_round_wins"]
    t_wins_real = real["t_eliminations"] + real["detonations"]
    t_wins_sim = sim["rounds"] - sim["ct_round_wins"]
    return [
        ("CT round share", share(real["ct_wins"], real["rounds"]), share(ct_wins_sim, sim["rounds"])),
        ("Rounds with a bomb plant", share(real["plants"], real["rounds"]), share(sim["plants"], sim["rounds"])),
        ("T wins after planting", share(real["t_wins_after_plant"], real["plants"]), share(sim["t_wins_after_plant"], sim["plants"])),
        ("CT wins by defuse", share(real["defuses"], ct_wins_real), share(sim["defuses"], ct_wins_sim)),
        ("CT wins on time", share(real["timeouts"], ct_wins_real), share(sim["timeouts"], ct_wins_sim)),
        ("T wins by detonation", share(real["detonations"], t_wins_real), share(sim["detonations"], t_wins_sim)),
        ("First kill goes to CT", share(real["first_kill_ct"], real["first_kill_rounds"]), share(sim["first_kill_ct"], sim["first_kill_rounds"])),
        ("First-kill side wins the round", share(real["first_kill_wins"], real["first_kill_rounds"]), share(sim["first_kill_wins"], sim["first_kill_rounds"])),
        ("Pistol winner wins the next round", share(real["pistol_winner_converts"], real["after_pistol_rounds"]), share(sim["pistol_winner_converts"], sim["after_pistol_rounds"])),
        ("Kills per round (both teams)", real["kills"] / real["rounds"], sim["players"]["kills"] / sim["rounds"]),
        ("Headshot share of kills", share(real["headshots"], real["kills"]), share(sim["players"]["headshots"], sim["players"]["kills"])),
        ("Deaths that were traded", share(real["trades"], real["kills"]), share(sim["players"]["traded_deaths"], sim["players"]["deaths"])),
    ]


def demo_comparison(data, rankings, engine, window, sims=300):
    demos = json.loads(DEMOS.read_text())
    starts = {match["id"]: match for match in data.matches}
    rng = random.Random(1)
    jobs = []
    real = {}
    for demo in demos:
        match = starts.get(demo["match_id"])
        lineups = data.match_lineups.get(demo["match_id"], {})
        if match is None or demo["map"] not in MAPS or len(lineups) < 2:
            continue
        ratings = Ratings(data, match["start"], window)
        teams = [engine_team(ratings, rankings, team, lineups.get(team, [])) for team in (match["team1"], match["team2"])]
        jobs.append((teams, demo["map"], rng.getrandbits(63)))
        merge(real, {k: v for k, v in demo.items() if k not in ("match_id", "idx", "map")})
    return len(jobs), round_rows(real, simulate_jobs(engine, jobs, sims))


def match_comparison(db, data, rankings, engine, window, start, end, top=50, sims=20):
    rng = random.Random(2)
    jobs = []
    real = {"maps": 0, "overtime": 0, "rounds": 0, "ct": 0, "regulation": 0, "close": 0, "stomps": 0, "favourite_maps": 0}
    chosen = []
    cutoff = start
    while cutoff < end:
        ratings = None
        for match in data.matches:
            if not (cutoff <= match["start"] < min(cutoff + WEEK, end)):
                continue
            ranks = [rankings.rank("hltv", match[side], match["start"]) for side in ("team1", "team2")]
            lineups = data.match_lineups.get(match["id"], {})
            if None in ranks or max(ranks) > top or len(lineups.get(match["team1"], [])) < 5 or len(lineups.get(match["team2"], [])) < 5:
                continue
            ratings = ratings or Ratings(data, cutoff, window)
            teams = [engine_team(ratings, rankings, match[side], lineups[match[side]]) for side in ("team1", "team2")]
            for name, s1, s2, first, h1a, h1b, h2a, h2b in db.execute(
                "SELECT map, score1, score2, team1_first_side, half1_score1, half1_score2, half2_score1, half2_score2 FROM maps WHERE match_id = ?", (match["id"],)
            ):
                if name not in MAPS or None in (s1, s2, h1a, h1b, h2a, h2b) or first not in ("CT", "T"):
                    continue
                jobs.append((teams, name, rng.getrandbits(63)))
                real["maps"] += 1
                real["rounds"] += s1 + s2
                real["overtime"] += s1 + s2 > 24
                real["ct"] += (h1a + h2b) if first == "CT" else (h1b + h2a)
                real["regulation"] += h1a + h1b + h2a + h2b
                real["close"] += s1 + s2 > 24 or min(s1, s2) >= 10
                real["stomps"] += s1 + s2 <= 24 and min(s1, s2) <= 4
            chosen.append(match["id"])
        cutoff += WEEK
    marks = ",".join("?" * len(chosen))
    columns = ("rounds", "kills", "deaths", "assists", "headshots", "opening_kills", "multi_kills", "clutches", "flash_assists", "traded")
    sums = dict(zip(columns, db.execute(
        f"SELECT {', '.join(f'SUM({c})' for c in columns)} FROM player_maps WHERE assists IS NOT NULL AND match_id IN ({marks})", chosen).fetchone()))
    adr, kast = db.execute(f"SELECT SUM(adr * rounds) / SUM(rounds), SUM(kast * rounds) / SUM(rounds) FROM player_maps WHERE match_id IN ({marks})", chosen).fetchone()
    detailed_maps = db.execute(f"SELECT COUNT(*) FROM (SELECT DISTINCT match_id, idx FROM player_maps WHERE assists IS NOT NULL AND match_id IN ({marks}))", chosen).fetchone()[0]
    sim = simulate_jobs(engine, jobs, sims)
    p = sim["players"]
    loser = sim["loser_score"]
    rows = [
        ("CT round share", share(real["ct"], real["regulation"]), share(sim["ct_round_wins"], sim["rounds"])),
        ("Maps going to overtime", share(real["overtime"], real["maps"]), share(sim["overtime_maps"], sim["maps"])),
        ("Rounds per map", real["rounds"] / real["maps"], sim["rounds"] / sim["maps"]),
        ("Close maps (13-10, 13-11 or overtime)", share(real["close"], real["maps"]), share(sum(loser[10:]), sim["maps"])),
        ("One-sided maps (13-4 or wider)", share(real["stomps"], real["maps"]), share(sum(loser[:5]), sim["maps"])),
        ("Kills per player per round", sums["kills"] / sums["rounds"], p["kills"] / p["rounds"]),
        ("Damage per round (ADR)", adr, p["damage"] / p["rounds"]),
        ("KAST", kast, share(p["kast_rounds"], p["rounds"])),
        ("Assists per player per round", sums["assists"] / sums["rounds"], p["assists"] / p["rounds"]),
        ("Headshot share of kills", share(sums["headshots"], sums["kills"]), share(p["headshots"], p["kills"])),
        ("Multi-kill rounds per player per round", sums["multi_kills"] / sums["rounds"], p["multi_kill_rounds"] / p["rounds"]),
        ("Clutches won per map (both teams)", sums["clutches"] / detailed_maps, p["clutches_won"] / sim["maps"]),
        ("Flash assists per player per round", sums["flash_assists"] / sums["rounds"], p["flash_assists"] / p["rounds"]),
        ("Deaths that were traded", share(sums["traded"], sums["deaths"]), share(p["traded_deaths"], p["deaths"])),
    ]
    return real["maps"], rows


def print_rows(title, rows):
    print(title)
    print(f"  {'':<40} {'real':>8} {'engine':>8}")
    for name, real, sim in rows:
        print(f"  {name:<40} {real:>8.2f} {sim:>8.2f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-05-01")
    parser.add_argument("--full-days", type=int, default=30)
    parser.add_argument("--zero-days", type=int, default=180)
    parser.add_argument("--top", type=int, default=50)
    args = parser.parse_args()
    db = open_db()
    data = Data(db)
    rankings = Rankings(db)
    engine = Engine()
    window = Window(args.full_days, args.zero_days)
    count, rows = demo_comparison(data, rankings, engine, window)
    print_rows(f"Round-level behaviour: {count} demo maps, each matchup simulated 300 times", rows)
    count, rows = match_comparison(db, data, rankings, engine, window, moment(args.start), datetime.now(timezone.utc).timestamp(), args.top)
    print_rows(f"Map-level statistics: {count} real maps between top-{args.top} teams, each simulated 20 times", rows)


if __name__ == "__main__":
    main()
