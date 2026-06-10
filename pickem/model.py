"""
Per-team strength model + matchup win-probability for BO1 and BO3.

Strength is built from:
  - Elo seeded by HLTV world rank + VRS rank
  - Recent-form (exponentially-weighted W/L) with a hard cutoff so we
    don't peek at matches played after the pickem deadline
  - Roster Rating 3.0 mean
  - Optional H2H Bayesian prior layered on top of the matchup prob

BO1 vs BO3 conversion:
  Once we have a per-match-win probability ``p`` (which corresponds to a
  single-map win prob), we derive:
    - P_BO1 = p
    - P_BO3 = p**2 + 2 * p**2 * (1 - p) = p**2 * (3 - 2*p)
  This is the standard formula for "win 2 of 3 maps". BO3 is more
  predictable (less variance) - a 60% map favourite is a 65% BO3
  favourite.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from hltv_api import HLTVClient, Match, Player, Team


@dataclass
class TeamStrength:
    """All inputs to the matchup model, frozen as of the pickem deadline."""

    team_id: int
    name: str
    world_rank: int | None
    vrs_rank: int | None
    elo: float                     # margin-aware adjusted Elo
    base_elo: float                # rank-seeded Elo before map updates
    elo_delta: float               # elo - base_elo (form effect)
    roster_rating: float | None
    recent_form: float             # 0..1, exp-weighted (legacy signal)
    sample_n: int                  # number of pre-cutoff series in form sample
    map_sample_n: int = 0          # number of pre-cutoff maps that updated elo

    # Player-level signals
    roster_rating_3mo: float | None = None  # mean ratingPast3Months
    rising_player_share: float = 0.0        # fraction of roster on a "Rising" trend
    falling_player_share: float = 0.0       # fraction on "Falling"

    # Per-map win rates {map_name: {wr, ct_wr, t_wr, played, pick_pct, ban_pct}}
    map_pool: dict[str, dict[str, float]] = field(default_factory=dict)

    # Latest known VRS forecast points (acts as a high-quality external prior)
    vrs_points: float | None = None

    # Stage/tournament context
    stage_entry_record: tuple[int, int] | None = None
    stage_entry_boost: float = 0.0
    big_event_score: float = 0.0
    big_event_maps: int = 0
    style_score: float = 0.0

    def __repr__(self) -> str:
        return (
            f"<TeamStrength {self.name} #{self.world_rank} elo={self.elo:.0f} "
            f"(base={self.base_elo:.0f} {self.elo_delta:+.0f}) "
            f"form={self.recent_form:.2f} maps_n={self.map_sample_n}>"
        )


def _elo_from_rank(world_rank: int | None, vrs_rank: int | None) -> float:
    """
    Seed Elo from HLTV world rank (~2000 for #1, ~1200 floor) and blend
    in VRS points (centred at 1500).
    """
    if not world_rank or world_rank <= 0:
        base = 1500.0
    else:
        base = max(1200.0, 2000.0 - 15.0 * (world_rank - 1))
    if vrs_rank and vrs_rank > 0:
        vrs_offset = (vrs_rank - 1500) / 5.0
        base = 0.7 * base + 0.3 * (1500 + vrs_offset)
    return base


def _recent_form_capped(
    team: "Team", cutoff: datetime, half_life_days: float = 30.0
) -> tuple[float, int]:
    """
    Exponentially-weighted recent-form, but only counting matches whose
    ``startDateTime`` is strictly before ``cutoff``.

    Returns
    -------
    (form, n) : (float, int)
        ``form`` in [0, 1], ``n`` is the sample size.
    """
    cutoff_ts = cutoff.timestamp()
    decay = math.log(2) / (half_life_days * 86400.0)
    wnum = wden = 0.0
    n = 0
    for m in team.recent_matches:
        if m.start_time is None or m.start_time.timestamp() >= cutoff_ts:
            continue
        if m.winner_team_id is None:
            continue
        age = max(0.0, cutoff_ts - m.start_time.timestamp())
        w = math.exp(-decay * age)
        wden += w
        if m.winner_team_id == team.id:
            wnum += w
        n += 1
    if wden == 0:
        return 0.5, 0
    return wnum / wden, n


def _recent_form_from_snapshot(matches: list["Match"]) -> tuple[float, int]:
    """
    Win-rate form from the pre-match form block embedded in MatchScreen.

    Historical team endpoints expose today's recent matches, which leaks
    badly into old Major backtests. The opening-round match payload carries
    the form block HLTV showed at the time of that match; use it when
    available.
    """
    wnum = 0.0
    wden = 0.0
    n = 0
    for idx, m in enumerate(matches):
        outcome = m.form_outcome
        if outcome not in ("WON", "LOST"):
            continue
        # API order is newest first. Keep old rows useful, but make the
        # event's most recent matches matter most.
        recency_w = math.exp(-idx / 12.0)
        score_a = m.raw.get("teamScore")
        score_b = m.raw.get("opponentScore")
        try:
            margin = float(score_a) - float(score_b)
        except (TypeError, ValueError):
            margin = 1.0 if outcome == "WON" else -1.0
        dominance = max(-0.18, min(0.18, margin * 0.06))
        result = 1.0 if outcome == "WON" else 0.0
        wnum += recency_w * max(0.02, min(0.98, result + dominance))
        wden += recency_w
        n += 1
    if n == 0:
        return 0.5, 0
    return wnum / wden, n


def _placement_score(raw: str | None) -> float:
    if not raw:
        return 0.0
    text = raw.lower()
    if text.startswith("1st"):
        return 1.0
    if text.startswith("2nd"):
        return 0.82
    if text.startswith("3"):
        return 0.65
    if text.startswith("4"):
        return 0.55
    if text.startswith("5") or text.startswith("6"):
        return 0.38
    if text.startswith("7") or text.startswith("8"):
        return 0.30
    if text.startswith("9") or text.startswith("10") or text.startswith("11") or text.startswith("12"):
        return 0.12
    return 0.06


def _event_tier_weight(name: str | None) -> float:
    text = (name or "").lower()
    if "major" in text:
        return 1.25
    if any(k in text for k in ("iem", "pgl", "blast", "esl", "starladder", "epl", "cologne", "katowice")):
        return 1.0
    if any(k in text for k in ("qualifier", "closed qualifier", "online")):
        return 0.35
    return 0.55


def _big_event_pedigree(team: "Team", cutoff: datetime, days: float = 180.0) -> tuple[float, int]:
    """
    Cutoff-filtered placement history from Team.events.

    This is not roster-perfect, but it captures the org/team's recent
    LAN/big-event level without looking past the stage cutoff.
    """
    cutoff_ts = cutoff.timestamp()
    decay = math.log(2) / (days * 86400.0)
    score = 0.0
    maps = 0
    for ev in team.events:
        end = ev.get("endDate")
        if not end:
            continue
        try:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except Exception:
            continue
        if end_dt.timestamp() >= cutoff_ts:
            continue
        ev_maps = int(ev.get("maps") or 0)
        if ev_maps <= 0:
            continue
        age = max(0.0, cutoff_ts - end_dt.timestamp())
        w = math.exp(-decay * age)
        tier = _event_tier_weight(ev.get("name"))
        placement = _placement_score(ev.get("placement"))
        map_conf = min(1.0, ev_maps / 10.0)
        score += w * tier * placement * map_conf
        if tier >= 0.9:
            maps += ev_maps
    return score, maps


def _style_score_from_matches(
    team_id: int,
    team_name: str | None,
    matches: list["Match"],
    cutoff: datetime,
    limit: int = 6,
) -> float:
    """
    Aggregate pre-cutoff post-match team stats into one style score.

    Positive means the team has recently been winning opening duels,
    multi-kill volume, pistol rounds, flash support, and clutches.
    """
    cutoff_ts = cutoff.timestamp()
    total = 0.0
    weight_sum = 0.0
    used = 0
    for idx, match in enumerate(matches):
        if used >= limit:
            break
        if match.start_time is not None and match.start_time.timestamp() >= cutoff_ts:
            continue
        if match.id is None:
            continue
        full = match.full
        stats = full.post_match_team_stats
        if not stats:
            continue
        if full.team1_id == team_id or full.team1_name == team_name:
            own = stats.get("team1Stats") or {}
            opp = stats.get("team2Stats") or {}
        elif full.team2_id == team_id or full.team2_name == team_name:
            own = stats.get("team2Stats") or {}
            opp = stats.get("team1Stats") or {}
        else:
            continue

        def ndiff(key: str) -> float:
            a = float(own.get(key) or 0.0)
            b = float(opp.get(key) or 0.0)
            return (a - b) / max(1.0, a + b)

        style = (
            0.34 * ndiff("openingKills")
            + 0.24 * ndiff("multiKills")
            + 0.18 * ndiff("pistolRounds")
            + 0.14 * ndiff("flashAssists")
            + 0.10 * ndiff("clutches")
        )
        w = math.exp(-idx / 6.0)
        total += w * style
        weight_sum += w
        used += 1
    if weight_sum == 0:
        return 0.0
    return max(-1.0, min(1.0, total / weight_sum))


def _margin_multiplier(round_diff: int) -> float:
    """
    Glicko-style margin-of-victory multiplier. A 16-14 squeaker counts
    less than a 16-3 stomp. Capped to avoid runaway updates.

    The shape is ``ln(|diff| + 1) / 2.2`` - gives roughly:
      |diff|=2 -> 0.50, |diff|=5 -> 0.81, |diff|=10 -> 1.09, |diff|=13 -> 1.20
    """
    return math.log(abs(round_diff) + 1) / 2.2


def _deep_form_series(client: "HLTVClient", team_id: int, cutoff: datetime) -> list[dict]:
    """
    Return up to ~30 series for ``team_id`` by combining the 10-series
    ``team.recent_matches`` list with the 20-series form blocks visible
    inside the team's most recent ``MatchScreen``.

    Each returned entry is a dict ``{ts, opp_id, opp_name, won, score_a, score_b}``
    suitable for downstream form aggregation. Series with ``ts >= cutoff``
    are filtered out.
    """
    cutoff_ts = cutoff.timestamp()
    seen: set[int] = set()
    out: list[dict] = []
    team = client.get_team(team_id)
    for series in team.recent_matches:
        if series.id is None or series.id in seen:
            continue
        if series.start_time is None or series.start_time.timestamp() >= cutoff_ts:
            continue
        seen.add(series.id)
        opp_id = series.team2_id if series.team1_id == team_id else series.team1_id
        opp_name = series.team2_name if series.team1_id == team_id else series.team1_name
        won = series.winner_team_id == team_id
        out.append({
            "ts": series.start_time.timestamp(),
            "opp_id": opp_id,
            "opp_name": opp_name,
            "won": won,
            "score_a": series.team1_score if series.team1_id == team_id else series.team2_score,
            "score_b": series.team2_score if series.team1_id == team_id else series.team1_score,
        })
    # Deeper form via the form blocks inside the latest match
    form_matches = client.team_form_via_match(team_id)
    for fm in form_matches:
        if fm.id is None or fm.id in seen:
            continue
        seen.add(fm.id)
        # Form-block entries have no startDateTime, only outcome scores
        outcome = fm.form_outcome
        if outcome not in ("WON", "LOST"):
            continue
        out.append({
            "ts": None,  # unknown precise time, treat as "recent"
            "opp_id": None,
            "opp_name": fm.raw.get("opponentName"),
            "won": outcome == "WON",
            "score_a": fm.raw.get("teamScore"),
            "score_b": fm.raw.get("opponentScore"),
        })
    return out


def _walk_maps_for_elo(
    client: "HLTVClient",
    team_ids: list[int],
    cutoff: datetime,
    base_elo: dict[int, float],
    k: float = 24.0,
) -> tuple[dict[int, float], dict[int, int]]:
    """
    Replay each pre-cutoff map result and update both teams' Elo using a
    margin-aware Glicko-style update.

    For each map:
        expected_a = 1 / (1 + 10**((elo_b - elo_a) / 400))
        actual_a   = 1.0 if A won the map, else 0.0
        margin     = _margin_multiplier(|rounds_a - rounds_b|)
        delta      = K * margin * (actual_a - expected_a)
        elo_a += delta;  elo_b -= delta

    Only matches between teams already in ``base_elo`` are counted
    (so we don't try to update teams not in the tournament). Other
    matches still contribute via the team's own Elo update.

    Parameters
    ----------
    client : HLTVClient
    team_ids : list[int]
        Tournament participants. Only their Elo is updated.
    cutoff : datetime
    base_elo : dict[int, float]
        Starting Elo by team id (mutated copy returned).
    k : float, default 24.0
        Standard Elo K-factor.

    Returns
    -------
    (elo, sample_counts) : (dict[int, float], dict[int, int])
        ``elo`` is the updated rating; ``sample_counts`` is the number
        of map updates applied to each team.
    """
    cutoff_ts = cutoff.timestamp()
    elo = dict(base_elo)
    counts: dict[int, int] = {tid: 0 for tid in team_ids}

    # Gather all (timestamp, team_a, team_b, score_a, score_b) tuples
    # from every team's recent_matches (and per-map breakdowns).
    map_events: list[tuple[float, int, int, int, int]] = []
    seen_matches: set[int] = set()
    for tid in team_ids:
        team = client.get_team(tid)
        for series in team.recent_matches:
            if series.id is None or series.id in seen_matches:
                continue
            if series.start_time is None or series.start_time.timestamp() >= cutoff_ts:
                continue
            seen_matches.add(series.id)
            t1, t2 = series.team1_id, series.team2_id
            if t1 is None or t2 is None:
                continue
            # Walk each map (requires fetching the full match - cached)
            full = series.full
            for m in full.maps:
                if m.team1_score is None or m.team2_score is None:
                    continue
                map_events.append(
                    (series.start_time.timestamp(), t1, t2, m.team1_score, m.team2_score)
                )

    # Sort chronologically so updates compose properly
    map_events.sort(key=lambda e: e[0])

    for _ts, t1, t2, s1, s2 in map_events:
        # Only update Elo for teams in the tournament
        elo_1 = elo.get(t1)
        elo_2 = elo.get(t2)
        if elo_1 is None and elo_2 is None:
            continue
        # If one team isn't in the bracket, treat their Elo as
        # the world average (1500) so the update still uses opponent
        # quality information.
        if elo_1 is None:
            elo_1 = 1500.0
        if elo_2 is None:
            elo_2 = 1500.0
        expected_1 = 1.0 / (1.0 + 10.0 ** ((elo_2 - elo_1) / 400.0))
        actual_1 = 1.0 if s1 > s2 else 0.0
        margin = _margin_multiplier(s1 - s2)
        delta = k * margin * (actual_1 - expected_1)
        if t1 in elo:
            elo[t1] += delta
            counts[t1] += 1
        if t2 in elo:
            elo[t2] -= delta
            counts[t2] += 1

    return elo, counts


def build_team_strengths(
    client: "HLTVClient",
    team_ids: list[int],
    cutoff: datetime,
    half_life_days: float = 30.0,
    use_map_updates: bool = True,
    k_factor: float = 24.0,
    lineup_players_by_team: dict[int, list["Player"]] | None = None,
    form_matches_by_team: dict[int, list["Match"]] | None = None,
    map_pool_by_team: dict[int, dict[str, dict[str, float]]] | None = None,
    world_rank_by_team: dict[int, int] | None = None,
    vrs_points_by_team: dict[int, float] | None = None,
    stage_entry_records_by_team: dict[int, tuple[int, int]] | None = None,
    style_scores_by_team: dict[int, float] | None = None,
    use_player_trends: bool = True,
    use_event_history: bool = True,
    use_style_stats: bool = False,
) -> dict[int, TeamStrength]:
    """
    Fetch every team and compute its strength snapshot as of ``cutoff``.

    The Elo for each team starts from a rank-seeded base (``base_elo``).
    If ``use_map_updates`` is True (the default), every pre-cutoff map
    each team played is replayed via a margin-aware Elo update (Glicko-
    style margin multiplier). This captures:

      - **Opponent quality**: beating a top-5 team adjusts more than
        beating a bottom-50 team.
      - **Round differential**: 16-3 stomps move Elo more than 16-14
        squeakers; OT losses barely move it at all.

    The resulting ``elo`` is the **margin-aware adjusted** rating;
    ``base_elo`` and ``elo_delta`` are also stored for diagnostics.

    Parameters
    ----------
    client : HLTVClient
    team_ids : list[int]
    cutoff : datetime
        Hard cutoff. Matches played at or after this time are ignored to
        prevent look-ahead leakage when back-testing.
    half_life_days : float
        Recent-form decay half-life (legacy signal, still computed).
    use_map_updates : bool, default True
        If False, fall back to plain rank-derived Elo.
    k_factor : float, default 24.0
        Standard Elo K-factor for the map-level updates.
    lineup_players_by_team, form_matches_by_team, map_pool_by_team : optional
        Historical snapshots, usually taken from the event's opening-round
        MatchScreen payloads. They avoid leaking today's roster/form/map
        state into old Major backtests.
    world_rank_by_team, vrs_points_by_team : optional
        Stage-time ranking/VRS snapshots. Prefer these over today's team
        endpoint fields when backtesting.
    stage_entry_records_by_team : optional
        Previous-stage Swiss records for teams entering a later stage.
        This captures the Major-specific carryover when a team just
        qualified 3-0/3-1/3-2 into the next stage.
    use_player_trends : bool, default True
        If False, disables Rating 3.0 trend and 3-month-rating Elo nudges.
    use_event_history : bool, default True
        If False, disables cutoff-filtered big-event placement history.
    use_style_stats : bool, default False
        If False, disables post-match opening/pistol/flash/multi/clutch
        style aggregation. Current backtests keep this off by default
        because the aggregate is not predictive enough yet.

    Returns
    -------
    dict[int, TeamStrength]
    """
    teams = client.bulk_teams(team_ids)
    # Seed Elo from rank + roster
    base_elo: dict[int, float] = {}
    rosters: dict[int, float | None] = {}
    rosters_3mo: dict[int, float | None] = {}
    rising_share: dict[int, float] = {}
    falling_share: dict[int, float] = {}
    map_pools: dict[int, dict[str, dict[str, float]]] = {}
    vrs_points: dict[int, float | None] = {}
    big_scores: dict[int, float] = {}
    big_maps: dict[int, int] = {}
    stage_boosts: dict[int, float] = {}
    style_scores: dict[int, float] = {}

    lineup_players_by_team = lineup_players_by_team or {}
    form_matches_by_team = form_matches_by_team or {}
    map_pool_by_team = map_pool_by_team or {}
    world_rank_by_team = world_rank_by_team or {}
    vrs_points_by_team = vrs_points_by_team or {}
    stage_entry_records_by_team = stage_entry_records_by_team or {}
    style_scores_by_team = style_scores_by_team or {}

    # Prefetch all roster players in parallel for the trend signal. Snapshot
    # lineups already carry their own ratingPast3Months and should not force
    # today's PlayerScreen endpoint.
    all_player_ids: list[int] = []
    if use_player_trends:
        for t in teams:
            if lineup_players_by_team.get(t.id):
                continue
            for p in t.roster:
                if p.id is not None and p.id not in all_player_ids:
                    all_player_ids.append(p.id)
    if use_player_trends and all_player_ids:
        client.bulk_players(all_player_ids)

    for t in teams:
        world_rank = world_rank_by_team.get(t.id, t.world_rank)
        vrs_seed = vrs_points_by_team.get(t.id, t.vrs_rank)
        e = _elo_from_rank(world_rank, vrs_seed)
        snapshot_players = lineup_players_by_team.get(t.id) or []
        snapshot_ratings = [
            r for r in (p.rating_past_3_months or p.rating for p in snapshot_players)
            if r is not None
        ]
        roster = (
            sum(snapshot_ratings) / len(snapshot_ratings)
            if snapshot_ratings
            else t.average_roster_rating()
        )
        if roster is not None:
            e += 500.0 * (roster - 1.00)
        base_elo[t.id] = e
        rosters[t.id] = roster

        stage_record = stage_entry_records_by_team.get(t.id)
        stage_boost = 0.0
        if stage_record == (3, 0):
            stage_boost = 70.0
        elif stage_record == (3, 1):
            stage_boost = 38.0
        elif stage_record == (3, 2):
            stage_boost = 18.0
        if stage_boost:
            base_elo[t.id] += stage_boost
        stage_boosts[t.id] = stage_boost

        if use_event_history:
            event_score, event_maps = _big_event_pedigree(t, cutoff)
            # Big-event history is useful, but noisy because it is team/org
            # history rather than exact active-roster history.
            base_elo[t.id] += max(-20.0, min(55.0, 24.0 * event_score))
            big_scores[t.id] = event_score
            big_maps[t.id] = event_maps
        else:
            big_scores[t.id] = 0.0
            big_maps[t.id] = 0

        if use_style_stats:
            if t.id in style_scores_by_team:
                style_score = style_scores_by_team[t.id]
            else:
                style_source = form_matches_by_team.get(t.id) or t.recent_matches
                style_score = _style_score_from_matches(t.id, t.name, style_source, cutoff)
            base_elo[t.id] += max(-35.0, min(35.0, 55.0 * style_score))
            style_scores[t.id] = style_score
        else:
            style_scores[t.id] = 0.0

        # Per-player trend + 3-month rating
        ratings_3mo: list[float] = []
        n_rising = n_falling = n_total = 0
        players = snapshot_players if snapshot_players else t.roster
        for p in players:
            if not use_player_trends:
                continue
            full = p if snapshot_players else p.full
            stats = full.stats
            if stats is None:
                continue
            n_total += 1
            r3 = full.rating_past_3_months
            # ratingPast3Months only set on allPlayers from match payloads;
            # fall back to the headline rating
            r3 = r3 if r3 is not None else stats.rating
            if r3 is not None:
                ratings_3mo.append(r3)
            trend = stats.rating_trend
            if trend == "Rising":
                n_rising += 1
            elif trend == "Falling":
                n_falling += 1
        rosters_3mo[t.id] = (
            sum(ratings_3mo) / len(ratings_3mo) if ratings_3mo else None
        )
        rising_share[t.id] = n_rising / max(1, n_total)
        falling_share[t.id] = n_falling / max(1, n_total)

        if snapshot_ratings:
            rosters_3mo[t.id] = sum(snapshot_ratings) / len(snapshot_ratings)

        if use_player_trends:
            # Roster trend nudges Elo: each net "Rising" player ~ +6 Elo
            trend_net = rising_share[t.id] - falling_share[t.id]
            base_elo[t.id] += 30.0 * trend_net

            # 3-month rating boost on top of the season-long rating
            if rosters_3mo[t.id] is not None and roster is not None:
                base_elo[t.id] += 250.0 * (rosters_3mo[t.id] - roster)

        # Snapshot map pool (free from cached match payload)
        pool_dict: dict[str, dict[str, float]] = dict(map_pool_by_team.get(t.id) or {})
        if not pool_dict:
            for name, entry in t.map_pool().items():
                pool_dict[name] = {
                    "win_rate": entry.win_rate or 0.5,
                    "ct_win_rate": entry.ct_win_rate or 0.5,
                    "t_win_rate": entry.t_win_rate or 0.5,
                    "played": float(entry.played or 0),
                    "pick_pct": entry.pick_pct or 0.0,
                    "ban_pct": entry.ban_pct or 0.0,
                }
        map_pools[t.id] = pool_dict

        # VRS forecast: take the team's most recent match where we can
        # see *their* CurrentPoints
        vp: float | None = None
        if t.id in vrs_points_by_team:
            vp = vrs_points_by_team[t.id]
        else:
            for series in t.recent_matches:
                if series.id is None:
                    continue
                full = series.full
                f = full.vrs_forecast
                if not f:
                    continue
                if full.team1_id == t.id:
                    vp = f.get("team1CurrentPoints")
                elif full.team2_id == t.id:
                    vp = f.get("team2CurrentPoints")
                if vp:
                    break
        vrs_points[t.id] = vp

    # Margin-aware map updates on top of (already trend-adjusted) base
    if use_map_updates:
        adjusted_elo, map_counts = _walk_maps_for_elo(
            client, team_ids, cutoff, base_elo, k=k_factor
        )
    else:
        adjusted_elo = dict(base_elo)
        map_counts = {tid: 0 for tid in team_ids}

    out: dict[int, TeamStrength] = {}
    for t in teams:
        if form_matches_by_team.get(t.id):
            form, n = _recent_form_from_snapshot(form_matches_by_team[t.id])
        else:
            form, n = _recent_form_capped(t, cutoff, half_life_days)
        stage_record = stage_entry_records_by_team.get(t.id)
        if stage_record == (3, 0):
            form = max(form, 0.85)
        elif stage_record == (3, 1):
            form = max(form, 0.72)
        elif stage_record == (3, 2):
            form = max(form, 0.62)
        out[t.id] = TeamStrength(
            team_id=t.id,
            name=t.name,
            world_rank=world_rank_by_team.get(t.id, t.world_rank),
            vrs_rank=t.vrs_rank,
            elo=adjusted_elo[t.id],
            base_elo=base_elo[t.id],
            elo_delta=adjusted_elo[t.id] - base_elo[t.id],
            roster_rating=rosters[t.id],
            recent_form=form,
            sample_n=n,
            map_sample_n=map_counts.get(t.id, 0),
            roster_rating_3mo=rosters_3mo[t.id],
            rising_player_share=rising_share[t.id],
            falling_player_share=falling_share[t.id],
            map_pool=map_pools[t.id],
            vrs_points=vrs_points[t.id],
            stage_entry_record=stage_record,
            stage_entry_boost=stage_boosts.get(t.id, 0.0),
            big_event_score=big_scores.get(t.id, 0.0),
            big_event_maps=big_maps.get(t.id, 0),
            style_score=style_scores.get(t.id, 0.0),
        )
    return out


def _elo_win_prob(a: float, b: float, scale: float = 400.0) -> float:
    return 1.0 / (1.0 + 10.0 ** ((b - a) / scale))


def _map_comfort(entry: dict[str, float]) -> float:
    played = entry.get("played", 0.0)
    wr = entry.get("win_rate", 0.5)
    pick = entry.get("pick_pct", 0.0)
    ban = entry.get("ban_pct", 0.0)
    ct = entry.get("ct_win_rate", 0.5)
    tt = entry.get("t_win_rate", 0.5)
    confidence = min(1.0, played / 10.0)
    shrunk_wr = 0.5 + (wr - 0.5) * confidence
    side_quality = ((ct + tt) / 2.0) - 0.5
    comfort = shrunk_wr + 0.10 * pick - 0.10 * ban + 0.08 * side_quality
    return max(0.05, min(0.95, comfort))


def matchup_p_bo1(
    a: TeamStrength,
    b: TeamStrength,
    *,
    form_blend: float = 0.08,
    h2h: tuple[int, int] | None = None,
    h2h_blend: float = 0.08,
    h2h_prior_weight: float = 2.0,
    vrs_blend: float = 0.12,
    map_name: str | None = None,
    map_blend: float = 0.12,
) -> float:
    """
    Single-map win probability of A vs B.

    Layers (each weighted nudge on top of the Elo prob):
      1. Elo
      2. Recent form (W/L exp-weighted)
      3. H2H Bayesian prior
      4. VRS forecast (HLTV's own model), if both teams have points
      5. Per-map win-rate edge, if ``map_name`` is provided

    Parameters
    ----------
    a, b : TeamStrength
    form_blend, h2h_blend, vrs_blend, map_blend : float
    h2h : (int, int), optional
    h2h_prior_weight : float
    map_name : str, optional
        Map being played. If supplied, the per-team
        :py:attr:`TeamStrength.map_pool` win-rate is folded in.
    """
    p = _elo_win_prob(a.elo, b.elo)

    p_form = 0.5 + 0.5 * (a.recent_form - b.recent_form)
    p_form = max(0.02, min(0.98, p_form))
    p = (1 - form_blend) * p + form_blend * p_form

    if a.vrs_points and b.vrs_points:
        p_vrs = _elo_win_prob(a.vrs_points, b.vrs_points)
        p = (1 - vrs_blend) * p + vrs_blend * p_vrs

    if h2h is not None:
        aw, bw = h2h
        p_h2h = (aw + h2h_prior_weight * 0.5) / (aw + bw + h2h_prior_weight)
        p = (1 - h2h_blend) * p + h2h_blend * p_h2h

    if map_name is not None:
        wa = a.map_pool.get(map_name, {})
        wb = b.map_pool.get(map_name, {})
        played_a = wa.get("played", 0)
        played_b = wb.get("played", 0)
        # Need both teams to have >=2 maps played for it to be meaningful
        if played_a >= 2 and played_b >= 2:
            score_a = _map_comfort(wa)
            score_b = _map_comfort(wb)
            # Map edge from win rate + pick/ban comfort, with shrinkage
            # for low sample sizes.
            p_map = 0.5 + 0.55 * (score_a - score_b)
            p_map = max(0.05, min(0.95, p_map))
            p = (1 - map_blend) * p + map_blend * p_map

    return max(0.02, min(0.98, p))


def matchup_p_bo3(p_map: float) -> float:
    """
    Convert a per-map win probability to BO3 series win probability.

    ``P(win 2 of 3) = p^2 + 2 * p^2 * (1 - p) = p^2 * (3 - 2*p)``.
    """
    return p_map * p_map * (3.0 - 2.0 * p_map)


# Current active map pool. Use this list as the BO3 universe; veto picks
# from these 7.
ACTIVE_MAP_POOL: tuple[str, ...] = (
    "Dust2",
    "Mirage",
    "Inferno",
    "Nuke",
    "Ancient",
    "Anubis",
    "Train",
    "Overpass",
)


def _map_score(team: TeamStrength, name: str) -> float:
    """
    Score a map for ``team`` from its pool, prefer-played maps with
    higher win-rate. Returns 0 if the team has no data on this map.
    """
    entry = team.map_pool.get(name)
    if not entry:
        return 0.0
    wr = entry.get("win_rate", 0.5)
    played = entry.get("played", 0)
    if played < 1:
        return 0.0
    # Confidence-weighted score: more games played -> trust win-rate more,
    # otherwise pull toward 0.5
    confidence = min(1.0, played / 8.0)
    return 0.5 + (wr - 0.5) * confidence


def simulate_bo3_with_veto(
    a: TeamStrength,
    b: TeamStrength,
    rng,
    *,
    h2h: tuple[int, int] | None = None,
    pool: tuple[str, ...] = ACTIVE_MAP_POOL,
    p_map_fn: Callable[[TeamStrength, TeamStrength, tuple[int, int] | None, str], float] | None = None,
) -> tuple[bool, list[tuple[str, bool]]]:
    """
    Simulate a single BO3 between A and B with realistic veto.

    Veto pattern: A bans -> B bans -> A picks -> B picks -> A bans -> B bans
    -> decider (last remaining map).

    Each team's ban target is the opponent's strongest map; each team's
    pick is its own strongest remaining map.

    The resulting 3 maps are played in order; the first team to 2 wins
    the series. Returns ``(a_won, [(map, a_won_map), ...])``.

    ``p_map_fn`` lets backtests and ablations use the same probability
    variant in BO3s that they use in BO1s.
    """
    available = list(pool)
    # If pool is bigger than 7, prune to top by combined play count
    if len(available) > 7:
        available.sort(
            key=lambda m: -(a.map_pool.get(m, {}).get("played", 0)
                             + b.map_pool.get(m, {}).get("played", 0))
        )
        available = available[:7]

    def ban(remover: TeamStrength, victim: TeamStrength) -> str:
        # Remove the victim's strongest remaining map (worst for the
        # remover). Ties broken randomly.
        scored = [(m, _map_score(victim, m)) for m in available]
        top_score = max(s for _, s in scored)
        candidates = [m for m, s in scored if s == top_score]
        return rng.choice(candidates)

    def pick(picker: TeamStrength) -> str:
        scored = [(m, _map_score(picker, m)) for m in available]
        top_score = max(s for _, s in scored)
        candidates = [m for m, s in scored if s == top_score]
        return rng.choice(candidates)

    maps_to_play: list[str] = []
    # Order: A ban, B ban, A pick, B pick, A ban, B ban, decider
    available.remove(ban(a, b))
    available.remove(ban(b, a))
    pa = pick(a); maps_to_play.append(pa); available.remove(pa)
    pb = pick(b); maps_to_play.append(pb); available.remove(pb)
    available.remove(ban(a, b))
    available.remove(ban(b, a))
    maps_to_play.append(available[0])  # decider

    # Play out the maps
    a_wins = b_wins = 0
    history: list[tuple[str, bool]] = []
    for m in maps_to_play:
        p = (
            p_map_fn(a, b, h2h, m)
            if p_map_fn is not None
            else matchup_p_bo1(a, b, h2h=h2h, map_name=m)
        )
        a_won = rng.random() < p
        history.append((m, a_won))
        if a_won:
            a_wins += 1
        else:
            b_wins += 1
        if a_wins == 2 or b_wins == 2:
            break
    return a_wins == 2, history
