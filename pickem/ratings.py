from collections import defaultdict

import numpy as np

from .store import parse_time

DAY = 86400.0
MIN_POPULATION_ROUNDS = 200
SHRINK_ROUNDS = {
    "rating": 400, "kpr": 300, "dpr": 300, "adr": 300, "kast": 400, "swing": 500, "open_rate": 500,
    "multi": 400, "clutch": 1500, "flash": 400, "assists": 400, "hs": 300,
}
DETAILED = ("open_rate", "open_success", "multi", "clutch", "flash", "assists", "hs")
OPEN_SUCCESS_PRIOR = 40
ADJUSTED = ("rating", "kpr", "dpr", "adr", "kast")
OPPONENT_SCALE = 3.0
STYLES = {"Entry": (("entrying", "opening"), "open_rate"), "Lurker": (("clutching",), "clutch"), "Support": (("utility",), "flash")}
UNKNOWN_LEVEL = 9.0
PLAYER_COLUMNS = (
    "match_id, idx, map, start, player_id, team_id, opponent_id, opponent_vrs, lan, rounds, kills, deaths, assists, adr, kast, rating, "
    "round_swing, opening_kills, opening_deaths, multi_kills, clutches, headshots, flash_assists"
)


class Window:
    def __init__(self, full_days=30, zero_days=180):
        self.full_days = full_days
        self.zero_days = zero_days

    def weight(self, age_days):
        return np.clip(1.0 - (age_days - self.full_days) / (self.zero_days - self.full_days), 0.0, 1.0)

    def __repr__(self):
        return f"{self.full_days}-{self.zero_days}d"


def epoch(value):
    return parse_time(value).timestamp()


def column(rows, index, kind=float):
    if kind is float:
        return np.array([np.nan if row[index] is None else row[index] for row in rows], dtype=float)
    return np.array([row[index] for row in rows], dtype=kind)


class Data:
    def __init__(self, db):
        rows = db.execute(f"SELECT {PLAYER_COLUMNS} FROM player_maps WHERE rounds > 0 AND rating IS NOT NULL ORDER BY start, match_id, idx").fetchall()
        times = {}
        self.match = column(rows, 0, np.int64)
        self.idx = column(rows, 1, np.int64)
        self.map = np.array([row[2] or "" for row in rows])
        self.start = np.array([times.setdefault(row[3], epoch(row[3])) for row in rows])
        self.player = column(rows, 4, np.int64)
        self.team = column(rows, 5, np.int64)
        self.opp = column(rows, 7)
        self.lan = column(rows, 8)
        self.rounds = column(rows, 9)
        self.kills = column(rows, 10)
        self.deaths = column(rows, 11)
        self.assists = column(rows, 12)
        self.adr = np.nan_to_num(column(rows, 13))
        self.kast = np.nan_to_num(column(rows, 14))
        self.rating = column(rows, 15)
        self.swing = np.nan_to_num(column(rows, 16))
        self.detailed = ~np.isnan(self.assists)
        self.opening_kills = np.nan_to_num(column(rows, 17))
        self.opening_deaths = np.nan_to_num(column(rows, 18))
        self.multi = np.nan_to_num(column(rows, 19))
        self.clutches = np.nan_to_num(column(rows, 20))
        self.headshots = np.nan_to_num(column(rows, 21))
        self.flash = np.nan_to_num(column(rows, 22))
        self.assists = np.nan_to_num(self.assists)
        self.map_key = self.match * 8 + self.idx
        vrs = {}
        self.matches = []
        for match_id, event_id, start, best_of, team1, team2, winner, lan, vrs1, vrs2 in db.execute(
            "SELECT id, event_id, start, best_of, team1, team2, winner, lan, vrs1, vrs2 FROM matches WHERE start IS NOT NULL ORDER BY start, id"
        ):
            vrs[(match_id, team1)] = vrs1
            vrs[(match_id, team2)] = vrs2
            self.matches.append({
                "id": match_id, "event": event_id, "start": epoch(start), "best_of": best_of, "team1": team1, "team2": team2,
                "winner": winner, "lan": lan, "vrs1": vrs1, "vrs2": vrs2,
            })
        own = [vrs.get((m, t)) for m, t in zip(self.match.tolist(), self.team.tolist())]
        self.own = np.array([np.nan if value is None else value for value in own], dtype=float)
        self.map_rows = [
            (epoch(start), team1, team2, name, winner) for start, team1, team2, name, winner in db.execute(
                "SELECT m.start, m.team1, m.team2, p.map, p.winner FROM maps p JOIN matches m ON m.id = p.match_id WHERE m.start IS NOT NULL ORDER BY m.start"
            )
        ]
        self.map_starts = np.array([row[0] for row in self.map_rows])
        self.profiles = {}
        keys = ("id", "nick", "age", "sniping", "entrying", "opening", "clutching", "utility", "hltv_roles")
        for row in db.execute(f"SELECT {', '.join(keys)} FROM players"):
            profile = dict(zip(keys, row))
            profile["labels"] = set(filter(None, (profile.pop("hltv_roles") or "").split(",")))
            self.profiles[row[0]] = profile
        for player_id, roles in db.execute("SELECT player_id, roles FROM rosters WHERE roles != ''"):
            self.profiles.setdefault(player_id, {"id": player_id, "labels": set()})["labels"].update(roles.split(","))
        self.match_lineups = {}
        first_idx = {}
        for match_id, idx, team, player in zip(self.match.tolist(), self.idx.tolist(), self.team.tolist(), self.player.tolist()):
            if first_idx.setdefault((match_id, team), idx) == idx:
                self.match_lineups.setdefault(match_id, {}).setdefault(team, []).append(player)
        self.lineups = {}
        last_key = {}
        for key, team, player in zip(self.map_key.tolist(), self.team.tolist(), self.player.tolist()):
            if last_key.get(team) != key:
                last_key[team] = key
                self.lineups.setdefault(team, []).append((key, []))
            self.lineups[team][-1][1].append(player)
        self.lineup_starts = {}
        first = {}
        for key, start in zip(self.map_key.tolist(), self.start.tolist()):
            first.setdefault(key, start)
        for team, entries in self.lineups.items():
            self.lineup_starts[team] = np.array([first[key] for key, _ in entries])

    def lineup_before(self, team, cutoff):
        starts = self.lineup_starts.get(team)
        if starts is None:
            return []
        position = int(np.searchsorted(starts, cutoff)) - 1
        return self.lineups[team][position][1][:5] if position >= 0 else []

    def nick(self, player_id):
        return (self.profiles.get(player_id) or {}).get("nick") or str(player_id)


def attribute(z):
    return round(min(20.0, max(1.0, 10.5 + 3.0 * z)), 1)


def standardize(values, population):
    reference = values[population] if population.any() else values
    sd = float(np.std(reference)) or 1.0
    return (values - float(np.mean(reference))) / sd


class Ratings:
    def __init__(self, data, cutoff, window, rosters=None, latest_lineups=True):
        self.latest_lineups = latest_lineups
        self.data = data
        self.cutoff = cutoff
        self.window = window
        d = data
        low = int(np.searchsorted(d.start, cutoff - window.zero_days * DAY))
        high = int(np.searchsorted(d.start, cutoff))
        self.rows = slice(low, high)
        cut = lambda array: array[low:high]
        weight = window.weight((cutoff - cut(d.start)) / DAY)
        opp, own, rounds = cut(d.opp), cut(d.own), cut(d.rounds)
        values = {"rating": cut(d.rating), "kpr": cut(d.kills) / rounds, "dpr": cut(d.deaths) / rounds, "adr": cut(d.adr), "kast": cut(d.kast)}
        self.mean_opp, self.slopes = self._slopes(opp, own, rounds, values)
        shift = np.where(np.isnan(opp), 0.0, opp - self.mean_opp)
        adjusted = {key: values[key] - self.slopes[key] * shift for key in ADJUSTED}
        keep = weight > 0
        ids, inverse = np.unique(cut(d.player)[keep], return_inverse=True)
        total = lambda array: np.bincount(inverse, weights=array[keep], minlength=len(ids))
        self.ids = ids
        self.index = {int(player_id): i for i, player_id in enumerate(ids.tolist())}
        wr = weight * rounds
        detail = cut(d.detailed).astype(float)
        n = total(wr)
        n_detail = total(wr * detail)
        safe = lambda top, bottom, fallback: np.divide(top, bottom, out=np.full(len(ids), float(fallback)), where=bottom > 0)
        stats = {"rounds": n, "detail_rounds": n_detail, "maps": total(weight)}
        for key in ADJUSTED:
            stats[key] = total(wr * adjusted[key]) / n
            stats["raw_" + key] = total(wr * values[key]) / n
        stats["swing"] = total(wr * cut(d.swing)) / n
        opening = total(weight * (cut(d.opening_kills) + cut(d.opening_deaths)))
        stats["open_rate"] = safe(opening, n_detail, 0.0)
        stats["open_attempts"] = opening
        stats["open_success"] = safe(total(weight * cut(d.opening_kills)), opening, 0.5)
        for key, source in (("multi", d.multi), ("clutch", d.clutches), ("flash", d.flash), ("assists", d.assists)):
            stats[key] = safe(total(weight * cut(source)), n_detail, 0.0)
        stats["hs"] = safe(total(weight * cut(d.headshots)), total(weight * cut(d.kills) * detail), 0.45)
        weights = total(weight)
        mean = total(weight * adjusted["rating"]) / weights
        variance = np.maximum(total(weight * adjusted["rating"] ** 2) / weights - mean ** 2, 0.0)
        stats["rating_sd"] = np.where(total(np.ones(high - low)) > 1, np.sqrt(variance), 0.3)
        lan = cut(d.lan)
        lan_w = total(wr * (lan == 1))
        online_w = total(wr * (lan == 0))
        stats["lan_rating"] = safe(total(wr * adjusted["rating"] * (lan == 1)), lan_w, np.nan)
        stats["online_rating"] = safe(total(wr * adjusted["rating"] * (lan == 0)), online_w, np.nan)
        stats["big_n"] = np.minimum(lan_w, online_w)
        last = np.zeros(len(ids), dtype=np.int64)
        np.maximum.at(last, inverse, np.arange(int(keep.sum())))
        self.team_of = dict(zip(ids.tolist(), cut(d.team)[keep][last].tolist()))
        self.stats = stats
        self.rosters = self._rosters(rosters)
        self.roles = self._roles()
        self._shrink()
        self.skills = self._skills()
        self.map_skill = self._map_skill()

    def _slopes(self, opp, own, rounds, values):
        known = ~np.isnan(opp) & ~np.isnan(own)
        if int(known.sum()) < 500:
            return 0.0, {key: 0.0 for key in ADJUSTED}
        opponent, mine, weight = opp[known], own[known], rounds[known]
        mean = float(np.average(opponent, weights=weight))
        mean_own = float(np.average(mine, weights=weight))
        design = np.column_stack([np.ones_like(opponent), opponent - mean, mine - mean_own]) * np.sqrt(weight)[:, None]
        slopes = {}
        for key in ADJUSTED:
            slopes[key] = OPPONENT_SCALE * float(np.linalg.lstsq(design, values[key][known] * np.sqrt(weight), rcond=None)[0][1])
        return mean, slopes

    def _rosters(self, overrides):
        rosters = {}
        d = self.data
        for team in set(d.team[self.rows].tolist()) if self.latest_lineups else ():
            members = [p for p in d.lineup_before(team, self.cutoff) if p in self.index]
            if members:
                rosters[team] = members
        for team, members in (overrides or {}).items():
            taken = set(members)
            for other in rosters:
                rosters[other] = [p for p in rosters[other] if p not in taken]
            rosters[team] = [p for p in members if p in self.index]
        return rosters

    def _population(self, key=None):
        rounds = self.stats["detail_rounds" if key in DETAILED else "rounds"]
        return rounds >= MIN_POPULATION_ROUNDS

    def _z(self, key, sign=1.0, among=None):
        population = self._population(key) if among is None else self._population(key) & among
        if among is not None and not population.any():
            population = among
        return sign * standardize(self.stats[key], population)

    def _profile(self, player_id):
        return self.data.profiles.get(player_id) or {}

    def _style_scores(self):
        ids = self.ids.tolist()
        scores = {}
        for role, (profile_keys, stat_key) in STYLES.items():
            fallback = self._z(stat_key)
            parts = []
            for profile_key in profile_keys:
                known = {i: self._profile(i)[profile_key] for i in ids if self._profile(i).get(profile_key) is not None}
                if not known:
                    continue
                mean = float(np.mean(list(known.values())))
                sd = float(np.std(list(known.values()))) or 1.0
                parts.append({i: (known[i] - mean) / sd for i in known})
            scores[role] = {}
            for position, i in enumerate(ids):
                found = [part[i] for part in parts if i in part]
                scores[role][i] = sum(found) / len(found) if found else float(fallback[position])
        for i in ids:
            scores["Lurker"][i] -= 0.5 * scores["Entry"][i]
        return scores

    def _labelled_awp(self, player_id):
        profile = self._profile(player_id)
        if "AWP" in profile.get("labels", ()):
            return True
        if profile.get("sniping") is None and not profile.get("labels"):
            position = self.index[player_id]
            if self.stats["detail_rounds"][position] <= 0:
                return False
            return self.stats["hs"][position] < 0.36 and self.stats["rounds"][position] >= 100
        return False

    def _roles(self):
        scores = self._style_scores()
        roles = {}
        for i in self.ids.tolist():
            if self._labelled_awp(i):
                roles[i] = "AWP"
                continue
            best = max(STYLES, key=lambda role: scores[role][i])
            roles[i] = best if scores[best][i] >= 0.5 else "Rifler"
        for members in self.rosters.values():
            if not members:
                continue
            if not any(roles[i] == "AWP" for i in members):
                sniper = max(members, key=lambda i: self._profile(i).get("sniping") or 0)
                if (self._profile(sniper).get("sniping") or 0) >= 30:
                    roles[sniper] = "AWP"
            open_slots = [i for i in members if roles[i] != "AWP"]
            for role in STYLES:
                if not open_slots:
                    break
                pool = open_slots
                if role == "Entry":
                    pool = [i for i in open_slots if "IGL" not in self._profile(i).get("labels", ())] or open_slots
                pick = max(pool, key=lambda i: scores[role][i])
                roles[pick] = role
                open_slots.remove(pick)
            for i in open_slots:
                roles[i] = "Rifler"
        return roles

    def _groups(self):
        awp = np.array([self.roles[i] == "AWP" for i in self.ids.tolist()])
        return [awp, ~awp]

    def _shrink(self):
        s = self.stats
        for group in self._groups():
            if not group.any():
                continue
            for key, k in SHRINK_ROUNDS.items():
                n = s["detail_rounds" if key in DETAILED else "rounds"]
                population = group & self._population(key)
                reference = s[key][population] if population.any() else s[key][group & (n > 0)] if (group & (n > 0)).any() else s[key][group]
                mean = float(np.mean(reference))
                s[key] = np.where(group, (n * s[key] + k * mean) / (n + k), s[key])
            population = group & self._population("open_success")
            reference = s["open_success"][population] if population.any() else s["open_success"][group]
            attempts = s["open_attempts"]
            s["open_success"] = np.where(group, (attempts * s["open_success"] + OPEN_SUCCESS_PRIOR * float(np.mean(reference))) / (attempts + OPEN_SUCCESS_PRIOR), s["open_success"])
        both = ~np.isnan(s["lan_rating"]) & ~np.isnan(s["online_rating"])
        gap = np.where(both, np.nan_to_num(s["lan_rating"]) - np.nan_to_num(s["online_rating"]), 0.0)
        s["big_gap"] = gap * s["big_n"] / (s["big_n"] + 400)
        s["rating_sd"] = (s["maps"] * s["rating_sd"] + 8 * 0.3) / (s["maps"] + 8)

    def _composite(self, parts, among=None):
        raw = np.zeros(len(self.ids))
        for key, weight, sign in parts:
            raw += weight * self._z(key, sign, among)
        population = self._population() if among is None else self._population() & among
        if among is not None and not population.any():
            population = among
        return standardize(raw, population)

    def _skills(self):
        aim = np.zeros(len(self.ids))
        for group in self._groups():
            if group.any():
                aim = np.where(group, self._composite([("kpr", 1, 1), ("adr", 1, 1), ("rating", 1.5, 1), ("multi", 0.5, 1)], group), aim)
        z = {
            "aim": aim,
            "positioning": self._composite([("dpr", 0.6, -1), ("kast", 0.4, 1)]),
            "game_sense": self._composite([("swing", 0.6, 1), ("kast", 0.4, 1)]),
            "entry": self._composite([("open_rate", 0.5, 1), ("open_success", 0.5, 1)]),
            "trading": self._composite([("assists", 1, 1)]),
            "utility": self._composite([("flash", 1, 1)]),
            "clutch": 0.6 * self._composite([("clutch", 1, 1)]),
            "composure": 0.6 * self._composite([("big_gap", 1, 1)]),
            "consistency": 0.7 * self._composite([("rating_sd", 1, -1)]),
        }
        self.z = z
        skills = {}
        for position, player_id in enumerate(self.ids.tolist()):
            profile = self._profile(player_id)
            if self.roles[player_id] == "AWP":
                awp = min(20.0, max(8.0, 13.0 + 2.5 * float(aim[position])))
            else:
                awp = 4.0 + 5.0 * (profile.get("sniping") or 0) / 100.0
            age = profile.get("age") or 24
            if "IGL" in profile.get("labels", ()):
                leadership = round(min(19.0, max(12.0, 12.0 + 0.4 * (age - 20))), 1)
            else:
                leadership = round(min(12.0, max(6.0, 8.0 + 0.2 * (age - 20))), 1)
            skills[player_id] = {key: attribute(float(values[position])) for key, values in z.items()}
            skills[player_id]["awp"] = round(awp, 1)
            skills[player_id]["leadership"] = leadership
        return skills

    def _map_skill(self):
        d = self.data
        low = int(np.searchsorted(d.map_starts, self.cutoff - self.window.zero_days * DAY))
        high = int(np.searchsorted(d.map_starts, self.cutoff))
        played = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        for start, team1, team2, name, winner in d.map_rows[low:high]:
            weight = float(self.window.weight((self.cutoff - start) / DAY))
            for team in (team1, team2):
                cell = played[team][name]
                cell[0] += weight
                cell[1] += weight if winner == team else 0.0
        return {
            team: {name: round(2.0 * ((won + 3.0) / (count + 6.0) - 0.5), 3) for name, (count, won) in maps.items()}
            for team, maps in played.items()
        }

    def chemistry(self, team, roster):
        d = self.data
        mine = d.team[self.rows] == team
        if not mine.any():
            return 0.35
        keys = d.map_key[self.rows][mine]
        present = np.isin(d.player[self.rows][mine], list(roster))
        _, inverse = np.unique(keys, return_inverse=True)
        together = int((np.bincount(inverse, weights=present) >= 4).sum())
        return round(0.35 + 0.55 * min(1.0, together / 25.0), 3)

    def player(self, player_id):
        if player_id in self.skills:
            return {"id": player_id, "role": self.roles[player_id], "skills": self.skills[player_id]}
        return {"id": player_id, "role": "Rifler", "skills": {"aim": UNKNOWN_LEVEL, "positioning": UNKNOWN_LEVEL, "game_sense": UNKNOWN_LEVEL}}

    def team(self, team_id, roster, veto_noise_scale=1.0):
        members = [self.player(player_id) for player_id in roster[:5]]
        while len(members) < 5:
            members.append(self.player(None))
        return {
            "id": team_id, "players": members, "chemistry": self.chemistry(team_id, roster),
            "map_skill": self.map_skill.get(team_id, {}), "veto_noise_scale": veto_noise_scale,
            "known": sum(1 for player_id in roster[:5] if player_id in self.skills),
        }

    def overall(self, player_id):
        skills = self.skills.get(player_id)
        return None if skills is None else 0.7 * skills["aim"] + 0.15 * skills["positioning"] + 0.15 * skills["game_sense"]
