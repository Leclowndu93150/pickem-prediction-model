import ctypes
import os
import random
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

NATIVE = Path(__file__).resolve().parent.parent / "native"
SOURCES = ("engine.cpp", "capi.cpp")
HEADERS = ("engine.h", "pcg.h")

MAPS = ("ancient", "anubis", "cache", "dust2", "inferno", "mirage", "nuke", "overpass", "train", "vertigo")
CT_EDGE_BY_MAP = {
    "ancient": 0.074, "anubis": 0.011, "cache": 0.052, "dust2": 0.043, "inferno": 0.063,
    "mirage": 0.107, "nuke": 0.152, "overpass": 0.126, "train": 0.09, "vertigo": 0.09,
}
CT_ROUND_RATES = {
    "ancient": 0.524, "anubis": 0.484, "cache": 0.514, "dust2": 0.509, "inferno": 0.52,
    "mirage": 0.553, "nuke": 0.568, "overpass": 0.563, "train": 0.585, "vertigo": 0.52,
}
TUNING = {
    "skill_scale": 0.15, "anchor_bonus": 0.15, "hold_bonus": 0.25, "site_crossfire": 0.05, "crossfire_per_player": 0.24,
    "trade_base": 0.5, "trade_bonus": 0.45, "execute_utility": 0.35, "retake_utility": 0.25, "post_plant_bonus": 0.48,
    "hit_chance_on_loss": 0.6, "stall_after_picks": 15.0, "hp_scale": 0.8, "entry_scale": 0.12, "clutch_scale": 0.12,
    "awp_skill_scale": 0.12, "armor_kevlar": 0.12, "armor_helmet": 0.18, "composure_scale": 0.08, "awp_hold_bonus": 0.1,
    "clutch_numbers": 0.3, "armor_vs_pistol": 2.5, "retake_save_factor": 1.2, "rebound_force_money": 1500.0,
    "pistol_ct_edge_factor": 0.5, "ct_edge_base": 0.08, "form_sd_base": 0.3, "team_form_sd": 0.12, "map_skill_scale": 0.15,
    "pickup_chance": 0.6, "map_shock_sd": 0.1, "z_spread": 6.0, "veto_noise": 0.15, "veto_preference": 5.0,
}
ROLES = {"AWP": 1, "Entry": 2, "Lurker": 3}
SKILLS = ("aim", "awp", "entry", "trading", "utility", "clutch", "positioning", "game_sense", "leadership", "composure", "consistency")
SKILL_DEFAULTS = {key: 10.5 for key in SKILLS} | {"awp": 5.0}
PLAN_WEIGHTS = (0.5, 0.25, 0.12, 0.13)
TACTICS = (0.12, 0.5, 0.6, 0.5)
STAT_FIELDS = ("rounds", "kills", "deaths", "assists", "flash_assists", "damage", "headshots", "opening_kills", "opening_deaths",
               "multi_kill_rounds", "kast_rounds", "traded_deaths", "clutches_played", "clutches_won")
MAP_STAT_FIELDS = ("maps", "team0_wins", "overtime_maps", "rounds", "ct_round_wins", "pistol_rounds", "pistol_ct_wins",
                   "eliminations", "detonations", "defuses", "timeouts", "plants", "t_wins_after_plant",
                   "first_kill_rounds", "first_kill_ct", "first_kill_wins", "after_pistol_rounds", "pistol_winner_converts")
SCORE_BINS = 14


def library_path():
    suffix = ".dll" if sys.platform == "win32" else ".dylib" if sys.platform == "darwin" else ".so"
    return NATIVE / "build" / ("cs2engine" + suffix)


def build():
    target = library_path()
    inputs = [NATIVE / name for name in SOURCES + HEADERS]
    if target.exists() and target.stat().st_mtime >= max(path.stat().st_mtime for path in inputs):
        return target
    compiler = os.environ.get("CXX") or next((c for c in ("g++", "clang++", "c++") if shutil.which(c)), None)
    if compiler is None:
        raise RuntimeError("no C++ compiler found: install g++ or clang++, or set CXX")
    target.parent.mkdir(exist_ok=True)
    command = [compiler, "-std=c++17", "-O2", "-ffp-contract=off", "-shared", "-o", str(target)]
    command += ["-static-libgcc", "-static-libstdc++"] if sys.platform == "win32" else ["-fPIC"]
    subprocess.run(command + [str(NATIVE / name) for name in SOURCES], check=True)
    return target


def pack_team(team):
    values = []
    for player in team["players"]:
        skills = SKILL_DEFAULTS | ({"awp": 15.0} if player.get("role") == "AWP" else {}) | (player.get("skills") or {})
        values.append(float(ROLES.get(player.get("role"), 0)))
        values.extend(float(skills[key]) for key in SKILLS)
    values.extend(PLAN_WEIGHTS)
    values.extend(TACTICS)
    values.append(float(team.get("chemistry", 0.5)))
    values.append(float(team.get("veto_noise_scale", 1.0)))
    skill = team.get("map_skill") or {}
    return (ctypes.c_double * len(values))(*values), (ctypes.c_double * len(MAPS))(*[float(skill.get(m, 0.0)) for m in MAPS])


class Engine:
    def __init__(self, tuning=None):
        self.lib = ctypes.CDLL(str(build()))
        self.lib.cs2_series_wins.restype = ctypes.c_int
        self.lib.cs2_map_stats.restype = None
        values = TUNING | (tuning or {})
        self.tuning = (ctypes.c_double * len(TUNING))(*[float(values[key]) for key in TUNING])
        self.maps = (ctypes.c_char_p * len(MAPS))(*[m.encode() for m in MAPS])
        self.ct_edge = (ctypes.c_double * len(MAPS))(*[CT_EDGE_BY_MAP[m] for m in MAPS])
        self.ct_rates = (ctypes.c_double * len(MAPS))(*[CT_ROUND_RATES[m] for m in MAPS])
        if self.lib.cs2_tuning_fields() != len(TUNING) or self.lib.cs2_map_stat_fields() != len(MAP_STAT_FIELDS) + len(STAT_FIELDS) + SCORE_BINS:
            raise RuntimeError("native library does not match this version: delete native/build")

    def series_wins(self, team_a, team_b, best_of, pool, seed, sims):
        a, skill_a = team_a if isinstance(team_a, tuple) else pack_team(team_a)
        b, skill_b = team_b if isinstance(team_b, tuple) else pack_team(team_b)
        indexes = (ctypes.c_int * len(pool))(*[MAPS.index(m) for m in pool])
        return self.lib.cs2_series_wins(
            a, b, skill_a, skill_b, len(MAPS), self.maps, self.ct_edge, self.ct_rates, self.tuning,
            len(pool), indexes, int(best_of), ctypes.c_uint64(seed), int(sims))

    def win_rate(self, team_a, team_b, best_of, pool, seed, sims):
        return (self.series_wins(team_a, team_b, best_of, pool, seed, sims) + 0.5) / (sims + 1.0)

    def tables(self, teams, pool, best_ofs=(1, 3), sims=300, seed=1, threads=None):
        packed = [pack_team(team) for team in teams]
        count = len(teams)
        rng = random.Random(seed)
        jobs = [(a, b, best_of, rng.getrandbits(63)) for a in range(count) for b in range(a + 1, count) for best_of in best_ofs]
        out = {best_of: np.full((count, count), 0.5) for best_of in best_ofs}

        def run(job):
            a, b, best_of, job_seed = job
            return job, self.win_rate(packed[a], packed[b], best_of, pool, job_seed, sims)

        with ThreadPoolExecutor(threads or max(1, (os.cpu_count() or 2) // 2)) as workers:
            for (a, b, best_of, _), rate in workers.map(run, jobs):
                out[best_of][a, b] = rate
                out[best_of][b, a] = 1.0 - rate
        return out

    def map_stats(self, team_a, team_b, map_id, seed, sims):
        a, skill_a = pack_team(team_a)
        b, skill_b = pack_team(team_b)
        size = len(MAP_STAT_FIELDS) + len(STAT_FIELDS) + SCORE_BINS
        out = (ctypes.c_int64 * size)()
        self.lib.cs2_map_stats(
            a, b, skill_a, skill_b, len(MAPS), self.maps, self.ct_edge, self.ct_rates, self.tuning,
            MAPS.index(map_id), ctypes.c_uint64(seed), int(sims), out)
        values = list(out)
        stats = dict(zip(MAP_STAT_FIELDS, values))
        stats["players"] = dict(zip(STAT_FIELDS, values[len(MAP_STAT_FIELDS):len(MAP_STAT_FIELDS) + len(STAT_FIELDS)]))
        stats["loser_score"] = values[-SCORE_BINS:]
        return stats
