import bisect
import math
import re
from collections import Counter

from . import picks as picking
from . import swiss
from .engine import MAPS
from .ratings import DAY, Ratings, epoch

POOL_SIZE = 7
POOL_DAYS = 45


class Rankings:
    def __init__(self, db):
        self.snapshots = {"hltv": [], "vrs": []}
        current = {}
        for kind, date, team_id, rank, points in db.execute("SELECT kind, date, team_id, rank, points FROM rankings ORDER BY kind, date"):
            key = (kind, date)
            if key not in current:
                current[key] = {}
                self.snapshots[kind].append((epoch(date), current[key]))
            current[key][team_id] = (rank, points)
        self.times = {kind: [moment for moment, _ in entries] for kind, entries in self.snapshots.items()}

    def at(self, kind, moment):
        position = bisect.bisect_right(self.times[kind], moment) - 1
        return self.snapshots[kind][position][1] if position >= 0 else {}

    def rank(self, kind, team_id, moment):
        return (self.at(kind, moment).get(team_id) or (None, None))[0]

    def points(self, kind, team_id, moment):
        return (self.at(kind, moment).get(team_id) or (None, None))[1]


def veto_noise_scale(rank):
    level = 8.0 if not rank or rank <= 0 else min(16.0, max(8.0, 16.0 - 7.0 * math.log(rank) / math.log(250.0)))
    return min(1.6, max(0.5, 1.6 - 0.05 * level))


def pool_from_veto(veto):
    names = re.findall(r"(?:removed|picked) ([A-Za-z0-9 ]+)|\d+\. ([A-Za-z0-9 ]+) was left over", veto or "")
    found = [(a or b).strip().lower().replace(" ", "") for a, b in names]
    pool = [name for name in dict.fromkeys(found) if name in MAPS]
    return sorted(pool) if len(pool) == POOL_SIZE else None


def active_pool(data, cutoff):
    low = bisect.bisect_left(data.map_starts, cutoff - POOL_DAYS * DAY)
    high = bisect.bisect_left(data.map_starts, cutoff)
    counts = Counter(name for _, _, _, name, _ in data.map_rows[low:high] if name in MAPS)
    return sorted(name for name, _ in counts.most_common(POOL_SIZE))


def engine_team(ratings, rankings, team_id, roster):
    return ratings.team(team_id, roster, veto_noise_scale(rankings.rank("hltv", team_id, ratings.cutoff)))


def stage_forecast(engine, teams, pool, rounds=(), all_bo3=False, sims=20000, pair_sims=300, goal=5, seed=1, adjust=None):
    tables = engine.tables(teams, pool, (3,) if all_bo3 else (1, 3), pair_sims, seed)
    if adjust is not None:
        tables = {best_of: adjust(best_of, table) for best_of, table in tables.items()}
    wins, losses = swiss.simulate(len(teams), tables, rounds, all_bo3, sims, seed)
    return {
        "tables": tables, "wins": wins, "losses": losses, "odds": picking.record_odds(wins, losses),
        "picks": picking.best_picks(wins, losses, goal, seed=seed),
    }
