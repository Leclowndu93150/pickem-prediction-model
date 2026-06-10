"""
Typed domain objects that wrap the raw HLTV mobile-API responses.

Every model:

- exposes the raw payload via ``.raw`` (so anything the API returns is
  still reachable);
- adds typed Python properties for the fields you actually use;
- lazily resolves cross-references (team -> roster, match -> maps,
  event -> teams) via the back-reference to a :class:`HLTVClient`;
- caches resolved sub-objects on the instance so repeated access is free.
"""

from __future__ import annotations

import functools
from collections import Counter
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .client import HLTVClient


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _lazy(fn):
    """Memoize a 0-arg property on the instance."""
    name = f"_cached_{fn.__name__}"

    @functools.wraps(fn)
    def wrapper(self):
        if not hasattr(self, name):
            object.__setattr__(self, name, fn(self))
        return getattr(self, name)

    return property(wrapper)


# ---------------------------------------------------------------- base


class HLTVModel:
    """Common base. Stores raw dict + client back-reference."""

    __slots__ = ("raw", "_client")

    def __init__(self, raw: dict, client: "HLTVClient") -> None:
        object.__setattr__(self, "raw", raw)
        object.__setattr__(self, "_client", client)

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style accessor onto the raw payload."""
        return self.raw.get(key, default)

    def __repr__(self) -> str:
        ident = getattr(self, "id", None) or self.raw.get("name") or "?"
        return f"<{type(self).__name__} {ident}>"


# ---------------------------------------------------------------- player


class PlayerStats(HLTVModel):
    """
    Detailed player stats block from ``/PlayerScreen``.

    Exposes both the headline metrics (rating, K/D-ish numbers) and the
    six 0-100 role scores that HLTV publishes (firepower, entrying,
    opening, sniping, trading, clutching, utility).
    """

    @property
    def rating(self) -> float | None:
        """Current Rating (latest version, currently 3.0)."""
        v = self.raw.get("versionedRating") or {}
        return _to_float(v.get("value") or self.raw.get("rating"))

    @property
    def rating_version(self) -> str | None:
        v = self.raw.get("versionedRating") or {}
        return v.get("version")

    @property
    def rating_trend(self) -> str | None:
        """``"Rising"`` / ``"Falling"`` / ``"Stable"`` from ``ratingForm``."""
        return self.raw.get("ratingForm")

    @property
    def dpr(self) -> float | None:
        """Deaths per round (lower = better)."""
        return _to_float(self.raw.get("dpr"))

    @property
    def kast(self) -> float | None:
        """Percentage of rounds with a Kill, Assist, Survive or Trade (0-1)."""
        raw = self.raw.get("kast")
        if isinstance(raw, str) and raw.endswith("%"):
            return _to_float(raw.rstrip("%")) / 100.0 if _to_float(raw.rstrip("%")) is not None else None
        return _to_float(raw)

    @property
    def adr(self) -> float | None:
        """Average damage per round."""
        return _to_float(self.raw.get("adr"))

    @property
    def kills_per_round(self) -> float | None:
        return _to_float(self.raw.get("killsPrRound"))

    @property
    def round_swing(self) -> float | None:
        """Average round-equity swing per round (string ``"+1.27%"``)."""
        raw = self.raw.get("roundSwing")
        if isinstance(raw, str):
            return _to_float(raw.rstrip("%").lstrip("+"))
        return _to_float(raw)

    @property
    def multi_kill_pct(self) -> float | None:
        raw = self.raw.get("multiKills")
        if isinstance(raw, str) and raw.endswith("%"):
            return _to_float(raw.rstrip("%"))
        return _to_float(raw)

    # 0-100 role scores
    @property
    def firepower(self) -> int | None:
        return _to_int(self.raw.get("firepower"))

    @property
    def entrying(self) -> int | None:
        return _to_int(self.raw.get("entrying"))

    @property
    def opening(self) -> int | None:
        return _to_int(self.raw.get("opening"))

    @property
    def sniping(self) -> int | None:
        return _to_int(self.raw.get("sniping"))

    @property
    def trading(self) -> int | None:
        return _to_int(self.raw.get("trading"))

    @property
    def clutching(self) -> int | None:
        return _to_int(self.raw.get("clutching"))

    @property
    def utility(self) -> int | None:
        return _to_int(self.raw.get("utility"))

    def role_scores(self) -> dict[str, int | None]:
        """All six 0-100 role scores in one dict (plus firepower)."""
        return {
            "firepower": self.firepower,
            "entrying": self.entrying,
            "opening": self.opening,
            "sniping": self.sniping,
            "trading": self.trading,
            "clutching": self.clutching,
            "utility": self.utility,
        }


class Player(HLTVModel):
    """A single CS player."""

    @property
    def id(self) -> int | None:
        """HLTV player id."""
        return _to_int(self.raw.get("playerId"))

    @property
    def nick(self) -> str | None:
        return self.raw.get("nick")

    @property
    def name(self) -> str | None:
        return self.raw.get("name")

    @property
    def rating(self) -> float | None:
        """Rating 3.0 value (current public number). Works on both the
        roster-summary shape and the full-profile shape."""
        # Full-profile: stats.versionedRating.value
        s = self.raw.get("stats")
        if isinstance(s, dict):
            v = s.get("versionedRating") or {}
            val = _to_float(v.get("value"))
            if val is not None:
                return val
            val = _to_float(s.get("rating"))
            if val is not None:
                return val
        # Roster-summary shape: rating is a dict
        r = self.raw.get("rating")
        if isinstance(r, dict):
            return _to_float(r.get("value"))
        return _to_float(r)

    @property
    def rating_version(self) -> str | None:
        s = self.raw.get("stats")
        if isinstance(s, dict):
            v = s.get("versionedRating") or {}
            if v.get("version"):
                return v["version"]
        r = self.raw.get("rating")
        return r.get("version") if isinstance(r, dict) else None

    @property
    def rating_past_3_months(self) -> float | None:
        """Set on ``allPlayers[]`` entries inside a Match payload."""
        raw = self.raw.get("ratingPast3Months")
        if isinstance(raw, dict):
            return _to_float(raw.get("value"))
        return _to_float(raw)

    @property
    def country(self) -> str | None:
        return self.raw.get("country")

    @property
    def age(self) -> int | None:
        return _to_int(self.raw.get("age"))

    @property
    def prize_money(self) -> str | None:
        """Total prize money as the formatted string from HLTV."""
        return self.raw.get("prizeMoney")

    @property
    def num_fans(self) -> int | None:
        return _to_int(self.raw.get("numberOfFans"))

    @_lazy
    def stats(self) -> PlayerStats | None:
        """Detailed :class:`PlayerStats`. Only present on the full profile."""
        s = self.raw.get("stats")
        return PlayerStats(s, self._client) if isinstance(s, dict) else None

    @_lazy
    def full(self) -> "Player":
        """
        Fetch the full ``/PlayerScreen`` payload for this player and
        return a new Player wrapping it. Cached.
        """
        if self.id is None:
            return self
        if "stats" in self.raw and isinstance(self.raw["stats"], dict):
            return self  # already full
        data = self._client.player(self.id)
        return Player(data, self._client)

    @property
    def team_id(self) -> int | None:
        team = self.raw.get("team")
        return _to_int(team.get("teamId") or team.get("id")) if isinstance(team, dict) else None

    @property
    def team_name(self) -> str | None:
        team = self.raw.get("team")
        return team.get("name") if isinstance(team, dict) else None

    @_lazy
    def teammates(self) -> list["Player"]:
        """Current teammates from the full profile."""
        src = self.raw if "teammates" in self.raw else self.full.raw
        return [Player(p, self._client) for p in src.get("teammates") or []]

    @_lazy
    def similar_players(self) -> list["Player"]:
        """
        Players HLTV considers similar by playstyle/role. Useful for
        replacement suggestions.
        """
        src = self.raw if "similarPlayers" in self.raw else self.full.raw
        return [Player(p, self._client) for p in src.get("similarPlayers") or []]

    @_lazy
    def team_history(self) -> list[dict]:
        """Past teams: ``[{name, id, startTime, endTime, logo, teamColor}]``."""
        src = self.raw if "teamHistory" in self.raw else self.full.raw
        return list(src.get("teamHistory") or [])

    @_lazy
    def earnings_by_team(self) -> list[dict]:
        """
        ``[{teamId, teamName, prizeMoney, mostRecentEvent, ...}]`` from
        the full profile.
        """
        src = self.raw if "earningsByTeam" in self.raw else self.full.raw
        return list(src.get("earningsByTeam") or [])

    @_lazy
    def recent_matches(self) -> list["Match"]:
        """Player's last ~10 series."""
        src = self.raw if "recentMatches" in self.raw else self.full.raw
        return [Match(m, self._client) for m in src.get("recentMatches") or []]

    @_lazy
    def events(self) -> list[dict]:
        """Per-event placement history."""
        src = self.raw if "events" in self.raw else self.full.raw
        return list(src.get("events") or [])

    @_lazy
    def trophies(self) -> dict[str, list[dict]]:
        """
        Trophy collection grouped by type:
        ``placementTrophies``, ``mvpTrophies``, ``top20Trophies``,
        ``grandSlamTrophies``, ``evpTrophies``.
        """
        src = self.raw if "trophies" in self.raw else self.full.raw
        return dict(src.get("trophies") or {})

    def num_majors_won(self) -> int:
        """Count of Major trophies (heuristic: placement trophies whose event name contains 'Major')."""
        return sum(
            1
            for t in self.trophies.get("placementTrophies") or []
            if "major" in (t.get("eventName") or "").lower()
        )

    def num_mvps(self) -> int:
        return len(self.trophies.get("mvpTrophies") or [])

    def num_top20(self) -> int:
        return len(self.trophies.get("top20Trophies") or [])


# ---------------------------------------------------------------- map


_MAP_NAMES = {
    31: "Dust2",
    32: "Inferno",
    33: "Vertigo",
    34: "Train",
    40: "Mirage",
    47: "Nuke",
    48: "Ancient",
    49: "Anubis",
    50: "Overpass",
}


class MapPoolEntry(HLTVModel):
    """
    One map's worth of recent stats for a team, taken from
    ``Match.map_team_stats``.
    """

    @property
    def map_id(self) -> int | None:
        return _to_int(self.raw.get("mapId"))

    @property
    def map_name(self) -> str | None:
        if (m := _MAP_NAMES.get(self.map_id)):  # type: ignore[arg-type]
            return m
        return self.raw.get("mapName")

    @property
    def played(self) -> int | None:
        return _to_int(self.raw.get("played"))

    @property
    def win_rate(self) -> float | None:
        raw = self.raw.get("winRate")
        if isinstance(raw, str) and raw.endswith("%"):
            v = _to_float(raw.rstrip("%"))
            return v / 100.0 if v is not None else None
        return _to_float(raw)

    @property
    def ct_win_rate(self) -> float | None:
        raw = self.raw.get("ctWinRate")
        if isinstance(raw, str) and raw.endswith("%"):
            v = _to_float(raw.rstrip("%"))
            return v / 100.0 if v is not None else None
        return _to_float(raw)

    @property
    def t_win_rate(self) -> float | None:
        raw = self.raw.get("tWinRate")
        if isinstance(raw, str) and raw.endswith("%"):
            v = _to_float(raw.rstrip("%"))
            return v / 100.0 if v is not None else None
        return _to_float(raw)

    @property
    def pick_pct(self) -> float | None:
        raw = self.raw.get("pickPercentage")
        if isinstance(raw, str) and raw.endswith("%"):
            v = _to_float(raw.rstrip("%"))
            return v / 100.0 if v is not None else None
        return _to_float(raw)

    @property
    def ban_pct(self) -> float | None:
        raw = self.raw.get("banPercentage")
        if isinstance(raw, str) and raw.endswith("%"):
            v = _to_float(raw.rstrip("%"))
            return v / 100.0 if v is not None else None
        return _to_float(raw)


class MapResult(HLTVModel):
    """One map inside a series."""

    @property
    def map_id(self) -> int | None:
        return _to_int(self.raw.get("mapId"))

    @property
    def name(self) -> str | None:
        return self.raw.get("mapName")

    @property
    def team1_score(self) -> int | None:
        return _to_int((self.raw.get("mapResult") or {}).get("team1Score"))

    @property
    def team2_score(self) -> int | None:
        return _to_int((self.raw.get("mapResult") or {}).get("team2Score"))

    @property
    def winner_team_id(self) -> int | None:
        return _to_int((self.raw.get("mapResult") or {}).get("winnerTeamId"))

    @property
    def picked_by_team_id(self) -> int | None:
        return _to_int(self.raw.get("pickedByTeamId"))

    @property
    def went_to_overtime(self) -> bool:
        mr = self.raw.get("mapResult") or {}
        return bool(_to_int(mr.get("team1OvertimeScore")) or _to_int(mr.get("team2OvertimeScore")))


# ---------------------------------------------------------------- match


class Match(HLTVModel):
    """
    A match (series). Wraps either:

    - a ``recentMatches[i]`` entry (lightweight: ids, scores, datetime),
    - or the full ``/MatchScreen`` payload (everything).

    Use :py:attr:`full` to force-fetch the heavyweight version.
    """

    @property
    def id(self) -> int | None:
        if "match" in self.raw and isinstance(self.raw["match"], dict):
            return _to_int(self.raw["match"].get("matchId"))
        return _to_int(self.raw.get("matchId"))

    def _match_block(self) -> dict:
        return self.raw.get("match", self.raw) if isinstance(self.raw, dict) else {}

    @property
    def team1_id(self) -> int | None:
        m = self._match_block()
        own = m.get("ownTeam") or m.get("team1") or {}
        return _to_int(own.get("teamId"))

    @property
    def team2_id(self) -> int | None:
        m = self._match_block()
        opp = m.get("opponentTeam") or m.get("team2") or {}
        return _to_int(opp.get("teamId"))

    @property
    def team1_name(self) -> str | None:
        m = self._match_block()
        own = m.get("ownTeam") or m.get("team1") or {}
        return own.get("name")

    @property
    def team2_name(self) -> str | None:
        m = self._match_block()
        opp = m.get("opponentTeam") or m.get("team2") or {}
        return opp.get("name")

    @property
    def team1_score(self) -> int | None:
        m = self._match_block()
        return _to_int(m.get("ownTeamScore") or (m.get("matchResult") or {}).get("team1Score"))

    @property
    def team2_score(self) -> int | None:
        m = self._match_block()
        return _to_int(m.get("opponentTeamScore") or (m.get("matchResult") or {}).get("team2Score"))

    @property
    def winner_team_id(self) -> int | None:
        m = self._match_block()
        return _to_int(m.get("winnerTeamId") or (m.get("matchResult") or {}).get("winnerTeamId"))

    @property
    def start_time(self) -> datetime | None:
        m = self._match_block()
        return _parse_iso(m.get("startDateTime"))

    @property
    def event_id(self) -> int | None:
        m = self._match_block()
        return _to_int(m.get("eventId"))

    @property
    def event_name(self) -> str | None:
        m = self._match_block()
        return m.get("eventName")

    @property
    def is_finished(self) -> bool:
        m = self._match_block()
        if m.get("postMatch") is True:
            return True
        # On lightweight ``recentMatches`` entries, ``winnerTeamId`` is
        # populated only for finished series.
        return bool(m.get("winnerTeamId") or (m.get("matchResult") or {}).get("winnerTeamId"))

    @_lazy
    def full(self) -> "Match":
        """
        Fetch the full ``/MatchScreen`` payload. Returns a Match wrapping
        ``matchData``. If we already have ``matchData``, returns self.
        """
        if "matchData" in self.raw:
            return self
        if self.id is None:
            return self
        data = self._client.match(self.id)
        return Match(data.get("matchData", data), self._client)

    @property
    def maps(self) -> list[MapResult]:
        """Map breakdown - lazy-loads the full payload if needed."""
        if "matchData" in self.raw:
            ms = self.raw["matchData"].get("maps") or []
        elif "maps" in self.raw:
            ms = self.raw["maps"] or []
        else:
            full = self.full
            ms = full.raw.get("maps") or []
        return [MapResult(m, self._client) for m in ms]

    @property
    def head_to_head(self) -> dict[str, int]:
        """
        Aggregate H2H counter from the full payload:
        ``{team1Wins, overtimes, team2Wins}``. Lazy-loads if needed.
        """
        src = self.raw if "headToHead" in self.raw else self.full.raw
        return src.get("headToHead") or {}

    @property
    def streams(self) -> list[dict]:
        src = self.raw if "streams" in self.raw else self.full.raw
        return src.get("streams") or []

    @property
    def veto(self) -> list[dict]:
        src = self.raw if "veto" in self.raw else self.full.raw
        return src.get("veto") or []

    @property
    def information(self) -> str | None:
        """Free text e.g. ``"Best of 5 (LAN)\\n\\n* Grand final"``."""
        src = self.raw if "information" in self.raw else self.full.raw
        return src.get("information")

    @property
    def stars(self) -> int | None:
        """HLTV match rating (0-5 stars) - present on schedule listings."""
        return _to_int(self._match_block().get("stars"))

    @property
    def vrs_forecast(self) -> dict[str, Any] | None:
        """
        Pre-match VRS forecast (the API's own win-impact calculator):
        per-team current points/rank, win-case points/rank, lose-case
        points/rank. Use as a sanity-check on your own win-prob model.
        """
        src = self.raw if "vrsForCast" in self.raw else self.full.raw
        return src.get("vrsForCast")

    @property
    def expected_lineup_team1(self) -> list[int]:
        """Player ids HLTV expects team1 to start with."""
        src = self.raw if "expectedLineupData" in self.raw else self.full.raw
        d = src.get("expectedLineupData") or {}
        out: list[int] = []
        for p in d.get("team1PlayerIds") or []:
            pid = _to_int(p.get("playerId") if isinstance(p, dict) else p)
            if pid is not None:
                out.append(pid)
        return out

    @property
    def expected_lineup_team2(self) -> list[int]:
        src = self.raw if "expectedLineupData" in self.raw else self.full.raw
        d = src.get("expectedLineupData") or {}
        out: list[int] = []
        for p in d.get("team2PlayerIds") or []:
            pid = _to_int(p.get("playerId") if isinstance(p, dict) else p)
            if pid is not None:
                out.append(pid)
        return out

    @property
    def expected_lineup_players_team1(self) -> list[Player]:
        """Player snapshots HLTV expects team1 to start with."""
        src = self.raw if "expectedLineupData" in self.raw else self.full.raw
        d = src.get("expectedLineupData") or {}
        return [
            Player(p, self._client)
            for p in (d.get("team1PlayerIds") or [])
            if isinstance(p, dict)
        ]

    @property
    def expected_lineup_players_team2(self) -> list[Player]:
        src = self.raw if "expectedLineupData" in self.raw else self.full.raw
        d = src.get("expectedLineupData") or {}
        return [
            Player(p, self._client)
            for p in (d.get("team2PlayerIds") or [])
            if isinstance(p, dict)
        ]

    @property
    def all_players(self) -> list[Player]:
        """
        Roster snapshot at the time of the match - includes
        ``ratingPast3Months`` for each.
        """
        src = self.raw if "allPlayers" in self.raw else self.full.raw
        return [Player(p, self._client) for p in src.get("allPlayers") or []]

    @property
    def team1_form_matches(self) -> list["Match"]:
        """
        ~20 of team1's most recent results, deeper than ``team.recent_matches``
        (which only shows 10). Each entry exposes ``outcome``,
        ``teamScore``, ``opponentScore``.
        """
        src = self.raw if "team1FormMatches" in self.raw else self.full.raw
        return [Match(m, self._client) for m in src.get("team1FormMatches") or []]

    @property
    def team2_form_matches(self) -> list["Match"]:
        src = self.raw if "team2FormMatches" in self.raw else self.full.raw
        return [Match(m, self._client) for m in src.get("team2FormMatches") or []]

    @property
    def map_team_stats(self) -> dict[str, Any] | None:
        """
        Full per-map stat block: ``team1MapStats``, ``team2MapStats``
        (each ``{mapId: {played, winRate, ctRounds, ctWinRate, tRounds,
        tWinRate, pickPercentage, banPercentage}}``), plus ``mapInfos``
        and ``veto``.
        """
        src = self.raw if "mapTeamStats" in self.raw else self.full.raw
        return src.get("mapTeamStats")

    @property
    def post_match_team_stats(self) -> dict[str, Any] | None:
        """``{team1Stats, team2Stats}`` with ``flashAssists``,
        ``openingKills``, ``multiKills``, ``clutches``, ``pistolRounds``."""
        src = self.raw if "postMatchTeamStats" in self.raw else self.full.raw
        return src.get("postMatchTeamStats")

    @property
    def post_match_player_stats(self) -> dict[str, Any] | None:
        """Per-player aggregated stats from the just-played series."""
        src = self.raw if "postMatchPlayerStats" in self.raw else self.full.raw
        return src.get("postMatchPlayerStats")

    @property
    def highlights(self) -> list[dict]:
        src = self.raw if "highlights" in self.raw else self.full.raw
        return list(src.get("highlights") or [])

    @property
    def form_outcome(self) -> str | None:
        """``"WON"`` / ``"LOST"`` - present on form-match entries."""
        return self.raw.get("outcome")


# ---------------------------------------------------------------- team


class Team(HLTVModel):
    """A team profile + lazy access to roster, matches and map pool."""

    @property
    def id(self) -> int | None:
        # Full team-profile shape: ``teamName`` holds the id and ``name``
        # holds the display name (API quirk).
        # Ranking-summary shape: ``teamId`` is the id, ``teamName`` is the
        # display name.
        tid = _to_int(self.raw.get("teamId"))
        if tid is not None:
            return tid
        return _to_int(self.raw.get("teamName"))

    @property
    def name(self) -> str | None:
        # If both fields are present, ``name`` wins (full profile).
        if self.raw.get("name"):
            return self.raw["name"]
        # Otherwise, fall back to teamName-as-display (ranking summary).
        tn = self.raw.get("teamName")
        if isinstance(tn, str) and not tn.isdigit():
            return tn
        return None

    @property
    def country(self) -> str | None:
        return self.raw.get("countryName")

    @property
    def world_rank(self) -> int | None:
        return _to_int(self.raw.get("worldRank"))

    @property
    def vrs_rank(self) -> int | None:
        """Valve Regional Standing points (higher = better)."""
        return _to_int(self.raw.get("vrsRank"))

    @property
    def world_rank_trend(self) -> str | None:
        """``"Rising"``, ``"Falling"`` or ``"Stable"``."""
        return self.raw.get("worldRankForm")

    @property
    def win_rate(self) -> float | None:
        """Win rate as a 0-1 float (raw API field is a ``"76.2%"`` string)."""
        raw = self.raw.get("winRate")
        if not isinstance(raw, str):
            return None
        try:
            return float(raw.rstrip("%")) / 100.0
        except ValueError:
            return None

    @property
    def num_fans(self) -> int | None:
        return _to_int(self.raw.get("numberOfFans"))

    @_lazy
    def roster(self) -> list[Player]:
        """Active 5 starters as :class:`Player` objects."""
        return [
            Player(p, self._client)
            for p in (self.raw.get("activeLineup") or [])
        ]

    @_lazy
    def bench(self) -> list[Player]:
        return [
            Player(p, self._client)
            for p in (self.raw.get("benchedPlayers") or [])
        ]

    @_lazy
    def coach(self) -> Player | None:
        c = self.raw.get("coach")
        return Player(c, self._client) if isinstance(c, dict) else None

    @_lazy
    def recent_matches(self) -> list[Match]:
        """The last ~10 series from ``recentMatches`` (lightweight)."""
        return [
            Match(m, self._client) for m in (self.raw.get("recentMatches") or [])
        ]

    @_lazy
    def upcoming_matches(self) -> list[Match]:
        return [
            Match(m, self._client) for m in (self.raw.get("upcomingMatches") or [])
        ]

    @_lazy
    def events(self) -> list[dict]:
        """
        Tournament-placement history as raw dicts. Each has ``eventId``,
        ``placement`` (e.g. ``"1st"``), ``placementType``, ``maps``,
        ``endDate``.
        """
        return list(self.raw.get("events") or [])

    @_lazy
    def trophies(self) -> list[dict]:
        return list(self.raw.get("trophies") or [])

    # ------------------------------------------------------------ derived

    def average_roster_rating(self) -> float | None:
        """
        Mean Rating 3.0 across the active 5 (None if no ratings present).
        """
        vals = [p.rating for p in self.roster if p.rating is not None]
        return sum(vals) / len(vals) if vals else None

    def total_recent_maps_played(self, days: int = 90) -> int:
        """
        Sum of ``maps`` across the ``events`` history whose ``endDate``
        falls inside the last ``days`` days.
        """
        cutoff = datetime.now(tz=timezone.utc).timestamp() - days * 86400
        total = 0
        for ev in self.events:
            ts = _parse_iso(ev.get("endDate"))
            if ts and ts.timestamp() >= cutoff:
                total += int(ev.get("maps") or 0)
        return total

    def recent_match_record(self) -> tuple[int, int]:
        """
        ``(wins, losses)`` over :py:attr:`recent_matches` (series-level).
        """
        wins = sum(
            1 for m in self.recent_matches if m.winner_team_id == self.id
        )
        return wins, len(self.recent_matches) - wins

    def map_pool_from_matches(self, fetch_full: bool = False) -> dict[str, dict[str, int]]:
        """
        Aggregate per-map record from the ``recentMatches`` list. If
        ``fetch_full`` is True, expands each match via ``/MatchScreen`` so
        per-map results are visible (costs N extra requests but they're
        cached).

        Returns
        -------
        dict[str, dict[str, int]]
            ``{map_name: {"wins": x, "losses": y, "picks": z}}``
        """
        if not fetch_full:
            return {}
        out: dict[str, Counter] = {}
        for series in self.recent_matches:
            if not series.is_finished:
                continue
            for m in series.full.maps:
                if not m.name:
                    continue
                cell = out.setdefault(m.name, Counter())
                if m.winner_team_id == self.id:
                    cell["wins"] += 1
                else:
                    cell["losses"] += 1
                if m.picked_by_team_id == self.id:
                    cell["picks"] += 1
        return {k: dict(v) for k, v in out.items()}

    def map_pool(self) -> dict[str, MapPoolEntry]:
        """
        Pre-aggregated per-map stats for this team - sourced from the
        team's most recent finished match (whose ``map_team_stats``
        already contains the full map pool with win-rates, pick%, ban%).

        Falls back to ``{}`` if no recent match is available.

        Returns
        -------
        dict[str, MapPoolEntry]
            Keyed by map name (Dust2, Inferno, ...).
        """
        for series in self.recent_matches:
            if not series.is_finished or series.id is None:
                continue
            full = series.full
            mts = full.map_team_stats
            if not mts:
                continue
            is_team1 = full.team1_id == self.id
            block = mts.get("team1MapStats" if is_team1 else "team2MapStats") or {}
            out: dict[str, MapPoolEntry] = {}
            for _mid, entry in (block.items() if isinstance(block, dict) else []):
                m = MapPoolEntry(entry, self._client)
                if m.map_name:
                    out[m.map_name] = m
            if out:
                return out
        return {}

    def head_to_head(self, other: "Team | int") -> dict[str, int]:
        """
        H2H summary against ``other`` using only the cached
        ``recentMatches`` for *this* team. For deeper history use
        :py:meth:`HLTVClient.head_to_head_full`.

        Parameters
        ----------
        other : Team or int
            Either another :class:`Team` or a raw team id.

        Returns
        -------
        dict
            ``{"wins": x, "losses": y, "series": [Match, ...]}``
        """
        other_id = other.id if isinstance(other, Team) else _to_int(other)
        wins = losses = 0
        series: list[Match] = []
        for m in self.recent_matches:
            if other_id not in (m.team1_id, m.team2_id):
                continue
            series.append(m)
            if m.winner_team_id == self.id:
                wins += 1
            elif m.winner_team_id == other_id:
                losses += 1
        return {"wins": wins, "losses": losses, "series": series}


# ---------------------------------------------------------------- event


class Event(HLTVModel):
    """A tournament/event with teams, groupings and matches."""

    @property
    def _event_block(self) -> dict:
        return self.raw.get("event") or {}

    @property
    def id(self) -> int | None:
        return _to_int(self._event_block.get("eventId"))

    @property
    def name(self) -> str | None:
        return self._event_block.get("name") or self.raw.get("name")

    @property
    def prize_pool(self) -> str | None:
        return self._event_block.get("prizePool")

    @property
    def date_start(self) -> datetime | None:
        return _parse_iso(
            self._event_block.get("dateStart")
            or self.raw.get("start_time")
        )

    @property
    def date_end(self) -> datetime | None:
        return _parse_iso(
            self._event_block.get("dateEnd")
            or self.raw.get("end_time")
        )

    @property
    def location(self) -> str | None:
        return self.raw.get("location") or self._event_block.get("location")

    @property
    def is_big(self) -> bool:
        return bool(self.raw.get("isBig"))

    @property
    def is_major(self) -> bool:
        return bool(self.raw.get("isMajor"))

    @property
    def is_featured(self) -> bool:
        return bool(self.raw.get("featured"))

    @property
    def prize(self) -> str | None:
        """Free-text prize description (``"$1,000,000"`` or
        ``"Spots in EU Pro League S7"``)."""
        return self.raw.get("prize") or self._event_block.get("prizePool")

    @property
    def teams_count(self) -> int | None:
        return _to_int(self.raw.get("teams_count") or self._event_block.get("numberOfTeams"))

    @property
    def is_finished(self) -> bool:
        return bool(self.raw.get("isFinished"))

    @_lazy
    def full(self) -> "Event":
        """
        If this Event was built from a lightweight feed entry
        (``events.ongoing.events[i]``), fetch the full
        ``/EventDetails2`` payload and return a new Event. If already
        full, returns ``self``.
        """
        # Detect "full" by presence of either ``teams`` list or ``matches``.
        if self.raw.get("teams") or self.raw.get("matches"):
            return self
        if self.id is None:
            return self
        return self._client.get_event(self.id)

    @_lazy
    def team_ids(self) -> list[int]:
        """All team ids appearing on the event detail (deduped, in order)."""
        seen: list[int] = []
        for t in self.raw.get("teams") or []:
            tid = _to_int(t.get("teamId") or t.get("teamName"))
            if tid and tid not in seen:
                seen.append(tid)
        # Also pull from groupings (they're more reliable for the 16-team list)
        for grouping in self.raw.get("groupings") or []:
            for group in grouping.get("groups") or []:
                inner = (
                    group.get("swissGroup")
                    or group.get("regularGroup")
                    or group.get("eslGroup")
                    or {}
                )
                for t in inner.get("teams") or []:
                    tid = _to_int(t.get("teamId"))
                    if tid and tid not in seen:
                        seen.append(tid)
        return seen

    def teams(self) -> list[Team]:
        """
        Resolve all participating teams to full :class:`Team` objects.
        Each is fetched (cache-aware) via ``client.team()``.
        """
        return [self._client.get_team(tid) for tid in self.team_ids]

    @_lazy
    def groupings(self) -> list[dict]:
        """Raw ``groupings`` (Swiss/Regular/ESL group blocks)."""
        return list(self.raw.get("groupings") or [])

    @property
    def swiss_state(self) -> dict | None:
        """The first Swiss group block, or None."""
        for grouping in self.groupings:
            for g in grouping.get("groups") or []:
                if g.get("swissGroup"):
                    return g["swissGroup"]
        return None

    @property
    def teams_advancing(self) -> int | None:
        ss = self.swiss_state
        return _to_int(ss.get("teamsMovingUp")) if ss else None

    @_lazy
    def matches(self) -> list[Match]:
        """All matches associated with the event."""
        return [Match(m, self._client) for m in (self.raw.get("matches") or [])]

    @_lazy
    def results(self) -> list[Match]:
        return [Match(m, self._client) for m in (self.raw.get("results") or [])]

    @property
    def pickem_info(self) -> list[dict]:
        """Raw ``pickemData.pickems`` (name, deadlineTime, embedUrl)."""
        pd = self.raw.get("pickemData") or {}
        return list(pd.get("pickems") or [])


# ---------------------------------------------------------------- article


class Article(HLTVModel):
    """A news article."""

    @property
    def _data(self) -> dict:
        return self.raw.get("articleData") or self.raw

    @property
    def id(self) -> int | None:
        return _to_int(
            self._data.get("articleId")
            or self._data.get("newsId")
            or self._data.get("id")
        )

    @property
    def title(self) -> str | None:
        return self._data.get("title")

    @property
    def author(self) -> str | None:
        a = self._data.get("author")
        return a.get("name") if isinstance(a, dict) else a

    @property
    def published(self) -> datetime | None:
        return _parse_iso(
            self._data.get("date")
            or self._data.get("publishedAt")
            or self._data.get("created")
        )

    @property
    def body(self) -> Any:
        """The article DSL/HTML payload (raw - varies by article)."""
        return self._data.get("body") or self._data.get("content")

    @property
    def embed_url(self) -> str | None:
        """Full mobile-embed URL - useful for opening the article in a
        webview if the body DSL is not parsed."""
        return self._data.get("embedUrl")

    @property
    def web_url(self) -> str | None:
        """Public hltv.org URL of the article."""
        return self._data.get("hltvNewsUrl")

    @property
    def discussion_id(self) -> int | None:
        """Comment-section discussion id; pair with ``forum_thread``."""
        cs = self.raw.get("commentSectionData") or {}
        return _to_int(cs.get("discussionId"))


# ---------------------------------------------------------------- forum


class ForumThread(HLTVModel):
    """A thread row from ``/ForumContent``."""

    @property
    def id(self) -> int | None:
        return _to_int(self.raw.get("id"))

    @property
    def name(self) -> str | None:
        """``threadName``."""
        return self.raw.get("threadName") or self.raw.get("subject")

    @property
    def body(self) -> str | None:
        return self.raw.get("text")

    @property
    def reply_count(self) -> int | None:
        return _to_int(self.raw.get("threadReplies"))

    @property
    def author_id(self) -> int | None:
        return _to_int(self.raw.get("threadAuthorId"))

    @property
    def author_name(self) -> str | None:
        return self.raw.get("threadAuthorName")

    @property
    def latest_activity(self) -> datetime | None:
        return _parse_iso(self.raw.get("threadLatestActivity"))

    @property
    def is_sticky(self) -> bool:
        return bool(self.raw.get("sticky"))

    @property
    def forum_type(self) -> str | None:
        """``COUNTER_STRIKE``, ``OFF_TOPIC``, ``BETTING`` etc."""
        return self.raw.get("type")

    def open(self) -> dict:
        """
        Fetch the full thread (post + replies + comment section).
        Returns the raw dict from ``client.forum_thread()``.
        """
        if self.id is None:
            return {}
        return self._client.forum_thread(self.id)


class ForumReply(HLTVModel):
    """A single reply inside a forum thread's comment section."""

    @property
    def id(self) -> int | None:
        return _to_int(self.raw.get("replyId"))

    @property
    def number(self) -> int | None:
        return _to_int(self.raw.get("replyNumber"))

    @property
    def text(self) -> str:
        parts = self.raw.get("replyContent") or []
        out = []
        for p in parts:
            t = p.get("text") if isinstance(p, dict) else None
            if isinstance(t, dict):
                out.append(t.get("text") or "")
            else:
                out.append(p.get("defaultText") if isinstance(p, dict) else str(p))
        return "".join(s for s in out if s)

    @property
    def child_count(self) -> int | None:
        return _to_int(self.raw.get("totalChildrenReplies"))

    @property
    def user_id(self) -> int | None:
        u = self.raw.get("replyUser") or {}
        return _to_int(u.get("userId"))

    @property
    def user_name(self) -> str | None:
        u = self.raw.get("replyUser") or {}
        return u.get("name")

    @property
    def time(self) -> datetime | None:
        return _parse_iso(self.raw.get("time"))


class ForumPost(HLTVModel):
    """Full forum thread payload from ``/ForumThread``."""

    @property
    def id(self) -> int | None:
        return _to_int(self.raw.get("threadId"))

    @property
    def subject(self) -> str | None:
        return self.raw.get("subject")

    @property
    def body(self) -> str:
        parts = self.raw.get("postContent") or []
        out = []
        for p in parts:
            t = p.get("text") if isinstance(p, dict) else None
            if isinstance(t, dict):
                out.append(t.get("text") or "")
            else:
                out.append(p.get("defaultText") if isinstance(p, dict) else str(p))
        return "".join(s for s in out if s)

    @property
    def plus_count(self) -> int | None:
        return _to_int(self.raw.get("plusCount"))

    @property
    def time(self) -> datetime | None:
        return _parse_iso(self.raw.get("time"))

    @property
    def author_id(self) -> int | None:
        return _to_int((self.raw.get("postUser") or {}).get("userId"))

    @property
    def author_name(self) -> str | None:
        return (self.raw.get("postUser") or {}).get("name")

    @property
    def replies(self) -> list[ForumReply]:
        cs = self.raw.get("commentSectionData") or {}
        return [ForumReply(r, self._client) for r in cs.get("replies") or []]

    @property
    def total_replies(self) -> int | None:
        cs = self.raw.get("commentSectionData") or {}
        return _to_int(cs.get("totalReplies"))


# ---------------------------------------------------------------- search


class SearchResult(HLTVModel):
    """Wrapper around ``/v2/search`` response."""

    @property
    def players(self) -> list[Player]:
        return [Player(p, self._client) for p in self.raw.get("players") or []]

    @property
    def teams(self) -> list[dict]:
        """Raw team summaries (id, name, logo, worldRanking)."""
        return list(self.raw.get("teams") or [])

    @property
    def events(self) -> list[dict]:
        """Raw event summaries."""
        return list(self.raw.get("events") or [])

    def first_team_id(self) -> int | None:
        ts = self.teams
        return _to_int(ts[0].get("teamId")) if ts else None

    def first_player_id(self) -> int | None:
        ps = self.raw.get("players") or []
        if not ps:
            return None
        pd = ps[0].get("playerData") or ps[0]
        return _to_int(pd.get("playerId"))

    def first_event_id(self) -> int | None:
        es = self.events
        return _to_int(es[0].get("eventId")) if es else None


# ---------------------------------------------------------------- fan rankings


class FanEntry(HLTVModel):
    """An entry in the ``topPlayersByFanCount`` / ``topTeamsByFanCount`` list."""

    @property
    def id(self) -> int | None:
        return _to_int(self.raw.get("id"))

    @property
    def name(self) -> str | None:
        return self.raw.get("nick") or self.raw.get("name")

    @property
    def fan_count(self) -> int | None:
        return _to_int(self.raw.get("fanCount"))


# ---------------------------------------------------------------- event stats


class PlayerEventStats(HLTVModel):
    """An entry from ``/event/stats``'s ``playerStats`` list."""

    @property
    def player(self) -> Player:
        return Player(self.raw.get("playerData") or {}, self._client)

    @property
    def rating(self) -> float | None:
        fr = self.raw.get("formattedRating")
        if isinstance(fr, dict):
            return _to_float(fr.get("value"))
        return _to_float(self.raw.get("sortingRating"))

    @property
    def maps_played(self) -> int | None:
        return _to_int(self.raw.get("maps"))

    @property
    def kd_ratio(self) -> float | None:
        return _to_float(self.raw.get("kd"))

    @property
    def kd_diff(self) -> int | None:
        return _to_int(self.raw.get("kdDiff"))


# ---------------------------------------------------------------- favorites / sub status


class StartupCheck(HLTVModel):
    """Typed view of ``/startupCheck``."""

    @property
    def android_min_version_code(self) -> int | None:
        return _to_int((self.raw.get("android") or {}).get("minimumVersionCode"))

    @property
    def android_play_store_link(self) -> str | None:
        return (self.raw.get("android") or {}).get("playStoreLink")

    @property
    def ios_min_version(self) -> str | None:
        return (self.raw.get("ios") or {}).get("minimumVersion")

    @property
    def fcm_report_interval_min(self) -> int | None:
        return _to_int(self.raw.get("fcmTokenReportIntervalMinutes"))

    @property
    def feature_toggles(self) -> dict[str, Any]:
        """Live feature flags (``showFantasyMenu``, ``showStories`` etc)."""
        return dict(self.raw.get("featureToggle") or {})

    def is_feature_on(self, name: str) -> bool:
        return bool(self.feature_toggles.get(name))

    @property
    def event_hubs(self) -> list[dict]:
        """List of on-site EventHubs currently being run."""
        return list(self.raw.get("eventHubs") or [])

    @property
    def country_override(self) -> str | None:
        ft = self.feature_toggles
        return ft.get("countryOverride")


class PlayerComparison(HLTVModel):
    """
    One column from ``/PlayerCompare``'s ``playersStats`` - values plus
    relative-scale percentages (100 = best of the compared players).
    """

    @property
    def id(self) -> int | None:
        return _to_int(self.raw.get("playerId"))

    @property
    def nick(self) -> str | None:
        return self.raw.get("nick")

    @property
    def team_name(self) -> str | None:
        return self.raw.get("teamName")

    @property
    def map_count(self) -> int | None:
        return _to_int(self.raw.get("mapCount"))

    @property
    def rating(self) -> float | None:
        r = self.raw.get("rating") or {}
        if isinstance(r, dict):
            return _to_float(r.get("value"))
        return _to_float(r)

    @property
    def rating_pct(self) -> float | None:
        """Percentile within the compared cohort (0-100)."""
        return _to_float(self.raw.get("ratingPercentage"))

    @property
    def round_swing(self) -> str | None:
        return self.raw.get("roundSwingValue")

    @property
    def round_swing_pct(self) -> float | None:
        return _to_float(self.raw.get("roundSwingPercentage"))

    @property
    def kast(self) -> str | None:
        return self.raw.get("kastValue")

    @property
    def kast_pct(self) -> float | None:
        return _to_float(self.raw.get("kastPercentage"))

    @property
    def rounds_with_kill(self) -> str | None:
        return self.raw.get("roundsWithKillValue")

    @property
    def rounds_with_multi_kill(self) -> str | None:
        return self.raw.get("roundsWithMultiKillsValue")

    @property
    def adr(self) -> str | None:
        return self.raw.get("adrValue")

    @property
    def adr_pct(self) -> float | None:
        return _to_float(self.raw.get("adrPercentage"))

    @property
    def kpr(self) -> str | None:
        return self.raw.get("kprValue")

    @property
    def kill_diff(self) -> str | None:
        return self.raw.get("killDiffValue")

    @property
    def kill_diff_pct(self) -> float | None:
        return _to_float(self.raw.get("killDiffPercentage"))

    def wins_count(self) -> int:
        """How many ``best*`` flags are True (i.e. categories this player tops)."""
        return sum(
            1 for k, v in self.raw.items()
            if k.startswith("best") and v is True
        )


class SubscriptionStatus(HLTVModel):
    """Response from ``/settings/EventSubscription``."""

    @property
    def event_id(self) -> int | None:
        return _to_int(self.raw.get("eventId"))

    @property
    def is_subscribed(self) -> bool:
        return bool(self.raw.get("isSubscribed") or self.raw.get("subscribed"))
