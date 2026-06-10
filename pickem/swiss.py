"""
Monte Carlo simulator for the HLTV Major Swiss-system format.

Rules implemented:
  - 16 teams, single Swiss bracket, 8 advance.
  - Each team plays until they reach 3 wins (advance) or 3 losses
    (eliminated). No team plays more than 5 matches.
  - Within a round, teams are paired with another team that has the same
    win/loss record.
  - No team plays the same opponent twice within the stage.
  - Match format follows HLTV's mixed rule:
      * 0-0, 1-0, 0-1, 1-1, 2-1, 1-2 records -> BO1
      * 2-0, 0-2 (and any X-2 / 2-X that decides advance/elim) -> BO3
    Operationally: a match is BO3 iff a win advances or a loss
    eliminates one of the teams.
  - After round 1, each record bucket is ordered by Valve Difficulty
    Score (opponent wins minus losses), then by the stage's initial seed.
    The highest-ranked team is paired downward against the lowest-ranked
    available team while avoiding rematches.

Output is per-team final record over many simulations.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable

from .model import TeamStrength, matchup_p_bo1, matchup_p_bo3, simulate_bo3_with_veto


@dataclass
class FinalRecord:
    """Per-team distribution of (wins, losses) across simulations."""

    team_id: int
    name: str
    counts: Counter = field(default_factory=Counter)  # (w, l) -> n

    def n(self) -> int:
        return sum(self.counts.values())

    def p(self, w: int, l: int) -> float:
        return self.counts.get((w, l), 0) / max(1, self.n())

    def p_advance(self) -> float:
        return sum(c for (w, _), c in self.counts.items() if w >= 3) / max(
            1, self.n()
        )

    def p_3_0(self) -> float:
        return self.p(3, 0)

    def p_3_1(self) -> float:
        return self.p(3, 1)

    def p_3_2(self) -> float:
        return self.p(3, 2)

    def p_0_3(self) -> float:
        return self.p(0, 3)

    def p_1_3(self) -> float:
        return self.p(1, 3)

    def p_2_3(self) -> float:
        return self.p(2, 3)

    def __repr__(self) -> str:
        return (
            f"<FinalRecord {self.name} 3-0={self.p_3_0():.2f} "
            f"3-1={self.p_3_1():.2f} 3-2={self.p_3_2():.2f} "
            f"0-3={self.p_0_3():.2f} adv={self.p_advance():.2f}>"
        )


def _is_decisive(w: int, l: int) -> bool:
    """A match between teams at this record is BO3 because at least one
    team will advance or be eliminated by it."""
    return w == 2 or l == 2


def _difficulty(tid: int, played: dict[int, set[int]], record: dict[int, list[int]]) -> int:
    """Valve Difficulty Score: opponent wins minus opponent losses."""
    return sum(record[opp][0] - record[opp][1] for opp in played[tid])


_SIX_TEAM_PAIRING_PRIORITIES = (
    ((0, 5), (1, 4), (2, 3)),
    ((0, 5), (1, 3), (2, 4)),
    ((0, 4), (1, 5), (2, 3)),
    ((0, 4), (1, 3), (2, 5)),
    ((0, 3), (1, 5), (2, 4)),
    ((0, 3), (1, 4), (2, 5)),
    ((0, 5), (1, 2), (3, 4)),
    ((0, 4), (1, 2), (3, 5)),
    ((0, 2), (1, 5), (3, 4)),
    ((0, 2), (1, 4), (3, 5)),
    ((0, 3), (1, 2), (4, 5)),
    ((0, 2), (1, 3), (4, 5)),
    ((0, 1), (2, 5), (3, 4)),
    ((0, 1), (2, 4), (3, 5)),
    ((0, 1), (2, 3), (4, 5)),
)


def _seeded_pairings(
    tids: list[int],
    played: dict[int, set[int]],
    round_number: int,
) -> list[tuple[int, int]]:
    """Apply Valve's seeded Swiss pairing and rematch priorities."""
    if len(tids) == 6 and round_number >= 4:
        for pattern in _SIX_TEAM_PAIRING_PRIORITIES:
            pairs = [(tids[a], tids[b]) for a, b in pattern]
            if all(b not in played[a] for a, b in pairs):
                return pairs

    paired: list[tuple[int, int]] = []
    taken: set[int] = set()
    n = len(tids)
    for i in range(n // 2):
        a = tids[i]
        if a in taken:
            continue
        for j in range(n - 1, i, -1):
            b = tids[j]
            if b in taken or b in played[a]:
                continue
            paired.append((a, b))
            taken.add(a)
            taken.add(b)
            break
    leftover = [tid for tid in tids if tid not in taken]
    for i in range(0, len(leftover) - 1, 2):
        paired.append((leftover[i], leftover[i + 1]))
    return paired


def _simulate_one(
    teams: dict[int, TeamStrength],
    h2h: dict[tuple[int, int], tuple[int, int]] | None,
    rng: random.Random,
    p_bo1: Callable[[TeamStrength, TeamStrength, tuple[int, int] | None], float],
    p_map: Callable[[TeamStrength, TeamStrength, tuple[int, int] | None, str], float] | None = None,
    bo3_match: Callable[[TeamStrength, TeamStrength, tuple[int, int] | None, random.Random], bool] | None = None,
    r1_pairings: list[tuple[int, int]] | None = None,
    use_buchholz: bool = True,
    all_matches_bo3: bool = False,
    initial_seeds: dict[int, int] | None = None,
) -> dict[int, tuple[int, int]]:
    """
    Run one Swiss bracket. Returns ``{team_id: (wins, losses)}``.

    Pairing within a record bucket uses Buchholz-style seeding:
    sort by Buchholz score (descending), then pair the strongest with
    the weakest, second-strongest with second-weakest, etc. This
    reproduces HLTV's actual pairing rule.
    """
    active = {tid for tid in teams}
    record: dict[int, list[int]] = {tid: [0, 0] for tid in teams}
    played: defaultdict[int, set[int]] = defaultdict(set)
    if initial_seeds is None:
        initial_seeds = {
            tid: index
            for index, tid in enumerate(
                sorted(teams, key=lambda team_id: -teams[team_id].elo),
                start=1,
            )
        }
    round_number = 1

    while True:
        # Group active teams by record
        buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        for tid in active:
            buckets[tuple(record[tid])].append(tid)
        # Any decisive matches to play?
        any_match = False
        # Iterate buckets in deterministic order (sort by wins desc)
        for (w, l), tids in sorted(buckets.items(), key=lambda x: (-x[0][0], x[0][1])):
            paired: list[tuple[int, int]] = []
            taken: set[int] = set()
            # Round 1 (0-0 bucket): use the actual HLTV-published R1
            # pairings if available; otherwise random.
            if (w, l) == (0, 0) and r1_pairings:
                for a, b in r1_pairings:
                    if a in tids and b in tids and a not in taken and b not in taken:
                        paired.append((a, b))
                        taken.add(a); taken.add(b)
                # Any teams in the bucket not covered by preset pairings
                # get random pairing among themselves
                extra = [t for t in tids if t not in taken]
                rng.shuffle(extra)
                for i in range(0, len(extra) - 1, 2):
                    paired.append((extra[i], extra[i + 1]))
                    taken.add(extra[i]); taken.add(extra[i + 1])
            else:
                # Subsequent rounds: current record, Difficulty Score, then
                # the stage's initial seed, exactly as the Major rules specify.
                if (w, l) == (0, 0):
                    rng.shuffle(tids)
                elif not use_buchholz:
                    rng.shuffle(tids)
                else:
                    tids.sort(
                        key=lambda t: (
                            -_difficulty(t, played, record),
                            initial_seeds.get(t, 999),
                        ),
                    )
                paired = _seeded_pairings(tids, played, round_number)
            # Play each pairing
            decisive_round = all_matches_bo3 or _is_decisive(w, l)
            for a, b in paired:
                any_match = True
                ta, tb = teams[a], teams[b]
                pair_key = (min(a, b), max(a, b))
                h = None
                if h2h is not None:
                    h_raw = h2h.get(pair_key)
                    if h_raw is not None:
                        # h2h stored as (low_wins, high_wins); reorient
                        if a < b:
                            h = h_raw
                        else:
                            h = (h_raw[1], h_raw[0])
                if decisive_round:
                    if bo3_match is not None:
                        won = bo3_match(ta, tb, h, rng)
                    else:
                        a_won, _maps = simulate_bo3_with_veto(ta, tb, rng, h2h=h, p_map_fn=p_map)
                        won = a_won
                else:
                    p_win = p_bo1(ta, tb, h)
                    won = rng.random() < p_win
                if won:
                    # a wins
                    record[a][0] += 1
                    record[b][1] += 1
                else:
                    record[b][0] += 1
                    record[a][1] += 1
                played[a].add(b)
                played[b].add(a)
                # Drop teams that are done
                if record[a][0] >= 3 or record[a][1] >= 3:
                    active.discard(a)
                if record[b][0] >= 3 or record[b][1] >= 3:
                    active.discard(b)
        if not any_match:
            break
        round_number += 1

    return {tid: (record[tid][0], record[tid][1]) for tid in teams}


def simulate_swiss_with_sims(
    teams: dict[int, TeamStrength],
    n_sims: int = 50_000,
    h2h: dict[tuple[int, int], tuple[int, int]] | None = None,
    seed: int | None = None,
    p_bo1: Callable[[TeamStrength, TeamStrength, tuple[int, int] | None], float] | None = None,
    p_map: Callable[[TeamStrength, TeamStrength, tuple[int, int] | None, str], float] | None = None,
    bo3_match: Callable[[TeamStrength, TeamStrength, tuple[int, int] | None, random.Random], bool] | None = None,
    r1_pairings: list[tuple[int, int]] | None = None,
    use_buchholz: bool = True,
    all_matches_bo3: bool = False,
    initial_seeds: dict[int, int] | None = None,
) -> tuple[dict[int, FinalRecord], list[dict[int, tuple[int, int]]]]:
    """
    Same as :func:`simulate_swiss` but also returns the raw per-sim
    outcome list, needed by :func:`pickem.optimize.score_ticket` to
    compute joint expectations (capturing correlations like "two teams
    that meet can't both 3-0").
    """
    if len(teams) != 16:
        raise ValueError(f"Swiss expects 16 teams, got {len(teams)}")
    rng = random.Random(seed)
    if p_bo1 is None:
        p_bo1 = lambda a, b, h: matchup_p_bo1(a, b, h2h=h)

    out: dict[int, FinalRecord] = {
        tid: FinalRecord(team_id=tid, name=t.name)
        for tid, t in teams.items()
    }
    sims: list[dict[int, tuple[int, int]]] = []
    for _ in range(n_sims):
        result = _simulate_one(
            teams,
            h2h,
            rng,
            p_bo1,
            p_map=p_map,
            bo3_match=bo3_match,
            r1_pairings=r1_pairings,
            use_buchholz=use_buchholz,
            all_matches_bo3=all_matches_bo3,
            initial_seeds=initial_seeds,
        )
        sims.append(result)
        for tid, rec in result.items():
            out[tid].counts[rec] += 1
    return out, sims


def simulate_swiss(
    teams: dict[int, TeamStrength],
    n_sims: int = 50_000,
    h2h: dict[tuple[int, int], tuple[int, int]] | None = None,
    seed: int | None = None,
    p_bo1: Callable[[TeamStrength, TeamStrength, tuple[int, int] | None], float] | None = None,
    p_map: Callable[[TeamStrength, TeamStrength, tuple[int, int] | None, str], float] | None = None,
    bo3_match: Callable[[TeamStrength, TeamStrength, tuple[int, int] | None, random.Random], bool] | None = None,
    r1_pairings: list[tuple[int, int]] | None = None,
    use_buchholz: bool = True,
    all_matches_bo3: bool = False,
    initial_seeds: dict[int, int] | None = None,
) -> dict[int, FinalRecord]:
    """
    Run ``n_sims`` Swiss brackets and accumulate per-team final-record
    distributions.

    Parameters
    ----------
    teams : dict[int, TeamStrength]
        Must have exactly 16 entries.
    n_sims : int, default 50_000
    h2h : dict[(low_id, high_id), (low_wins, high_wins)], optional
        Pre-aggregated H2H counts. Pair keys are sorted (lower id first).
    seed : int, optional
    p_bo1 : callable, optional
        Per-map win-prob function ``(a, b, h2h_pair_or_None) -> float``.
        Defaults to :func:`pickem.model.matchup_p_bo1`.

    Returns
    -------
    dict[int, FinalRecord]
    """
    if len(teams) != 16:
        raise ValueError(f"Swiss expects 16 teams, got {len(teams)}")
    rng = random.Random(seed)
    if p_bo1 is None:
        p_bo1 = lambda a, b, h: matchup_p_bo1(a, b, h2h=h)

    out: dict[int, FinalRecord] = {
        tid: FinalRecord(team_id=tid, name=t.name)
        for tid, t in teams.items()
    }
    for _ in range(n_sims):
        result = _simulate_one(
            teams,
            h2h,
            rng,
            p_bo1,
            p_map=p_map,
            bo3_match=bo3_match,
            r1_pairings=r1_pairings,
            use_buchholz=use_buchholz,
            all_matches_bo3=all_matches_bo3,
            initial_seeds=initial_seeds,
        )
        for tid, rec in result.items():
            out[tid].counts[rec] += 1
    return out
