import argparse
import json
import math
import os
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np

from . import picks as picking
from . import web
from .calibration import BO1_SCALE, adjust_tables
from .engine import Engine
from .model import Rankings, active_pool, engine_team, pool_from_veto, stage_forecast
from .ratings import DAY, Data, Ratings, Window
from .store import open_db, parse_time
from .swiss import SwissStage, rounds_from_matches

WEEK = 7 * DAY


def moment(text):
    return parse_time(text).timestamp()


def logit(p):
    return math.log(p / (1.0 - p))


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def fit_logistic(features, outcomes, steps=50):
    x = np.column_stack(features)
    y = np.asarray(outcomes, dtype=float)
    beta = np.zeros(x.shape[1])
    for _ in range(steps):
        p = sigmoid(x @ beta)
        gradient = x.T @ (y - p)
        hessian = (x * (p * (1 - p))[:, None]).T @ x + 1e-9 * np.eye(x.shape[1])
        beta = beta + np.linalg.solve(hessian, gradient)
    return beta


def scores(probabilities, outcomes):
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(outcomes, dtype=float)
    intercept, slope = fit_logistic([np.ones(len(p)), np.log(p / (1 - p))], y)
    return {
        "n": len(p), "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), "brier": float(np.mean((p - y) ** 2)),
        "accuracy": float(np.mean((p > 0.5) == (y > 0.5))), "slope": float(slope), "intercept": float(intercept),
    }


def veto_pools(db):
    return {match_id: pool_from_veto(veto) for match_id, veto in db.execute("SELECT id, veto FROM matches WHERE veto IS NOT NULL")}


def predict_matches(data, rankings, engine, window, start, end, pools, top=50, sims=300, seed=1, threads=None):
    rng = random.Random(seed)
    jobs = []
    cutoff = start
    while cutoff < end:
        ratings = None
        for match in data.matches:
            if not (cutoff <= match["start"] < min(cutoff + WEEK, end)) or match["winner"] not in (match["team1"], match["team2"]):
                continue
            ranks = [rankings.rank("hltv", match[side], match["start"]) for side in ("team1", "team2")]
            lineups = data.match_lineups.get(match["id"], {})
            sides = [lineups.get(match["team1"], []), lineups.get(match["team2"], [])]
            if None in ranks or max(ranks) > top or min(len(side) for side in sides) < 5 or match["best_of"] not in (1, 3, 5):
                continue
            ratings = ratings or Ratings(data, cutoff, window)
            teams = [engine_team(ratings, rankings, match[side], roster) for side, roster in zip(("team1", "team2"), sides)]
            pool = pools.get(match["id"]) or active_pool(data, match["start"])
            jobs.append((match, ranks, teams, pool, rng.getrandbits(63)))
        cutoff += WEEK

    def run(job):
        match, ranks, teams, pool, job_seed = job
        raw = engine.win_rate(teams[0], teams[1], match["best_of"], pool, job_seed, sims)
        return {
            "id": match["id"], "event": match["event"], "start": match["start"], "best_of": match["best_of"], "lan": match["lan"],
            "raw": raw, "p": float(adjust_tables(match["best_of"], np.array([raw]))[0]), "y": int(match["winner"] == match["team1"]),
            "rank1": ranks[0], "rank2": ranks[1], "vrs1": match["vrs1"], "vrs2": match["vrs2"],
            "known": min(teams[0]["known"], teams[1]["known"]),
        }

    with ThreadPoolExecutor(threads or max(1, (os.cpu_count() or 2) // 2)) as workers:
        return list(workers.map(run, jobs))


def baseline_columns(rows):
    vrs = np.array([((r["vrs1"] or 0) - (r["vrs2"] or 0)) / 400.0 if r["vrs1"] and r["vrs2"] else 0.0 for r in rows])
    rank = np.array([math.log(r["rank2"]) - math.log(r["rank1"]) for r in rows])
    return vrs, rank


def walk_forward(rows, feature_columns, warmup=150, step=50):
    order = np.argsort([r["start"] for r in rows], kind="stable")
    y = np.array([rows[i]["y"] for i in order], dtype=float)
    x = [np.asarray(column)[order] for column in feature_columns]
    out = np.full(len(rows), np.nan)
    for begin in range(warmup, len(rows), step):
        beta = fit_logistic([column[:begin] for column in x], y[:begin])
        block = slice(begin, min(begin + step, len(rows)))
        out[block] = sigmoid(np.column_stack([column[block] for column in x]) @ beta)
    result = np.full(len(rows), np.nan)
    result[order] = out
    return result


def format_scales(rows):
    y = [r["y"] for r in rows]
    raw = np.array([r["raw"] for r in rows])
    single = np.array([1.0 if r["best_of"] == 1 else 0.0 for r in rows])
    series, one = fit_logistic([np.log(raw / (1 - raw)) * (1 - single), np.log(raw / (1 - raw)) * single], y)
    return float(series), float(one)


def report(rows, label):
    y = [r["y"] for r in rows]
    p = np.array([r["p"] for r in rows])
    vrs, rank = baseline_columns(rows)
    engine_logit = np.log(p / (1 - p))
    lines = {label: scores(p, y), "engine without the best-of-one factor": scores([r["raw"] for r in rows], y)}
    lines["VRS points (fitted scale)"] = scores(sigmoid(vrs * fit_logistic([vrs], y)[0]), y)
    lines["HLTV rank (fitted scale)"] = scores(sigmoid(rank * fit_logistic([rank], y)[0]), y)
    walked = {
        "engine, rescaled (walk-forward)": walk_forward(rows, [engine_logit]),
        "VRS points (walk-forward)": walk_forward(rows, [vrs]),
        "engine + VRS (walk-forward)": walk_forward(rows, [engine_logit, vrs]),
        "engine + VRS + rank (walk-forward)": walk_forward(rows, [engine_logit, vrs, rank]),
    }
    held = ~np.isnan(next(iter(walked.values())))
    lines[label + " on walk-forward set"] = scores(p[held], np.array(y)[held])
    for name, values in walked.items():
        lines[name] = scores(values[held], np.array(y)[held])
    return lines


def print_report(lines):
    print(f"{'model':<40} {'n':>5} {'log-loss':>9} {'Brier':>7} {'accuracy':>9} {'slope':>6}")
    for name, s in lines.items():
        print(f"{name:<40} {s['n']:>5} {s['log_loss']:>9.4f} {s['brier']:>7.4f} {100 * s['accuracy']:>8.1f}% {s['slope']:>6.2f}")


def calibration_buckets(rows, edges=(0.5, 0.6, 0.7, 0.8, 0.9, 1.0)):
    out = []
    favourite = [(max(r["p"], 1 - r["p"]), r["y"] if r["p"] >= 0.5 else 1 - r["y"]) for r in rows]
    for low, high in zip(edges, edges[1:]):
        chosen = [(p, y) for p, y in favourite if low <= p < high or (high == 1.0 and p == 1.0)]
        if chosen:
            out.append((low, high, len(chosen), sum(p for p, _ in chosen) / len(chosen), sum(y for _, y in chosen) / len(chosen)))
    return out


def find_swiss_stages(data, start, end):
    by_event = {}
    for match in data.matches:
        if start <= match["start"] < end and match["winner"] in (match["team1"], match["team2"]) and match["event"] is not None:
            by_event.setdefault(match["event"], []).append(match)
    stages = []
    for event, matches in by_event.items():
        wins, losses, played, rounds, used = {}, {}, {}, [], []
        valid = True
        for match in matches:
            a, b = match["team1"], match["team2"]
            if any(wins.get(t, 0) >= 3 or losses.get(t, 0) >= 3 for t in (a, b)):
                continue
            if (wins.get(a, 0), losses.get(a, 0)) != (wins.get(b, 0), losses.get(b, 0)):
                valid = False
                break
            number = played.get(a, 0)
            while len(rounds) <= number:
                rounds.append([])
            rounds[number].append(match)
            used.append(match)
            loser = b if match["winner"] == a else a
            wins[match["winner"]] = wins.get(match["winner"], 0) + 1
            losses[loser] = losses.get(loser, 0) + 1
            played[a] = number + 1
            played[b] = number + 1
        teams = set(wins) | set(losses)
        if valid and len(teams) == 16 and len(used) == 33 and len(rounds[0]) == 8 and all(wins.get(t, 0) == 3 or losses.get(t, 0) == 3 for t in teams):
            stages.append({
                "event": event, "start": used[0]["start"], "rounds": rounds, "matches": used,
                "records": {t: (wins.get(t, 0), losses.get(t, 0)) for t in teams},
                "all_bo3": all(m["best_of"] == 3 for m in used),
            })
    return sorted(stages, key=lambda stage: stage["start"])


def stage_backtest(data, rankings, engine, window, stage, pools, sims=10000, pair_sims=300, seed=1, adjust=None):
    cutoff = min(m["start"] for m in stage["matches"])
    ratings = Ratings(data, cutoff, window)
    ranks = rankings.at("hltv", cutoff)
    order = sorted(stage["records"], key=lambda team: ((ranks.get(team) or (9999,))[0], team))
    index = {team: i for i, team in enumerate(order)}
    lineups = {}
    for match in stage["matches"]:
        for team, players in data.match_lineups.get(match["id"], {}).items():
            lineups.setdefault(team, players)
    teams = [engine_team(ratings, rankings, team, lineups.get(team, [])) for team in order]
    pool = next((pools[m["id"]] for m in stage["matches"] if pools.get(m["id"])), None) or active_pool(data, cutoff)
    first_round = [[(index[m["team1"]], index[m["team2"]], -1) for m in stage["rounds"][0]]]
    forecast = stage_forecast(engine, teams, pool, first_round, stage["all_bo3"], sims, pair_sims, seed=seed, adjust=adjust)
    records = {index[team]: record for team, record in stage["records"].items()}
    advanced = np.array([1.0 if records[i][0] == 3 else 0.0 for i in range(16)])
    odds = forecast["odds"]
    return {
        "event": stage["event"], "start": cutoff, "model": picking.correct_picks(forecast["picks"], records),
        "baseline": picking.correct_picks(picking.seed_picks(list(range(16))), records),
        "p_goal": forecast["picks"]["p_goal"], "expected": forecast["picks"]["expected_correct"],
        "brier": float(np.mean((np.array([o["advance"] for o in odds]) - advanced) ** 2)),
        "baseline_brier": float(np.mean((np.array([1.0] * 8 + [0.0] * 8) - advanced) ** 2)),
        "known": sum(team["known"] for team in teams),
    }


def pairing_check(event_id):
    event = web.parse_event(web.Site().get(f"/events/{event_id}/-"), event_id)
    for stage in event["stages"]:
        index = {team: i for i, team in enumerate(stage["seeds"])}
        rounds = rounds_from_matches(stage["matches"], index)
        sim = SwissStage(len(index), (), all(match["winner"] is not None for match in stage["matches"]) and "Bo1" not in event["formats"])
        matched = total = 0
        for actual in rounds:
            if any(winner < 0 for _, _, winner in actual):
                break
            real = {frozenset((a, b)): winner for a, b, winner in actual}
            mine = sim.next_round()
            matched += sum(1 for entry in mine if frozenset((entry.a, entry.b)) in real)
            total += len(real)
            if {frozenset((entry.a, entry.b)) for entry in mine} != set(real):
                sim.matches = sim.matches[:-len(mine)]
                sim.current = []
                sim.round_number -= 1
                mine = sim.next_round([(a, b) for a, b, _ in actual])
            for entry in mine:
                sim.report(entry, real[frozenset((entry.a, entry.b))])
        print(f"{stage['name']}: {matched}/{total} real pairings reproduced")


def event_backtest(db, data, rankings, engine, window, event_id, sims=20000, pair_sims=1000, seed=1):
    event = web.parse_event(web.Site().get(f"/events/{event_id}/-"), event_id)
    results = []
    for stage in event["stages"]:
        matches = [match for match in stage["matches"] if match["winner"] is not None]
        if len(stage["seeds"]) != 16 or len(matches) != len(stage["matches"]) or len(matches) < 33:
            continue
        order = stage["seeds"]
        index = {team: i for i, team in enumerate(order)}
        cutoff = min(moment(match["start"]) for match in matches)
        lineups = {}
        for match in matches:
            for team, players in data.match_lineups.get(match["id"], {}).items():
                lineups.setdefault(team, players)
        ratings = Ratings(data, cutoff, window)
        teams = [engine_team(ratings, rankings, team, lineups.get(team) or data.lineup_before(team, cutoff)) for team in order]
        rounds = rounds_from_matches(matches, index)
        first_round = [[(a, b, -1) for a, b, _ in rounds[0]]]
        all_bo3 = all(row[0] == 3 for row in db.execute(
            f"SELECT best_of FROM matches WHERE id IN ({','.join('?' * len(matches))})", [match["id"] for match in matches]))
        forecast = stage_forecast(engine, teams, active_pool(data, cutoff), first_round, all_bo3, sims, pair_sims, seed=seed, adjust=adjust_tables)
        records = {}
        for a, b, winner in (entry for entries in rounds for entry in entries):
            loser = b if winner == a else a
            records[winner] = (records.get(winner, (0, 0))[0] + 1, records.get(winner, (0, 0))[1])
            records[loser] = (records.get(loser, (0, 0))[0], records.get(loser, (0, 0))[1] + 1)
        names = [stage["teams"].get(team) for team in order]
        chosen = forecast["picks"]
        results.append({
            "stage": stage["name"], "model": picking.correct_picks(chosen, records),
            "baseline": picking.correct_picks(picking.seed_picks(list(range(16))), records),
            "p_goal": chosen["p_goal"], "expected": chosen["expected_correct"],
            "picks": {slot: [f"{names[i]} ({records[i][0]}-{records[i][1]})" for i in chosen[slot]] for slot in picking.SLOT_NAMES},
        })
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["matches", "stages", "pairings", "event"])
    parser.add_argument("--event", type=int)
    parser.add_argument("--start", default="2026-05-01")
    parser.add_argument("--end")
    parser.add_argument("--full-days", type=int, default=30)
    parser.add_argument("--zero-days", type=int, default=180)
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--sims", type=int, default=300)
    parser.add_argument("--out")
    parser.add_argument("--min-ranked", type=int, default=15)
    args = parser.parse_args()
    if args.command == "pairings":
        pairing_check(args.event)
        return
    db = open_db()
    data = Data(db)
    rankings = Rankings(db)
    engine = Engine()
    pools = veto_pools(db)
    window = Window(args.full_days, args.zero_days)
    start = moment(args.start)
    end = moment(args.end) if args.end else datetime.now(timezone.utc).timestamp()
    names = {team_id: name for team_id, name in db.execute("SELECT id, name FROM events")}
    if args.command == "event":
        for r in event_backtest(db, data, rankings, engine, window, args.event):
            print(f"{r['stage']}: model {r['model']}/10, seeding {r['baseline']}/10 (model expected {r['expected']:.2f}, P(5+) {100 * r['p_goal']:.0f}%)")
            for slot in picking.SLOT_NAMES:
                print(f"  {slot:<11} {', '.join(r['picks'][slot])}")
        return
    if args.command == "matches":
        rows = predict_matches(data, rankings, engine, window, start, end, pools, args.top, args.sims)
        print_report(report(rows, f"engine {window}"))
        series, one = format_scales(rows)
        print(f"fitted scale on the engine's log-odds: best-of-three {series:.2f}, best-of-one {one:.2f} (in use: {BO1_SCALE})")
        for low, high, count, predicted, actual in calibration_buckets(rows):
            print(f"  favourite at {100 * low:.0f}-{100 * high:.0f}%: {count:>4} matches, predicted {100 * predicted:.1f}%, won {100 * actual:.1f}%")
        if args.out:
            with open(args.out, "w") as handle:
                json.dump(rows, handle)
    else:
        stages = [
            stage for stage in find_swiss_stages(data, start, end)
            if sum(1 for team in stage["records"] if team in rankings.at("hltv", stage["start"])) >= args.min_ranked
        ]
        results = [stage_backtest(data, rankings, engine, window, stage, pools, adjust=adjust_tables) for stage in stages]
        for r in results:
            print(f"{datetime.fromtimestamp(r['start'], timezone.utc):%Y-%m-%d} {str(names.get(r['event'], r['event']))[:40]:<40} model {r['model']} baseline {r['baseline']} "
                  f"expected {r['expected']:.2f} P(5+) {100 * r['p_goal']:.0f}% Brier {r['brier']:.3f} vs {r['baseline_brier']:.3f}")
        print(f"stages {len(results)} model {sum(r['model'] for r in results)} baseline {sum(r['baseline'] for r in results)} "
              f"model 5+ {sum(r['model'] >= 5 for r in results)} baseline 5+ {sum(r['baseline'] >= 5 for r in results)}")


if __name__ == "__main__":
    main()
