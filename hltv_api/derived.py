"""
Derived metrics layered on top of the typed models.

Nothing here hits the network directly - it all reads cached / already-
fetched data on :class:`hltv_api.models.Team`, :class:`Player`, etc.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Match, Team


# ---------------------------------------------------------------- elo


def elo_from_rank(world_rank: int | None, vrs_rank: int | None = None) -> float:
    """
    Seed an Elo-style rating from HLTV world rank (and optionally VRS
    rank, which is a points score where bigger = better).

    Parameters
    ----------
    world_rank : int, optional
        1-based HLTV world ranking position.
    vrs_rank : int, optional
        Valve Regional Standings points (e.g. 1985). If both are given,
        the result blends them.

    Returns
    -------
    float
        Elo-style rating. Roughly 2000 for rank 1, dropping ~15 per
        position, floored at 1200.
    """
    if not world_rank or world_rank <= 0:
        base = 1500.0
    else:
        base = max(1200.0, 2000.0 - 15.0 * (world_rank - 1))

    if vrs_rank is not None and vrs_rank > 0:
        # VRS points roughly span 0-2500. Treat the centred value as a
        # +/-200 nudge on top of the rank-derived base.
        vrs_offset = (vrs_rank - 1500) / 5.0
        base = 0.7 * base + 0.3 * (1500 + vrs_offset)
    return base


def win_prob_from_elo(elo_a: float, elo_b: float, scale: float = 400.0) -> float:
    """
    Logistic ``P(a beats b)`` from two Elo ratings.

    Parameters
    ----------
    elo_a, elo_b : float
    scale : float, default 400
        Elo scale parameter. 400 -> 10x rating gap means ~91% favourite.

    Returns
    -------
    float
        Probability in ``(0, 1)``.
    """
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / scale))


# ---------------------------------------------------------------- roster strength


def roster_strength(team: "Team", baseline: float = 1.00) -> float:
    """
    Roster bonus (positive = above average) derived from the active
    lineup's mean Rating 3.0.

    Parameters
    ----------
    team : Team
    baseline : float, default 1.00

    Returns
    -------
    float
        Roughly the gap between this roster's average rating and
        ``baseline``. A team averaging 1.10 returns 0.10.
    """
    avg = team.average_roster_rating()
    if avg is None:
        return 0.0
    return avg - baseline


# ---------------------------------------------------------------- form


def recent_form(team: "Team", half_life_days: int = 30) -> float:
    """
    Exponentially-weighted recent map record from ``team.recent_matches``.

    Parameters
    ----------
    team : Team
    half_life_days : int, default 30

    Returns
    -------
    float
        Weighted win share in ``[0, 1]``. Falls back to 0.5 if no recent
        data.
    """
    now = datetime.now(tz=timezone.utc).timestamp()
    decay = math.log(2) / (half_life_days * 86400.0)
    wnum = wden = 0.0
    for m in team.recent_matches:
        if m.start_time is None:
            continue
        age = max(0.0, now - m.start_time.timestamp())
        w = math.exp(-decay * age)
        wden += w
        if m.winner_team_id == team.id:
            wnum += w
    if wden == 0:
        return 0.5
    return wnum / wden


# ---------------------------------------------------------------- head-to-head prior


def h2h_prior(
    h2h: dict, prior_weight: float = 2.0
) -> float:
    """
    Bayesian-smoothed H2H win probability for *team_a*.

    Parameters
    ----------
    h2h : dict
        Either ``{"team_a_wins": x, "team_b_wins": y}`` (from
        :py:meth:`HLTVClient.head_to_head_full`) or
        ``{"wins": x, "losses": y, ...}`` (from
        :py:meth:`Team.head_to_head`).
    prior_weight : float, default 2.0
        Strength of the 0.5 prior. With 0 matches played, returns 0.5.
        After 2 wins / 0 losses with prior_weight=2 it's 0.67, not 1.0.

    Returns
    -------
    float
        P(team_a beats team_b) implied by H2H, smoothed.
    """
    a = h2h.get("team_a_wins", h2h.get("wins", 0)) or 0
    b = h2h.get("team_b_wins", h2h.get("losses", 0)) or 0
    return (a + prior_weight * 0.5) / (a + b + prior_weight)


# ---------------------------------------------------------------- compose


def vrs_forecast_prob(match: "Match") -> float | None:
    """
    Convert a match's ``vrs_forecast`` block (current points + win/lose
    point swings) into an implied ``P(team1 wins)``.

    The forecast deltas are the points each team *would* gain or lose;
    the size of the win-point delta vs lose-point delta is a strong
    proxy for the API's own model.

    Returns None if no forecast is present.
    """
    f = match.vrs_forecast
    if not f:
        return None
    p1 = f.get("team1CurrentPoints") or 0
    p2 = f.get("team2CurrentPoints") or 0
    if not p1 or not p2:
        return None
    return win_prob_from_elo(p1, p2, scale=400.0)


def matchup_win_prob(
    team_a: "Team",
    team_b: "Team",
    *,
    h2h: dict | None = None,
    h2h_blend: float = 0.15,
    form_blend: float = 0.10,
    roster_blend: float = 0.05,
    map_blend: float = 0.10,
    role_blend: float = 0.05,
) -> float:
    """
    Composite ``P(team_a beats team_b)`` for a BO3 series.

    Layers:
      1. Elo (from world rank + VRS),
      2. roster Rating 3.0 differential,
      3. recent-form differential,
      4. (optional) head-to-head Bayesian prior.

    Each non-Elo signal is blended with a small weight; they nudge the
    Elo number rather than overriding it.

    Parameters
    ----------
    team_a, team_b : Team
    h2h : dict, optional
        Output of :py:meth:`HLTVClient.head_to_head_full`. If omitted,
        H2H is skipped.
    h2h_blend, form_blend, roster_blend : float
        Weights for each adjustment layer, summed against the Elo prob.

    Returns
    -------
    float
        Probability in ``(0, 1)``.
    """
    elo_a = elo_from_rank(team_a.world_rank, team_a.vrs_rank)
    elo_b = elo_from_rank(team_b.world_rank, team_b.vrs_rank)

    # Roster differential as Elo nudge: 0.05 rating gap ~ 25 Elo
    elo_a += 500.0 * roster_strength(team_a)
    elo_b += 500.0 * roster_strength(team_b)

    p_elo = win_prob_from_elo(elo_a, elo_b)

    form_a = recent_form(team_a)
    form_b = recent_form(team_b)
    p_form = 0.5 + 0.5 * (form_a - form_b)
    p_form = max(0.02, min(0.98, p_form))

    p = (1 - form_blend) * p_elo + form_blend * p_form
    p = (1 - roster_blend) * p + roster_blend * p_elo  # roster already in elo, keep weight

    if h2h:
        p_h2h = h2h_prior(h2h)
        p = (1 - h2h_blend) * p + h2h_blend * p_h2h

    # Map-pool advantage - average of the team's win-rate edge across
    # all maps in the BO3 active pool.
    diffs = map_pool_win_diff(team_a, team_b)
    if diffs:
        # Average top-5 maps (drop their highest ban% on each side
        # implicitly by taking the overlap).
        mean_diff = sum(diffs.values()) / max(1, len(diffs))
        p_map = 0.5 + mean_diff  # diff is already a +/- delta in win-rate
        p_map = max(0.05, min(0.95, p_map))
        p = (1 - map_blend) * p + map_blend * p_map

    # Role balance - average of the 7 role-score differentials, scaled
    # to a +/-15-point nudge on the win prob.
    roles = matchup_role_balance(team_a, team_b)
    if roles:
        mean_role = sum(roles.values()) / max(1, len(roles))
        p_role = 0.5 + (mean_role / 200.0)  # 100 pts of role diff -> 50%
        p_role = max(0.05, min(0.95, p_role))
        p = (1 - role_blend) * p + role_blend * p_role

    return max(0.02, min(0.98, p))


# ---------------------------------------------------------------- map pool


def map_pool_overlap(team_a: "Team", team_b: "Team") -> set[str]:
    """
    Maps where both teams have a published win-rate in their per-team
    map pool (sourced free from any recent match payload).
    """
    a_pool = team_a.map_pool()
    b_pool = team_b.map_pool()
    return set(a_pool.keys()) & set(b_pool.keys())


def map_pool_win_diff(team_a: "Team", team_b: "Team") -> dict[str, float]:
    """
    Per-map ``A.winRate - B.winRate`` across the overlap. Positive
    values favour A; missing maps drop to 0.

    Useful when guessing which side has the map-pool advantage in a BO3.
    """
    a_pool = team_a.map_pool()
    b_pool = team_b.map_pool()
    out: dict[str, float] = {}
    for name in set(a_pool) | set(b_pool):
        a = a_pool.get(name)
        b = b_pool.get(name)
        a_wr = a.win_rate if a else None
        b_wr = b.win_rate if b else None
        if a_wr is None and b_wr is None:
            continue
        out[name] = (a_wr or 0.5) - (b_wr or 0.5)
    return out


# ---------------------------------------------------------------- player firepower


def team_firepower(team: "Team") -> float:
    """
    Mean ``firepower`` score (0-100) across the active 5 if available;
    falls back to mean Rating 3.0 x 65 if not.

    ``firepower`` is a richer signal than rating because it directly
    captures pure killing power per the HLTV scoring model.
    """
    scores = []
    for p in team.roster:
        # The roster-summary shape has only rating; full profile has stats.
        full = p.full
        if full.stats is not None and full.stats.firepower is not None:
            scores.append(full.stats.firepower)
    if scores:
        return sum(scores) / len(scores)
    avg_r = team.average_roster_rating()
    return (avg_r or 1.0) * 65.0


def matchup_role_balance(team_a: "Team", team_b: "Team") -> dict[str, float]:
    """
    Per-role differential ``A_mean - B_mean`` across the 0-100 role
    scores (firepower, entrying, opening, sniping, trading, clutching,
    utility). Positive favours A.

    Lazy-loads each player's full profile (cache-aware).
    """
    keys = (
        "firepower",
        "entrying",
        "opening",
        "sniping",
        "trading",
        "clutching",
        "utility",
    )

    def _means(team: "Team") -> dict[str, float | None]:
        sums: dict[str, list[int]] = {k: [] for k in keys}
        for p in team.roster:
            full = p.full
            if full.stats is None:
                continue
            for k in keys:
                v = getattr(full.stats, k)
                if v is not None:
                    sums[k].append(v)
        return {k: (sum(v) / len(v) if v else None) for k, v in sums.items()}

    a = _means(team_a)
    b = _means(team_b)
    return {
        k: ((a[k] or 0) - (b[k] or 0))
        for k in keys
        if a[k] is not None and b[k] is not None
    }
