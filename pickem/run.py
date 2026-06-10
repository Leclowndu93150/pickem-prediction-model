"""
End-to-end pickem analysis runner.

Usage:
    python -m pickem.run --event-id <id> --cutoff <iso8601> --n-sims <n>

Only data published before ``cutoff`` is used to build the model.
If the event is finished, the top ticket is also scored against the
actual final records for a quick backtest.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone

from hltv_api import HLTVClient
from pickem.history import archived_records, find_event_archive_by_name
from pickem.model import build_team_strengths
from pickem.optimize import (
    PTS_3_0,
    PTS_0_3,
    PTS_ADV,
    Ticket,
    best_tickets,
    explain_ticket,
    marginal_probs,
    score_ticket,
)
from pickem.simulator_config import (
    simulator_initial_seeds,
    simulator_round_one_pairings,
)
from pickem.swiss import simulate_swiss_with_sims


def collect_h2h(
    client: HLTVClient,
    team_ids: list[int],
    cutoff: datetime,
) -> dict[tuple[int, int], tuple[int, int]]:
    """
    Build a pair-level H2H counter from each team's pre-cutoff
    recentMatches list, deduped by match id.
    """
    cutoff_ts = cutoff.timestamp()
    teams_set = set(team_ids)
    seen: set[int] = set()
    h2h: dict[tuple[int, int], list[int]] = {}
    for tid in team_ids:
        t = client.get_team(tid)
        for m in t.recent_matches:
            if m.id is None or m.id in seen:
                continue
            if m.start_time is None or m.start_time.timestamp() >= cutoff_ts:
                continue
            t1, t2, w = m.team1_id, m.team2_id, m.winner_team_id
            if t1 is None or t2 is None or w is None:
                continue
            if t1 not in teams_set or t2 not in teams_set:
                continue
            seen.add(m.id)
            lo, hi = (t1, t2) if t1 < t2 else (t2, t1)
            entry = h2h.setdefault((lo, hi), [0, 0])
            if w == lo:
                entry[0] += 1
            else:
                entry[1] += 1
    return {k: (v[0], v[1]) for k, v in h2h.items()}


def extract_r1_pairings(
    client: HLTVClient, event_id: int
) -> list[tuple[int, int]]:
    """
    Return the actual Round 1 pairings from the API's match schedule.

    Looks at the earliest-timestamped eight matches where both teams are
    set (no TBD placeholders). This supports both mixed BO1/BO3 stages
    and all-BO3 Swiss stages.

    Returns
    -------
    list of (team_a_id, team_b_id)
        Empty if no R1 pairings are known yet.
    """
    raw = client.event(event_id)
    pairings: list[tuple[int, int, str]] = []
    for m in raw.get("results", []) + raw.get("matches", []):
        t1 = m.get("team1") or {}
        t2 = m.get("team2") or {}
        t1_id = t1.get("teamId")
        t2_id = t2.get("teamId")
        if not (t1_id and t2_id):
            continue
        start = m.get("startDateTime") or ""
        pairings.append((int(t1_id), int(t2_id), start))
    # Deduplicate (results and matches can overlap) by sorted pair-id
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for t1, t2, start in sorted(pairings, key=lambda p: p[2]):
        key = (min(t1, t2), max(t1, t2))
        if key in seen:
            continue
        seen.add(key)
        out.append((t1, t2))
        if len(out) >= 8:
            break
    return out


def actual_records(client: HLTVClient, event_id: int) -> dict[int, tuple[int, int]]:
    """
    Reconstruct the actual final (wins, losses) per team from a finished
    event's ``results`` list.
    """
    ev = client.get_event(event_id)
    record: dict[int, list[int]] = {tid: [0, 0] for tid in ev.team_ids}
    for m in ev.results:
        t1, t2, w = m.team1_id, m.team2_id, m.winner_team_id
        if t1 is None or t2 is None or w is None:
            continue
        if w == t1:
            record[t1][0] += 1
            record[t2][1] += 1
        else:
            record[t2][0] += 1
            record[t1][1] += 1
    return {tid: (v[0], v[1]) for tid, v in record.items()}


def _previous_stage_name(name: str | None) -> str | None:
    if not name:
        return None
    if name.endswith(" Stage 1") or "Opening Stage" in name:
        return None
    if name.endswith(" Stage 2"):
        return name[: -len(" Stage 2")] + " Stage 1"
    if name.endswith(" Stage 3"):
        return name[: -len(" Stage 3")] + " Stage 2"
    return name + " Stage 2"


def _find_finished_event_by_name(client: HLTVClient, name: str):
    try:
        data = client.events()
        for block_name in ("completed", "ongoing", "upcoming"):
            for entry in (data.get(block_name) or {}).get("events") or []:
                if entry.get("name") == name:
                    return client.get_event(int(entry["eventId"]))
    except Exception:
        pass
    for offset in range(0, 18):
        raw = client.finished_events(offset)
        for entry in (raw.get("events") or {}).get("events") or []:
            if entry.get("name") == name:
                return client.get_event(int(entry["eventId"]))
    return None


def previous_stage_records(
    client: HLTVClient, event_name: str | None, team_ids: list[int]
) -> dict[int, tuple[int, int]]:
    """Records from the previous Major stage for teams entering this stage."""
    prev_name = _previous_stage_name(event_name)
    if not prev_name:
        return {}
    archived = find_event_archive_by_name(prev_name)
    if archived is not None:
        prev_records = archived_records(archived)
        current = set(team_ids)
        return {
            tid: rec
            for tid, rec in prev_records.items()
            if tid in current and rec[0] >= 3
        }
    prev = _find_finished_event_by_name(client, prev_name)
    if prev is None:
        return {}
    prev_records = actual_records(client, prev.id)
    current = set(team_ids)
    return {tid: rec for tid, rec in prev_records.items() if tid in current and rec[0] >= 3}


def score_against_truth(ticket: Ticket, truth: dict[int, tuple[int, int]]) -> int:
    score = 0
    for tid in ticket.three_oh:
        if truth.get(tid) == (3, 0):
            score += PTS_3_0
    for tid in ticket.zero_three:
        if truth.get(tid) == (0, 3):
            score += PTS_0_3
    for tid in ticket.advancers:
        w, _ = truth.get(tid, (0, 0))
        if w >= 3:
            score += PTS_ADV
    return score


def main(event_id: int, cutoff: datetime, n_sims: int) -> int:
    client = HLTVClient()
    ev = client.get_event(event_id)
    team_ids = ev.team_ids
    print(f"event: {ev.name}  ({len(team_ids)} teams)  cutoff={cutoff.isoformat()}")
    if len(team_ids) != 16:
        print(f"  WARNING: expected 16 teams, found {len(team_ids)}")

    print("\nBuilding team strengths (pre-cutoff data only)...")
    stage_records = previous_stage_records(client, ev.name, team_ids)
    if stage_records:
        print("  Previous-stage carryover records:")
        for tid, rec in sorted(stage_records.items(), key=lambda kv: (-kv[1][0], kv[1][1])):
            print(f"    {client.get_team(tid).name:<22} {rec[0]}-{rec[1]}")
    strengths = build_team_strengths(
        client,
        team_ids,
        cutoff,
        stage_entry_records_by_team=stage_records,
    )
    print(f"  {'team':<22} #rank  base   elo  d_form  legacy_form  n(series/maps)")
    for s in sorted(strengths.values(), key=lambda x: -x.elo):
        rank = str(s.world_rank or "?")
        print(
            f"  {s.name:<22} #{rank:<3}   "
            f"{s.base_elo:>4.0f}  {s.elo:>4.0f}  {s.elo_delta:+5.0f}   "
            f"{s.recent_form:.2f}      {s.sample_n}/{s.map_sample_n}"
        )

    print("\nCollecting H2H from recent (pre-cutoff) matches...")
    h2h = collect_h2h(client, team_ids, cutoff)
    print(f"  {len(h2h)} pair-level H2H records (out of {len(team_ids)*(len(team_ids)-1)//2})")

    print("\nExtracting actual R1 pairings from API...")
    r1 = extract_r1_pairings(client, event_id)
    simulator_r1 = simulator_round_one_pairings(event_id, set(team_ids))
    if simulator_r1:
        r1 = simulator_r1
    if r1:
        names = {tid: strengths[tid].name for tid in team_ids if tid in strengths}
        for a, b in r1:
            print(f"  {names.get(a, a):<22} vs  {names.get(b, b)}")
    else:
        print("  (no R1 pairings known yet - sims will pair R1 randomly)")

    min_maps_seen: list[int] = []
    for raw_match in ev.raw.get("results", []) + ev.raw.get("matches", []):
        t1 = (raw_match.get("team1") or {}).get("teamId")
        t2 = (raw_match.get("team2") or {}).get("teamId")
        min_maps = raw_match.get("minMaps")
        if t1 and t2 and min_maps is not None:
            min_maps_seen.append(int(min_maps))
    all_matches_bo3 = bool(min_maps_seen) and all(v >= 2 for v in min_maps_seen)

    print(f"\nSimulating Swiss x {n_sims:,}...")
    print(f"  format: {'all BO3' if all_matches_bo3 else 'mixed BO1/BO3'}")
    initial_seeds = simulator_initial_seeds(event_id, set(team_ids))
    if initial_seeds:
        print("  using exact initial seeds from the HLTV web simulator")
    records, sims = simulate_swiss_with_sims(
        strengths,
        n_sims=n_sims,
        h2h=h2h,
        seed=42,
        r1_pairings=r1,
        initial_seeds=initial_seeds or None,
        all_matches_bo3=all_matches_bo3,
    )

    print("\n=== Marginal probabilities ===")
    marg = marginal_probs(records)
    print(f"  {'team':<22}  {'3-0':>5}  {'3-1':>5}  {'3-2':>5}  {'adv':>5}  {'2-3':>5}  {'1-3':>5}  {'0-3':>5}")
    for tid in sorted(team_ids, key=lambda t: -marg[t]["advance"]):
        m = marg[tid]
        print(
            f"  {m['name']:<22}  {m['3-0']*100:>4.1f}%  {m['3-1']*100:>4.1f}%  "
            f"{m['3-2']*100:>4.1f}%  {m['advance']*100:>4.1f}%  "
            f"{m['2-3']*100:>4.1f}%  {m['1-3']*100:>4.1f}%  {m['0-3']*100:>4.1f}%"
        )

    print(f"\n=== Finding best ticket - Valve Major objective: max P(>=5 correct) ===")
    top = best_tickets(records, sims=sims, top_k=5, objective="p_at_least_5")
    for i, score in enumerate(top):
        print(f"\n#{i+1}  P(>=5 correct) = {score.p_at_least_5*100:.1f}%")
        print(explain_ticket(score.ticket, records, sims=sims, score=score))

    print(f"\n=== Also showing top by EV (HLTV-style 5/5/2 scoring) ===")
    top_ev = best_tickets(records, sims=sims, top_k=2, objective="expected_points")
    for i, score in enumerate(top_ev):
        print(f"\n#{i+1}  EV={score.expected_points:.2f}  (P(>=5)={score.p_at_least_5*100:.1f}%)")
        print(explain_ticket(score.ticket, records, sims=sims, score=score))

    # Backtest if the event is finished
    if ev.is_finished:
        truth = actual_records(client, event_id)
        print("\n=== Backtest against actual Stage 1 outcomes ===")
        print(f"  {'team':<22}  actual  predicted_marg")
        for tid in sorted(team_ids, key=lambda t: -marg[t]["advance"]):
            t = strengths[tid]
            w, l = truth.get(tid, (0, 0))
            print(f"  {t.name:<22}  {w}-{l}    advance_p={marg[tid]['advance']*100:.0f}%")
        best = top[0]
        best_ticket = best.ticket
        actual_score = score_against_truth(best_ticket, truth)
        # Count correct picks (Valve style)
        correct = 0
        for tid in best_ticket.three_oh:
            if truth.get(tid) == (3, 0): correct += 1
        for tid in best_ticket.zero_three:
            if truth.get(tid) == (0, 3): correct += 1
        for tid in best_ticket.advancers:
            w, l = truth.get(tid, (0, 0))
            if w >= 3 and (w, l) != (3, 0): correct += 1
        print(f"\nTop ticket actual results:")
        print(f"  Correct picks: {correct} / 10  (medal threshold: 5)")
        print(f"  HLTV-style points scored: {actual_score} / 32")
        print(f"  Model's P(>=5) was: {best.p_at_least_5*100:.1f}%")
        print(f"  Model's E[correct]: {best.expected_correct:.2f}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-id", type=int, required=True)
    parser.add_argument("--cutoff", required=True, help="ISO 8601 datetime, e.g. 2026-06-02T10:30:00Z")
    parser.add_argument("--n-sims", type=int, default=50_000)
    args = parser.parse_args()
    cutoff = datetime.fromisoformat(args.cutoff.replace("Z", "+00:00"))
    sys.exit(main(args.event_id, cutoff, args.n_sims))
