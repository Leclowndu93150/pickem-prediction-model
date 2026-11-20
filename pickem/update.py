import argparse
from datetime import datetime, timedelta, timezone

from . import web
from .store import open_db, parse_time

RANKING_STEP_DAYS = 7
FIRST_RUN_DAYS = 30


def stored_dates(db, kind):
    return {parse_time(date).date() for (date,) in db.execute("SELECT DISTINCT date FROM rankings WHERE kind = ?", (kind,))}


def save_ranking(db, kind, date, teams):
    key = date.strftime("%Y-%m-%dT00:00:00Z")
    for team in teams:
        db.execute("INSERT OR REPLACE INTO rankings VALUES (?,?,?,?,?,?)", (kind, key, team["id"], team["rank"], team["points"], team["region"]))
        db.execute("INSERT OR IGNORE INTO teams (id, name) VALUES (?, ?)", (team["id"], team["name"]))
        db.execute("UPDATE teams SET name = ? WHERE id = ?", (team["name"], team["id"]))
        for player_id, nick in team["players"]:
            db.execute("INSERT OR IGNORE INTO players (id, nick) VALUES (?, ?)", (player_id, nick))


def refresh_rankings(db, site, since, log):
    latest = {}
    today = datetime.now(timezone.utc)
    for kind in ("hltv", "vrs"):
        page = web.parse_ranking(site.get("/valve-ranking/teams" if kind == "vrs" else "/ranking/teams"), kind)
        newest = max(page["dates"]) if page["dates"] else today
        known = stored_dates(db, kind)
        save_ranking(db, kind, newest, page["teams"])
        latest[kind] = page["teams"]
        wanted = []
        cursor = newest - timedelta(days=RANKING_STEP_DAYS)
        while cursor.date() > since.date():
            candidates = [d for d in page["dates"] if abs((d - cursor).days) <= 3]
            if candidates:
                pick = min(candidates, key=lambda d: abs((d - cursor).days))
                if pick.date() not in known and pick not in wanted:
                    wanted.append(pick)
            cursor -= timedelta(days=RANKING_STEP_DAYS)
        for date in wanted:
            save_ranking(db, kind, date, web.parse_ranking(site.get(web.ranking_path(kind, date)), kind)["teams"])
        log(f"{kind} ranking: {len(page['teams'])} teams, {len(wanted) + 1} snapshots saved")
    db.commit()
    return latest


def set_lineup(db, team_id, players, roles=None):
    known = {player_id: labels for player_id, labels in db.execute("SELECT player_id, roles FROM rosters WHERE roles != ''")}
    for player_id, labels in db.execute("SELECT id, hltv_roles FROM players WHERE hltv_roles != ''"):
        known.setdefault(player_id, labels)
    db.execute("DELETE FROM rosters WHERE team_id = ? AND status = 'active'", (team_id,))
    for player_id in players:
        db.execute("DELETE FROM rosters WHERE player_id = ? AND status = 'active'", (player_id,))
        labels = ",".join(sorted((roles or {}).get(player_id, ()), reverse=True)) or known.get(player_id) or ""
        db.execute("INSERT OR REPLACE INTO rosters VALUES (?,?,?,?)", (team_id, player_id, "active", labels))


def points_lookup(db):
    snapshots = {}
    for date, team_id, points in db.execute("SELECT date, team_id, points FROM rankings WHERE kind = 'vrs' ORDER BY date"):
        snapshots.setdefault(date[:10], {})[team_id] = points
    days = sorted(snapshots)

    def lookup(team_id, start):
        day = start[:10]
        earlier = [d for d in days if d <= day]
        chosen = earlier[-1] if earlier else days[0] if days else None
        return snapshots[chosen].get(team_id) if chosen else None
    return lookup


def save_match(db, match, stars, vrs_points):
    team1, team2 = match["team1"], match["team2"]
    vrs = {team1: vrs_points(team1, match["start"]), team2: vrs_points(team2, match["start"])}
    ranks = match["ranks"]
    db.execute(
        "INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (match["id"], match["event_id"], match["start"], match["best_of"], team1, team2, match["score1"], match["score2"], match["winner"],
         match["lan"], stars, None, vrs[team1], vrs[team2], (ranks.get(team1) or (None, None))[1], (ranks.get(team2) or (None, None))[1],
         match["veto"], match["info"]),
    )
    if match["event_id"] is not None:
        db.execute("INSERT OR IGNORE INTO events (id, name) VALUES (?, ?)", (match["event_id"], match["event_name"]))
    for team_id, name in match["names"].items():
        db.execute("INSERT OR IGNORE INTO teams (id, name) VALUES (?, ?)", (team_id, name))
    for idx, played in enumerate(match["maps"]):
        halves = played["halves"]
        half1 = halves["half1"] if halves else (None, None)
        half2 = halves["half2"] if halves else (None, None)
        extra1 = played["score1"] - half1[0] - half2[0] if halves else 0
        extra2 = played["score2"] - half1[1] - half2[1] if halves else 0
        db.execute(
            "INSERT OR REPLACE INTO maps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (match["id"], idx, played["map"], played["picked_by"], played["score1"], played["score2"],
             extra1 if extra1 + extra2 > 0 else None, extra2 if extra1 + extra2 > 0 else None,
             halves["first_side"] if halves else None, half1[0], half1[1], half2[0], half2[1], played["winner"]),
        )
        rounds = played["score1"] + played["score2"]
        for team_id, opponent_id in ((team1, team2), (team2, team1)):
            for row in played["players"].get(team_id, []):
                db.execute("INSERT OR IGNORE INTO players (id, nick) VALUES (?, ?)", (row["player_id"], row["nick"]))
                db.execute(
                    "INSERT OR REPLACE INTO player_maps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (match["id"], idx, played["map"], match["start"], row["player_id"], team_id, opponent_id, vrs[opponent_id], match["lan"],
                     int(played["winner"] == team_id), rounds, row["kills"], row["deaths"], None, row["adr"], row["kast"], row["rating"],
                     row["swing"], None, None, None, None, None, None, None),
                )


def update(db, site, top=100, events=(), since=None, log=print):
    last = db.execute("SELECT MAX(start) FROM matches").fetchone()[0]
    since = since or (parse_time(last) - timedelta(days=1) if last else datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_DAYS))
    latest = refresh_rankings(db, site, since, log)
    wanted = {team["name"] for kind in latest for team in latest[kind] if team["rank"] <= top}
    for event_id in events:
        event = web.parse_event(site.get(f"/events/{event_id}/-"), event_id)
        wanted.update(team["name"] for team in event["teams"])
    known = {match_id for (match_id,) in db.execute("SELECT id FROM matches WHERE start >= ?", (since.strftime("%Y-%m-%dT%H:%M:%S"),))}
    todo = []
    offset = 0
    while True:
        page = web.parse_results(site.get(f"/results?offset={offset}"))
        if not page:
            break
        todo.extend(r for r in page if r["id"] not in known and parse_time(r["start"]) >= since and (r["team1"] in wanted or r["team2"] in wanted))
        if parse_time(page[-1]["start"]) < since:
            break
        offset += 100
    todo.sort(key=lambda r: r["start"])
    log(f"{len(todo)} new matches since {since:%Y-%m-%d}")
    vrs_points = points_lookup(db)
    lineup_roles = {}
    for done, result in enumerate(todo, 1):
        match = web.parse_match(site.get(result["path"]), result["id"])
        if match is None or not match["maps"] or match["winner"] is None:
            continue
        save_match(db, match, result["stars"], vrs_points)
        lineup_roles.update(match["roles"])
        if done % 20 == 0:
            db.commit()
            log(f"  {done}/{len(todo)} matches")
    for team in latest["vrs"] + latest["hltv"]:
        if len(team["players"]) == 5:
            set_lineup(db, team["id"], [player_id for player_id, _ in team["players"]], lineup_roles)
    db.commit()
    return len(todo)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=100)
    parser.add_argument("--event", type=int, action="append", default=[])
    parser.add_argument("--since")
    parser.add_argument("--delay", type=float, default=1.2)
    args = parser.parse_args()
    db = open_db()
    site = web.Site(args.delay)
    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc) if args.since else None
    count = update(db, site, args.top, args.event, since)
    print(f"done: {count} matches listed, {site.requests} pages read")


if __name__ == "__main__":
    main()
