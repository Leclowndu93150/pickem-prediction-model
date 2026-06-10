"""
Pickem ticket scoring + optimization.

HLTV Major Swiss pickem structure:
  - 2 teams to go 3-0  (5 pts each = 10 max)
  - 6 teams to go 3-1 or 3-2  (2 pts each = 12 max)
  - 2 teams to go 0-3  (5 pts each = 10 max)

Total upside: 32 points. The 6 "advance" picks must NOT include the
2 teams already picked for 3-0 (those slots are exclusive in HLTV's UI).

The optimizer computes the joint expected score by **replaying the
saved per-sim outcomes** (not the marginal probabilities), so it
accounts for the fact that, for example, two teams can't both go 3-0
if they meet in the bracket.
"""

from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

try:
    import numpy as np
    _HAVE_NUMPY = True
except ImportError:
    _HAVE_NUMPY = False

from .swiss import FinalRecord


# HLTV Major pickem scoring (standard 5/5/2):
PTS_3_0 = 5
PTS_0_3 = 5
PTS_ADV = 2


@dataclass(frozen=True)
class Ticket:
    """A single pickem ticket."""

    three_oh: tuple[int, int]
    advancers: tuple[int, ...]
    zero_three: tuple[int, int]

    def teams(self) -> set[int]:
        return set(self.three_oh) | set(self.advancers) | set(self.zero_three)


def marginal_probs(
    records: dict[int, FinalRecord],
) -> dict[int, dict[str, float]]:
    """
    Per-team marginals: P(3-0), P(3-1), P(3-2), P(2-3), P(1-3), P(0-3),
    P(advance).
    """
    out: dict[int, dict[str, float]] = {}
    for tid, fr in records.items():
        out[tid] = {
            "name": fr.name,
            "3-0": fr.p_3_0(),
            "3-1": fr.p_3_1(),
            "3-2": fr.p_3_2(),
            "0-3": fr.p_0_3(),
            "1-3": fr.p_1_3(),
            "2-3": fr.p_2_3(),
            "advance": fr.p_advance(),
        }
    return out


def _sims_table(
    records: dict[int, FinalRecord],
) -> list[dict[int, tuple[int, int]]]:
    """
    Reconstruct each simulation as a single mapping team_id -> (w, l).
    Each FinalRecord stores its own Counter; we recover a per-sim view by
    aligning the iteration order across teams. This only works because
    every team participates in every sim - which is the case here.
    """
    # Build per-team sequence of (record, count) and expand to a flat
    # list of records per team. They are aligned only if the sim order
    # was identical (it is - the simulator updates every team's counter
    # once per sim, in iteration order).
    # We can't actually recover sim order from the counters alone, so
    # this helper is approximate - use ``score_ticket_via_sims`` instead.
    raise NotImplementedError(
        "Re-run simulator with ``record_sims=True`` for joint scoring."
    )


def score_ticket(
    ticket: Ticket,
    records: dict[int, FinalRecord],
    sims: list[dict[int, tuple[int, int]]] | None = None,
) -> float:
    """
    Expected points for ``ticket``.

    If ``sims`` is provided (the raw per-sim outcomes from
    :func:`pickem.swiss.simulate_swiss_with_sims`), uses true joint
    expectation. Otherwise falls back to marginal-product
    approximation, which slightly overestimates because it assumes
    independence between picks.
    """
    if sims is not None:
        total = 0
        for sim in sims:
            score = 0
            for tid in ticket.three_oh:
                if sim[tid] == (3, 0):
                    score += PTS_3_0
            for tid in ticket.zero_three:
                if sim[tid] == (0, 3):
                    score += PTS_0_3
            for tid in ticket.advancers:
                w, _ = sim[tid]
                if w >= 3:
                    score += PTS_ADV
            total += score
        return total / max(1, len(sims))

    # Marginal fallback
    expected = 0.0
    for tid in ticket.three_oh:
        expected += PTS_3_0 * records[tid].p_3_0()
    for tid in ticket.zero_three:
        expected += PTS_0_3 * records[tid].p_0_3()
    for tid in ticket.advancers:
        expected += PTS_ADV * records[tid].p_advance()
    return expected


def _build_sim_matrices(
    records: dict[int, FinalRecord],
    sims: list[dict[int, tuple[int, int]]],
) -> tuple[list[int], "np.ndarray", "np.ndarray", "np.ndarray", "np.ndarray"]:
    """
    Vectorise the per-sim outcomes into four boolean matrices indexed
    by (sim_idx, team_idx):

      ``three_oh_mat[s, t]``       -- team t went 3-0 in sim s
      ``zero_three_mat[s, t]``     -- team t went 0-3 in sim s
      ``advance_mat[s, t]``        -- team t advanced (>=3 wins) in sim s
      ``advance_not_3_0_mat[s, t]``-- advanced **but not 3-0** (counts for
                                      the 6 "advance" slots, since a team
                                      placed in the advance slot that goes
                                      3-0 is wrong under Valve's rules:
                                      only teams that finish 3-1 or 3-2
                                      score for an advance slot)
    """
    team_ids = list(records.keys())
    idx = {tid: i for i, tid in enumerate(team_ids)}
    n_sims = len(sims)
    n_teams = len(team_ids)
    three_oh = np.zeros((n_sims, n_teams), dtype=bool)
    zero_three = np.zeros((n_sims, n_teams), dtype=bool)
    advance = np.zeros((n_sims, n_teams), dtype=bool)
    advance_not_3_0 = np.zeros((n_sims, n_teams), dtype=bool)
    for s, sim in enumerate(sims):
        for tid, (w, l) in sim.items():
            t = idx[tid]
            is_3_0 = (w, l) == (3, 0)
            is_0_3 = (w, l) == (0, 3)
            adv = w >= 3
            if is_3_0:
                three_oh[s, t] = True
            elif is_0_3:
                zero_three[s, t] = True
            if adv:
                advance[s, t] = True
                if not is_3_0:
                    advance_not_3_0[s, t] = True
    return team_ids, three_oh, zero_three, advance, advance_not_3_0


@dataclass(frozen=True)
class TicketScore:
    """All the metrics we care about for one ticket."""

    ticket: "Ticket"
    expected_points: float       # average HLTV-style points per sim
    expected_correct: float      # average # of correct picks per sim (0-10)
    p_at_least_5: float          # P(>=5 correct picks) - the medal threshold
    p_perfect_10: float          # P(all 10 correct)
    correct_dist: tuple[float, ...]  # P(exactly k correct), k=0..10

    def __repr__(self) -> str:
        return (
            f"<TicketScore P(>=5)={self.p_at_least_5:.3f} "
            f"E[correct]={self.expected_correct:.2f} "
            f"EV_pts={self.expected_points:.2f}>"
        )


def best_tickets(
    records: dict[int, FinalRecord],
    sims: list[dict[int, tuple[int, int]]] | None = None,
    top_k: int = 10,
    candidate_3_0: int = 6,
    candidate_0_3: int = 6,
    candidate_adv: int = 12,
    objective: str = "p_at_least_5",
) -> list[TicketScore]:
    """
    Enumerate plausible tickets and return the top ``top_k`` ranked by
    the chosen objective.

    Parameters
    ----------
    objective : str, default ``"p_at_least_5"``
        Which scalar to maximize. Options:
          - ``"p_at_least_5"`` - P(at least 5 of 10 picks correct).
            This is the Valve Major medal-threshold objective.
          - ``"expected_correct"`` - Maximize the mean count of correct
            picks. Closely tracks p_at_least_5 but less risk-aware.
          - ``"expected_points"`` - Maximize HLTV-style 5/5/2 weighted
            points (legacy objective).
          - ``"p_perfect"`` - Maximize P(all 10 correct). Only useful if
            you're chasing a perfect score; nearly always zero.

    Pruning to keep enumeration feasible:
      - Only the top-``candidate_3_0`` teams by P(3-0) are eligible for
        the 3-0 slots.
      - Only the top-``candidate_0_3`` teams by P(0-3) are eligible for
        the 0-3 slots.
      - For the 6 advancing slots, only the top-``candidate_adv`` by
        P(advance) (excluding teams already in 3-0 picks).

    Search space with defaults: ~47k tickets, scored in <2s with numpy.

    Returns
    -------
    list of TicketScore, sorted by the chosen objective desc.
    """
    if objective not in ("p_at_least_5", "expected_correct", "expected_points", "p_perfect"):
        raise ValueError(f"Unknown objective: {objective}")

    marg = marginal_probs(records)
    teams = list(records.keys())
    by_3_0 = sorted(teams, key=lambda t: -marg[t]["3-0"])[:candidate_3_0]
    by_0_3 = sorted(teams, key=lambda t: -marg[t]["0-3"])[:candidate_0_3]
    by_adv = sorted(teams, key=lambda t: -marg[t]["advance"])[:candidate_adv]

    if not (sims and _HAVE_NUMPY):
        raise RuntimeError(
            "best_tickets now requires numpy and a sims list. "
            "Run with simulate_swiss_with_sims and ensure numpy is installed."
        )

    team_ids, three_oh_mat, zero_three_mat, adv_mat, adv_not_3_0_mat = (
        _build_sim_matrices(records, sims)
    )
    col = {tid: i for i, tid in enumerate(team_ids)}
    n_sims = three_oh_mat.shape[0]

    # Per-pick correctness matrices (bool) and per-pick points matrices.
    three_oh_pts = three_oh_mat.astype(np.int32) * PTS_3_0
    zero_three_pts = zero_three_mat.astype(np.int32) * PTS_0_3
    # An "advance" slot is correct ONLY if the team finished 3-1 or 3-2
    # (not 3-0). A team placed in the advance slot that goes 3-0 scores
    # nothing on Valve's pickem.
    adv_pts = adv_not_3_0_mat.astype(np.int32) * PTS_ADV

    results: list[TicketScore] = []
    for three_oh_pair in itertools.combinations(by_3_0, 2):
        t0a, t0b = col[three_oh_pair[0]], col[three_oh_pair[1]]
        # Correctness booleans (one per pick) for the two 3-0 slots
        c_3_0_a = three_oh_mat[:, t0a]
        c_3_0_b = three_oh_mat[:, t0b]
        score_3_0 = three_oh_pts[:, t0a] + three_oh_pts[:, t0b]

        for zero_three_pair in itertools.combinations(by_0_3, 2):
            if set(three_oh_pair) & set(zero_three_pair):
                continue
            t1a, t1b = col[zero_three_pair[0]], col[zero_three_pair[1]]
            c_0_3_a = zero_three_mat[:, t1a]
            c_0_3_b = zero_three_mat[:, t1b]
            score_0_3 = zero_three_pts[:, t1a] + zero_three_pts[:, t1b]

            adv_pool = [
                t for t in by_adv
                if t not in three_oh_pair and t not in zero_three_pair
            ]
            if len(adv_pool) < 6:
                continue

            for advancers in itertools.combinations(adv_pool, 6):
                cols = [col[t] for t in advancers]
                # Correctness per advance pick = team advanced AND not 3-0
                c_adv = adv_not_3_0_mat[:, cols]
                # Total correct picks per sim (vectorised)
                n_correct = (
                    c_3_0_a.astype(np.int8)
                    + c_3_0_b.astype(np.int8)
                    + c_0_3_a.astype(np.int8)
                    + c_0_3_b.astype(np.int8)
                    + c_adv.sum(axis=1, dtype=np.int8)
                )

                # Distribution of correct picks (0..10)
                counts = np.bincount(n_correct, minlength=11)
                dist = (counts / n_sims).astype(np.float64)

                p_at_least_5 = float(dist[5:].sum())
                p_perfect = float(dist[10])
                expected_correct = float(n_correct.mean())

                score_adv = adv_pts[:, cols].sum(axis=1)
                ev_points = float((score_3_0 + score_0_3 + score_adv).mean())

                results.append(
                    TicketScore(
                        ticket=Ticket(three_oh_pair, advancers, zero_three_pair),
                        expected_points=ev_points,
                        expected_correct=expected_correct,
                        p_at_least_5=p_at_least_5,
                        p_perfect_10=p_perfect,
                        correct_dist=tuple(dist),
                    )
                )

    key_fn = {
        "p_at_least_5": lambda r: -r.p_at_least_5,
        "expected_correct": lambda r: -r.expected_correct,
        "expected_points": lambda r: -r.expected_points,
        "p_perfect": lambda r: -r.p_perfect_10,
    }[objective]
    results.sort(key=key_fn)
    return results[:top_k]


def explain_ticket(
    ticket: Ticket,
    records: dict[int, FinalRecord],
    sims: list[dict[int, tuple[int, int]]] | None = None,
    score: TicketScore | None = None,
) -> str:
    """Pretty-print a single ticket with marginal probabilities and,
    if a :class:`TicketScore` is provided, the medal-relevant numbers."""
    marg = marginal_probs(records)
    lines = []
    lines.append(f"  3-0 picks:")
    for tid in ticket.three_oh:
        m = marg[tid]
        lines.append(f"    {m['name']:<22}  P(3-0)={m['3-0']:.2f}")
    lines.append(f"  Advance (3-1 or 3-2) picks:")
    for tid in ticket.advancers:
        m = marg[tid]
        # Valve scoring: the advance slot is correct ONLY if the team
        # finished 3-1 or 3-2 (not 3-0). So the relevant prob is
        # P(3-1) + P(3-2), not P(advance).
        p_adv_valid = m["3-1"] + m["3-2"]
        lines.append(
            f"    {m['name']:<22}  P(3-1 or 3-2)={p_adv_valid:.2f}  "
            f"[P(3-1)={m['3-1']:.2f}  P(3-2)={m['3-2']:.2f}]"
        )
    lines.append(f"  0-3 picks:")
    for tid in ticket.zero_three:
        m = marg[tid]
        lines.append(f"    {m['name']:<22}  P(0-3)={m['0-3']:.2f}")
    if score is not None:
        lines.append("")
        lines.append(
            f"  -> P(>=5 correct, medal threshold): {score.p_at_least_5*100:>5.1f}%"
        )
        lines.append(
            f"  -> E[correct picks]: {score.expected_correct:.2f} / 10"
        )
        lines.append(
            f"  -> Distribution of correct picks: "
            + ", ".join(
                f"{k}={p*100:.1f}%" for k, p in enumerate(score.correct_dist)
                if p >= 0.005
            )
        )
        lines.append(f"  -> EV (HLTV 5/5/2 scoring): {score.expected_points:.2f}")
    return "\n".join(lines)
