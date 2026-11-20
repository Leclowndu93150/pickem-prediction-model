import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from . import picks as picking
from . import web
from .board import THEMES, render
from .bracket import bracket
from .calibration import adjust_tables
from .engine import Engine
from .model import Rankings, active_pool, engine_team, stage_forecast
from .ratings import Data, Ratings, Window
from .store import open_db, parse_time
from .swiss import rounds_from_matches
from .update import set_lineup, update

FULL_DAYS = 30
ZERO_DAYS = 180
OUTPUT = Path(__file__).resolve().parent.parent / "predictions"


def slug(name):
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-").replace("--", "-")


def choose_stage(event, number):
    if number:
        return event["stages"][number - 1]
    named = [stage for stage in event["stages"] if stage["name"] == event["name"]]
    return named[0] if named else event["stages"][0]


def event_lineups(db, data, event, team_ids, cutoff):
    listed = {team["id"]: [player_id for player_id, _ in team["players"]] for team in event["teams"]}
    lineups = {}
    for team_id in team_ids:
        players = listed.get(team_id) or [p for (p,) in db.execute("SELECT player_id FROM rosters WHERE team_id = ? AND status = 'active'", (team_id,))]
        lineups[team_id] = players[:5] if len(players) >= 5 else data.lineup_before(team_id, cutoff)
    return lineups


def forecast_stage(db, event, stage, cutoff, sims, pair_sims, goal, seed):
    data = Data(db)
    rankings = Rankings(db)
    engine = Engine()
    order = stage["seeds"]
    if len(order) != 16:
        raise SystemExit(f"{stage['name']}: only {len(order)} of 16 teams are known so far")
    lineups = event_lineups(db, data, event, order, cutoff)
    ratings = Ratings(data, cutoff, Window(FULL_DAYS, ZERO_DAYS), lineups)
    teams = [engine_team(ratings, rankings, team_id, lineups[team_id]) for team_id in order]
    index = {team_id: i for i, team_id in enumerate(order)}
    rounds = rounds_from_matches(stage["matches"], index)
    all_bo3 = "Bo1" not in event["formats"]
    pool = active_pool(data, cutoff)
    vrs = [rankings.points("vrs", team_id, cutoff) for team_id in order]
    result = stage_forecast(engine, teams, pool, rounds, all_bo3, sims, pair_sims, goal, seed, adjust=lambda best_of, table: adjust_tables(best_of, table, vrs))
    names = [stage["teams"].get(team_id) or str(team_id) for team_id in order]
    logos = {team["id"]: team["logo"] for team in event["teams"]}
    baseline = picking.evaluate(result["wins"], result["losses"], picking.seed_picks(list(range(16))), goal)
    rows = []
    for i, team_id in enumerate(order):
        rows.append({
            "seed": i + 1, "team_id": team_id, "team": names[i], "logo": logos.get(team_id), "odds": result["odds"][i], "vrs_points": vrs[i],
            "hltv_rank": rankings.rank("hltv", team_id, cutoff), "rated_players": teams[i]["known"],
            "strength": round(sum(ratings.overall(p) or 9.0 for p in lineups[team_id]) / 5 * 5, 1),
            "lineup": [data.nick(p) for p in lineups[team_id]],
        })
    choice = result["picks"]
    listed = []
    for match in stage["matches"]:
        best_of = 3 if all_bo3 or 2 in match["record"] else 1
        a, b = index[match["a"]], index[match["b"]]
        listed.append({
            "record": list(match["record"]), "a": names[a], "b": names[b], "best_of": best_of, "p_a": float(result["tables"][best_of][a][b]),
            "winner": names[index[match["winner"]]] if match["winner"] is not None else None,
        })
    return {
        "event": event["name"], "stage": stage["name"], "event_id": event["id"], "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "stage_number": event["stages"].index(stage) + 1, "stage_count": len(event["stages"]), "matches": listed,
        "last_match": datetime.fromtimestamp(max(m["start"] for m in data.matches if m["start"] < cutoff), timezone.utc).strftime("%Y-%m-%d"),
        "data_until": datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "map_pool": pool, "all_bo3": all_bo3,
        "sims": sims, "pair_sims": pair_sims, "goal": goal, "rounds_played": sum(1 for r in rounds if r and all(w >= 0 for _, _, w in r)),
        "teams": rows,
        "picks": {
            "three_zero": [names[i] for i in choice["three_zero"]], "advance": [names[i] for i in choice["advance"]],
            "zero_three": [names[i] for i in choice["zero_three"]], "p_goal": choice["p_goal"],
            "expected_correct": choice["expected_correct"], "distribution": choice["distribution"],
        },
        "seed_picks": {"p_goal": baseline["p_goal"], "expected_correct": baseline["expected_correct"]},
        "first_round": [
            {"a": names[a], "b": names[b], "p_a": float(result["tables"][3 if all_bo3 else 1][a][b])} for a, b, _ in (rounds[0] if rounds else [])
        ],
    }


def forecast_playoffs(db, event, cutoff, pair_sims, seed):
    data = Data(db)
    rankings = Rankings(db)
    engine = Engine()
    order = [team_id for slot in event["playoffs"][0]["slots"] for team_id in slot]
    if None in order or len(order) != 8:
        raise SystemExit("the playoff bracket is not set yet")
    lineups = event_lineups(db, data, event, order, cutoff)
    ratings = Ratings(data, cutoff, Window(FULL_DAYS, ZERO_DAYS), lineups)
    teams = [engine_team(ratings, rankings, team_id, lineups[team_id]) for team_id in order]
    vrs = [rankings.points("vrs", team_id, cutoff) for team_id in order]
    tables = engine.tables(teams, active_pool(data, cutoff), (3, 5), pair_sims, seed)
    tables = {best_of: adjust_tables(best_of, table, vrs) for best_of, table in tables.items()}
    names = {i: name for i, name in enumerate(db.execute("SELECT name FROM teams WHERE id = ?", (team_id,)).fetchone()[0] for team_id in order)}
    result = bracket(list(range(8)), tables)
    return {
        "event": event["name"], "stage": "Playoffs", "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "teams": [{"team": names[i], **result["odds"][i]} for i in range(8)],
        "picks": {"champion": names[result["champion"]], "finalists": [names[i] for i in result["finalists"]],
                  "quarter_winners": [names[i] for i in result["quarter_winners"]], "expected_correct": result["expected_correct"]},
    }


def percent(value):
    return f"{100 * value:.0f}%"


def stage_markdown(result, image=None):
    lines = [f"# {result['stage']}: Pick'Em forecast", ""]
    if image:
        lines += [f"![{result['stage']} Pick'Em board]({image})", ""]
    lines.append(f"Generated {result['generated']} from matches up to {result['last_match']}. "
                 f"{result['sims']:,} simulated stages; {result['rounds_played']} round(s) already played.")
    lines += ["", "## Picks", "", "| Slot | Teams |", "|---|---|"]
    picks = result["picks"]
    lines.append(f"| 3-0 | {', '.join(picks['three_zero'])} |")
    lines.append(f"| Advance (3-1 or 3-2) | {', '.join(picks['advance'])} |")
    lines.append(f"| 0-3 | {', '.join(picks['zero_three'])} |")
    lines += ["", f"The model gives these picks a {percent(picks['p_goal'])} chance of {result['goal']} or more correct "
              f"(expected {picks['expected_correct']:.2f} of 10). Picking straight down the seeding gets "
              f"{percent(result['seed_picks']['p_goal'])} (expected {result['seed_picks']['expected_correct']:.2f}) under the same simulations.", ""]
    lines += ["## Odds by team", "", "| Seed | Team | 3-0 | 3-1 | 3-2 | Advance | 2-3 | 1-3 | 0-3 |", "|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in sorted(result["teams"], key=lambda r: -r["odds"]["advance"]):
        o = row["odds"]
        lines.append(f"| {row['seed']} | {row['team']} | {percent(o['3-0'])} | {percent(o['3-1'])} | {percent(o['3-2'])} | **{percent(o['advance'])}** | "
                     f"{percent(o['2-3'])} | {percent(o['1-3'])} | {percent(o['0-3'])} |")
    if result["first_round"]:
        lines += ["", "## Opening matches", "", "| Match | Win chance |", "|---|---|"]
        for match in result["first_round"]:
            lines.append(f"| {match['a']} vs {match['b']} | {percent(match['p_a'])} / {percent(1 - match['p_a'])} |")
    lines += ["", "## Lineups used", "", "| Team | Players | Rated players | VRS points | HLTV rank |", "|---|---|---:|---:|---:|"]
    for row in result["teams"]:
        lines.append(f"| {row['team']} | {', '.join(row['lineup'])} | {row['rated_players']}/5 | {row['vrs_points'] or '-'} | {row['hltv_rank'] or '-'} |")
    return "\n".join(lines) + "\n"


def playoff_markdown(result):
    lines = [f"# {result['event']}: playoff forecast", "", f"Generated {result['generated']}.", "", "| Team | Semi-final | Final | Champion |", "|---|---:|---:|---:|"]
    for row in sorted(result["teams"], key=lambda r: -r["champion"]):
        lines.append(f"| {row['team']} | {percent(row['quarter'])} | {percent(row['semi'])} | **{percent(row['champion'])}** |")
    picks = result["picks"]
    lines += ["", f"Bracket picks: {', '.join(picks['quarter_winners'])} win the quarter-finals; {' and '.join(picks['finalists'])} reach the final; "
              f"**{picks['champion']}** win it ({picks['expected_correct']:.2f} expected correct of 7)."]
    return "\n".join(lines) + "\n"


def predict_event(event_id, stage=None, playoffs=False, refresh=False, cutoff=None, sims=50000, pair_sims=3000, goal=5, seed=1):
    db = open_db()
    site = web.Site()
    if refresh:
        update(db, site, events=[event_id])
    event = web.parse_event(site.get(f"/events/{event_id}/-"), event_id)
    for team in event["teams"]:
        db.execute("INSERT OR IGNORE INTO teams (id, name) VALUES (?, ?)", (team["id"], team["name"]))
        for player_id, nick in team["players"]:
            db.execute("INSERT OR IGNORE INTO players (id, nick) VALUES (?, ?)", (player_id, nick))
        if len(team["players"]) == 5:
            set_lineup(db, team["id"], [player_id for player_id, _ in team["players"]])
    db.commit()
    moment = parse_time(cutoff).timestamp() if cutoff else datetime.now(timezone.utc).timestamp()
    if playoffs:
        result = forecast_playoffs(db, event, moment, pair_sims, seed)
        return result, playoff_markdown(result), slug(event["name"]) + "-playoffs"
    chosen = choose_stage(event, stage)
    result = forecast_stage(db, event, chosen, moment, sims, pair_sims, goal, seed)
    unnumbered = len(event["stages"]) > 1 and "stage" not in chosen["name"].lower()
    name = slug(chosen["name"]) + (f"-stage-{event['stages'].index(chosen) + 1}" if unnumbered else "")
    return result, stage_markdown(result, f"{name}.png"), name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=int, required=True)
    parser.add_argument("--stage", type=int)
    parser.add_argument("--playoffs", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--cutoff")
    parser.add_argument("--sims", type=int, default=50000)
    parser.add_argument("--pair-sims", type=int, default=3000)
    parser.add_argument("--goal", type=int, default=5)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--theme", choices=sorted(THEMES))
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()
    result, text, name = predict_event(args.event, args.stage, args.playoffs, args.update, args.cutoff, args.sims, args.pair_sims, args.goal, args.seed)
    print(json.dumps(result, indent=1) if args.json else text)
    if not args.no_save:
        OUTPUT.mkdir(exist_ok=True)
        (OUTPUT / f"{name}.md").write_text(text, encoding="utf-8")
        (OUTPUT / f"{name}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        if not args.playoffs:
            render(result, args.theme).save(OUTPUT / f"{name}.png", optimize=True)


if __name__ == "__main__":
    main()
