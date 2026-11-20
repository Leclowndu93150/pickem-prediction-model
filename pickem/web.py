import html
import json
import re
import time
from datetime import datetime, timezone

from curl_cffi import requests

BASE = "https://www.hltv.org"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
SWISS_BUCKETS = {
    "ZeroZero": (0, 0), "OneZero": (1, 0), "ZeroOne": (0, 1), "TwoZero": (2, 0), "OneOne": (1, 1), "ZeroTwo": (0, 2),
    "TwoOne": (2, 1), "OneTwo": (1, 2), "TwoTwo": (2, 2),
}


class SiteError(Exception):
    def __init__(self, status, path):
        super().__init__(f"HTTP {status} for {path}")
        self.status = status


class Site:
    def __init__(self, delay=1.2):
        self.session = requests.Session(impersonate="chrome131")
        self.delay = delay
        self.last = 0.0
        self.requests = 0

    def get(self, path):
        wait = self.last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        response = self.session.get(BASE + path, timeout=30)
        self.last = time.monotonic()
        self.requests += 1
        if response.status_code != 200:
            raise SiteError(response.status_code, path)
        return response.text


def iso(unix_ms):
    return datetime.fromtimestamp(int(unix_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def map_id(name):
    return (name or "").strip().lower().replace(" ", "").replace("de_", "") or None


def text_of(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def number(value, cast=float):
    cleaned = re.sub(r"[^0-9.\-]", "", value or "")
    return cast(float(cleaned)) if cleaned not in ("", "-", ".") else None


def ranking_path(kind, date):
    prefix = "/valve-ranking/teams" if kind == "vrs" else "/ranking/teams"
    return f"{prefix}/{date.year}/{MONTHS[date.month - 1]}/{date.day}"


def parse_results(text):
    start = text.find('class="results-holder allres"')
    out = []
    for block in text[start:].split('<div class="result-con')[1:]:
        link = re.search(r'href="(/matches/(\d+)/[^"]+)"', block)
        unix = re.search(r'data-zonedgrouping-entry-unix="(\d+)"', block)
        teams = re.findall(r'<div class="team[^"]*">([^<]+)</div>', block)
        if not link or not unix or len(teams) < 2:
            continue
        out.append({
            "id": int(link.group(2)), "path": link.group(1), "start": iso(unix.group(1)),
            "team1": html.unescape(teams[0]), "team2": html.unescape(teams[1]),
            "stars": block.split("</a>")[0].count("fa-star"),
        })
    return out


def parse_ranking(text, kind):
    date_links = re.findall(r'href="/(?:valve-)?ranking/teams/(\d+)/(\w+)/(\d+)"', text)
    dates = sorted({datetime(int(y), MONTHS.index(m) + 1, int(d), tzinfo=timezone.utc) for y, m, d in date_links if m in MONTHS})
    teams = []
    for block in text.split('class="ranked-team standard-box"')[1:]:
        team = re.search(r'href="/team/(\d+)/', block)
        rank = re.search(r'class="position[^"]*">#(\d+)<', block)
        points = re.search(r'class="points">\((\d+)', block)
        name = re.search(r'<span class="name">([^<]+)</span>', block)
        if not team or not rank:
            continue
        players = []
        for player_id, nick in re.findall(r'href="/player/(\d+)/[^"]*"[^>]*>.*?<div class="nick">(.*?)</div>', block, re.S):
            players.append((int(player_id), text_of(nick)))
        region = re.search(r'class="region region-(\w+)"', block)
        teams.append({
            "id": int(team.group(1)), "name": html.unescape(name.group(1)) if name else None, "rank": int(rank.group(1)),
            "points": int(points.group(1)) if points else None, "players": players, "region": region.group(1) if region else None,
        })
    return {"kind": kind, "teams": teams, "dates": dates}


def _half_scores(fragment):
    halves = re.findall(r'<span class="(ct|t)">(\d+)</span><span class="">:</span><span class="(ct|t)">(\d+)</span>', fragment)
    if len(halves) < 2:
        return None
    first, second = halves[0], halves[1]
    return {"first_side": first[0].upper(), "half1": (int(first[1]), int(first[3])), "half2": (int(second[1]), int(second[3]))}


def _stat_rows(table):
    rows = []
    for row in table.split("<tr")[2:]:
        player = re.search(r'href="/player/(\d+)/', row)
        nick = re.search(r'class="player-nick">([^<]+)<', row)
        kd = re.search(r'class="kd text-center traditional-data">(\d+)-(\d+)<', row)
        if not player or not kd:
            continue
        cell = lambda name: re.search(rf'class="{name} text-center[^"]*">([^<]*)<', row)
        swing = cell("roundSwing")
        adr = re.search(r'class="adr text-center traditional-data">([^<]*)<', row)
        kast = re.search(r'class="kast text-center traditional-data">([^<]*)<', row)
        rating = cell("rating")
        rows.append({
            "player_id": int(player.group(1)), "nick": html.unescape(nick.group(1)) if nick else None,
            "kills": int(kd.group(1)), "deaths": int(kd.group(2)), "adr": number(adr.group(1)) if adr else None,
            "kast": number(kast.group(1)) if kast else None, "rating": number(rating.group(1)) if rating else None,
            "swing": number(swing.group(1)) if swing else None,
        })
    return rows


def parse_match(text, match_id):
    team1 = re.search(r'class="team1-gradient"><a href="/team/(\d+)/[^"]*">.*?class="teamName">([^<]*)<.*?</a><div class="(won|lost|tie)">(\d+)<', text, re.S)
    team2 = re.search(r'class="team2-gradient"><a href="/team/(\d+)/[^"]*">.*?class="teamName">([^<]*)<.*?</a><div class="(won|lost|tie)">(\d+)<', text, re.S)
    unix = re.search(r'class="timeAndEvent"><div class="time"[^>]*data-unix="(\d+)"', text)
    if not team1 or not team2 or not unix:
        return None
    ids = (int(team1.group(1)), int(team2.group(1)))
    event = re.search(r'class="event text-ellipsis"><a href="/events/(\d+)/[^"]*" title="([^"]*)"', text)
    info_block = re.search(r'class="padding preformatted-text">(.*?)</div>', text, re.S)
    info = text_of(info_block.group(1)) if info_block else ""
    best_of = re.search(r"Best of (\d)", info)
    veto_block = re.search(r'veto-box real-veto-only"><div class="padding">(.*?)</div></div>', text, re.S)
    veto = "".join(text_of(line) + "\n" for line in re.findall(r"<div>(.*?)</div>", veto_block.group(1) + "</div>", re.S)) if veto_block else None
    winner = ids[0] if team1.group(3) == "won" else ids[1] if team2.group(3) == "won" else None
    maps = []
    for holder in text.split('class="mapholder"')[1:]:
        holder = holder[:6000]
        stats = re.search(r"/stats/matches/mapstatsid/(\d+)/", holder)
        scores = re.findall(r'class="results-team-score">(\d+)<', holder)
        name = re.search(r'class="mapname">([^<]+)<', holder)
        if not stats or len(scores) < 2 or not name:
            continue
        left = re.search(r'class="results-left([^"]*)"', holder).group(1)
        right = re.search(r'class="results-right([^"]*)"', holder).group(1)
        score1, score2 = int(scores[0]), int(scores[1])
        halves = _half_scores(holder)
        content = text.find(f'id="{stats.group(1)}-content"')
        tables = text[content:text.find('class="stats-content"', content + 10) if text.find('class="stats-content"', content + 10) > 0 else content + 80000].split('<table class="table totalstats">')[1:3]
        players = {}
        for table in tables:
            owner = re.search(r'href="/team/(\d+)/', table)
            if owner:
                players[int(owner.group(1))] = _stat_rows(table.split("</table>")[0])
        maps.append({
            "map": map_id(html.unescape(name.group(1))), "picked_by": ids[0] if "pick" in left else ids[1] if "pick" in right else None,
            "score1": score1, "score2": score2, "winner": ids[0] if score1 > score2 else ids[1], "halves": halves, "players": players,
        })
    ranks = {}
    lineups = {}
    roles = {}
    lineup_block = text[text.find('class="lineups"'):]
    for box in lineup_block.split('class="lineup standard-box"')[1:3]:
        team = re.search(r'href="/team/(\d+)/', box)
        if not team:
            continue
        team_id = int(team.group(1))
        hltv = re.search(r"HLTV: </span>#(\d+)", box)
        vrs = re.search(r"VRS: </span>#(\d+)", box)
        ranks[team_id] = (int(hltv.group(1)) if hltv else None, int(vrs.group(1)) if vrs else None)
        members = []
        for cell in re.findall(r'<td class="player[^"]*">(.*?)</td>', box, re.S):
            player = re.search(r'href="/player/(\d+)/', cell)
            if not player:
                continue
            player_id = int(player.group(1))
            if player_id not in members:
                members.append(player_id)
            for role in re.findall(r"role-pill--(\w+)", cell):
                roles.setdefault(player_id, set()).add(role.upper())
        lineups[team_id] = members
    return {
        "id": match_id, "event_id": int(event.group(1)) if event else None, "event_name": html.unescape(event.group(2)) if event else None,
        "start": iso(unix.group(1)), "best_of": int(best_of.group(1)) if best_of else None,
        "lan": 1 if "(LAN)" in info else 0 if "(Online)" in info else None, "team1": ids[0], "team2": ids[1],
        "names": {ids[0]: html.unescape(team1.group(2)), ids[1]: html.unescape(team2.group(2))},
        "score1": int(team1.group(4)), "score2": int(team2.group(4)), "winner": winner, "veto": veto, "info": info,
        "maps": maps, "ranks": ranks, "lineups": lineups, "roles": roles,
    }


def _team_id(entry):
    while isinstance(entry, dict):
        entry = entry["teamId"]
    return entry


def parse_event(text, event_id):
    name = re.search(r'<h1 class="event-hub-title"[^>]*>([^<]+)<', text) or re.search(r"<title>([^<|]+)", text)
    dates = re.search(r'class="eventdate">(.*?)</td>', text, re.S)
    unix = re.findall(r'data-unix="(\d+)"', dates.group(1)) if dates else []
    teams = []
    attending = text.find('class="teams-attending grid"')
    for box in text[attending:].split('team-box supports-hover"')[1:] if attending >= 0 else []:
        team = re.search(r'href="/team/(\d+)/[^"]*">.*?class="text">([^<]+)<', box, re.S)
        if not team:
            continue
        lineup = box.split('class="lineup-box')[1] if 'class="lineup-box' in box else ""
        lineup = lineup.split('class="coach-text')[0]
        hltv = re.search(r'class="event-world-rank"[^>]*>#(\d+)<', box[:1500])
        vrs = re.search(r'class="event-vrs-rank"[^>]*>#(\d+)<', box[:1500])
        teams.append({
            "id": int(team.group(1)), "name": html.unescape(team.group(2)),
            "hltv_rank": int(hltv.group(1)) if hltv else None, "vrs_rank": int(vrs.group(1)) if vrs else None,
            "players": [(int(p), html.unescape(n)) for p, n in re.findall(r'href="/player/(\d+)/[^"]*">([^<]+)<', lineup)],
        })
    simulator = re.search(r'data-multi-stage-simulator-json="([^"]+)"', text)
    single = re.search(r'data-swiss-simulator-json="([^"]+)"', text)
    stages = []
    playoffs = None
    if simulator:
        data = json.loads(html.unescape(simulator.group(1)))
        stages = [_swiss_stage(stage["group"]) for stage in data.get("swissStages") or []]
        playoffs = _bracket(data.get("playoffs"))
    elif single:
        stages = [_swiss_stage(json.loads(html.unescape(single.group(1))))]
    formats = re.search(r'class="formats table">(.*?)</table>', text, re.S)
    return {
        "id": event_id, "name": html.unescape(name.group(1)).strip() if name else str(event_id),
        "start": iso(unix[0]) if unix else None, "end": iso(unix[-1]) if unix else None,
        "teams": teams, "stages": stages, "playoffs": playoffs, "formats": text_of(formats.group(1)) if formats else "",
    }


def _swiss_stage(group):
    names = {_team_id(team): team.get("teamName") for team in group.get("teams") or []}
    seeds = [_team_id(entry) for entry in sorted((group.get("initialSeedings") or {}).get("orderedSeedings") or [], key=lambda e: e["seedingOrdinal"])]
    scores = {}
    results = group.get("results") or []
    for key, value in zip(results[0::2], results[1::2]):
        scores[(_team_id(key["matchupId"]["team1"]), _team_id(key["matchupId"]["team2"]))] = (value.get("team1Score"), value.get("team2Score"))
    matches = []
    for bucket, record in SWISS_BUCKETS.items():
        for match in (group.get("matches") or {}).get(bucket) or []:
            a, b = _team_id(match["team1"]), _team_id(match["team2"])
            score = scores.get((a, b))
            winner = None
            if score and score[0] is not None and score[0] != score[1]:
                winner = a if score[0] > score[1] else b
            matches.append({
                "record": record, "a": a, "b": b, "winner": winner, "id": (match.get("matchId") or {}).get("matchId"),
                "start": iso(match["timestamp"]) if match.get("timestamp") else None,
            })
    return {
        "name": group.get("eventName"), "teams": names, "seeds": seeds,
        "official_seeds": (group.get("initialSeedings") or {}).get("basis") == "OFFICIAL", "matches": matches,
    }


def _bracket(playoffs):
    if not playoffs:
        return None
    rounds = []
    for entry in (playoffs.get("bracket") or {}).get("rounds") or []:
        slots = []
        for key in sorted(k for k in entry if re.fullmatch(r"slot\d+", k)):
            matchup = (entry[key] or {}).get("matchup") or {}
            sides = [((matchup.get(side) or {}).get("id") or {}).get("teamId") for side in ("team1", "team2")]
            slots.append(sides)
        rounds.append({"name": (entry.get("roundName") or {}).get("name"), "slots": slots})
    return rounds
