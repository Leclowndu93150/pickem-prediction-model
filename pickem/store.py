import gzip
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(os.environ.get("PICKEM_DATA") or Path(__file__).resolve().parent.parent / "data")
DATABASE = "hltv.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, name TEXT, start TEXT, end TEXT, prize_usd INTEGER, teams_count INTEGER,
  location TEXT, online INTEGER, is_big INTEGER, is_major INTEGER, vrs_ranked INTEGER, finished INTEGER);
CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY, event_id INTEGER, start TEXT, best_of INTEGER, team1 INTEGER, team2 INTEGER,
  score1 INTEGER, score2 INTEGER, winner INTEGER, lan INTEGER, stars INTEGER, is_major INTEGER,
  vrs1 INTEGER, vrs2 INTEGER, vrs_rank1 INTEGER, vrs_rank2 INTEGER, veto TEXT, info TEXT);
CREATE TABLE IF NOT EXISTS maps (match_id INTEGER, idx INTEGER, map TEXT, picked_by INTEGER, score1 INTEGER, score2 INTEGER,
  ot1 INTEGER, ot2 INTEGER, team1_first_side TEXT, half1_score1 INTEGER, half1_score2 INTEGER,
  half2_score1 INTEGER, half2_score2 INTEGER, winner INTEGER, PRIMARY KEY (match_id, idx));
CREATE TABLE IF NOT EXISTS player_maps (match_id INTEGER, idx INTEGER, map TEXT, start TEXT, player_id INTEGER, team_id INTEGER,
  opponent_id INTEGER, opponent_vrs INTEGER, lan INTEGER, won INTEGER, rounds INTEGER, kills INTEGER, deaths INTEGER,
  assists INTEGER, adr REAL, kast REAL, rating REAL, round_swing REAL, opening_kills INTEGER, opening_deaths INTEGER,
  multi_kills INTEGER, clutches INTEGER, headshots INTEGER, flash_assists INTEGER, traded INTEGER,
  PRIMARY KEY (match_id, idx, player_id));
CREATE TABLE IF NOT EXISTS teams (id INTEGER PRIMARY KEY, name TEXT, color TEXT, logo_url TEXT, world_rank INTEGER, vrs_points INTEGER,
  coach_id INTEGER, fetched_at REAL);
CREATE TABLE IF NOT EXISTS rosters (team_id INTEGER, player_id INTEGER, status TEXT, roles TEXT, PRIMARY KEY (team_id, player_id));
CREATE TABLE IF NOT EXISTS players (id INTEGER PRIMARY KEY, nick TEXT, name TEXT, country TEXT, flag_url TEXT, age INTEGER, team_id INTEGER,
  benched INTEGER, image_url TEXT, rating REAL, kpr REAL, dpr REAL, adr REAL, kast REAL, round_swing REAL, multi_kill REAL,
  firepower INTEGER, entrying INTEGER, opening INTEGER, sniping INTEGER, trading INTEGER, clutching INTEGER, utility INTEGER,
  prize_usd INTEGER, retired INTEGER, fetched_at REAL, hltv_roles TEXT);
CREATE TABLE IF NOT EXISTS rankings (kind TEXT, date TEXT, team_id INTEGER, rank INTEGER, points INTEGER, region TEXT,
  PRIMARY KEY (kind, date, team_id));
CREATE INDEX IF NOT EXISTS player_maps_start ON player_maps (start);
CREATE INDEX IF NOT EXISTS matches_start ON matches (start);
"""


def parse_time(value):
    return datetime.fromisoformat(value[:19]).replace(tzinfo=timezone.utc)


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def open_db(path=None):
    path = Path(path or DATA / DATABASE)
    packed = path.with_name(path.name + ".gz")
    if not path.exists() and packed.exists():
        with gzip.open(packed, "rb") as source, open(path, "wb") as target:
            shutil.copyfileobj(source, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.executescript(SCHEMA)
    return db


def team_names(db):
    return {team_id: name for team_id, name in db.execute("SELECT id, name FROM teams")}


def player_nicks(db):
    return {player_id: nick for player_id, nick in db.execute("SELECT id, nick FROM players")}
