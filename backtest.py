"""Backtest the pickem model against finished 16-team Swiss events.

For each target event:
  1. Build TeamStrengths at the cutoff (first match start).
  2. Run the Swiss simulator.
  3. Optimize the top ticket by P(>=5).
  4. Score the ticket against the actual Swiss final records.
  5. Also report what a pure-VRS baseline would have scored.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from hltv_api import DiskCache, HLTVClient
from pickem.model import TeamStrength, build_team_strengths, matchup_p_bo1
from pickem.optimize import (
    PTS_3_0,
    PTS_0_3,
    PTS_ADV,
    Ticket,
    best_tickets,
    marginal_probs,
)
from pickem.run import (
    actual_records,
    collect_h2h,
    extract_r1_pairings,
    previous_stage_records,
    score_against_truth,
)
from pickem.swiss import simulate_swiss_with_sims


TARGETS = [
    {"id": 8049, "name": "PGL Astana 2026"},
    {"id": 9028, "name": "IEM Cologne Major 2026 Stage 1"},
    {"id": 9029, "name": "IEM Cologne Major 2026 Stage 2"},
]


def first_match_cutoff(event) -> datetime:
    starts = [
        m.start_time
        for m in event.results + event.matches
        if m.start_time is not None
    ]
    if not starts:
        return datetime.now(timezone.utc)
    return min(starts)


def detect_all_bo3(event) -> bool:
    seen: list[int] = []
    for raw_m in event.raw.get("results", []) + event.raw.get("matches", []):
        t1 = (raw_m.get("team1") or {}).get("teamId")
        t2 = (raw_m.get("team2") or {}).get("teamId")
        mm = raw_m.get("minMaps")
        if t1 and t2 and mm is not None:
            seen.append(int(mm))
    return bool(seen) and all(v >= 2 for v in seen)


def swiss_truth(event) -> dict[int, tuple[int, int]]:
    """Reconstruct Swiss-only records (stop at 3-x), not playoff records."""
    record: dict[int, list[int]] = {tid: [0, 0] for tid in event.team_ids}
    active = set(event.team_ids)
    matches = sorted(
        event.results,
        key=lambda m: (
            m.start_time.timestamp() if m.start_time is not None else float("inf"),
            m.id or 0,
        ),
    )
    for m in matches:
        t1, t2, w = m.team1_id, m.team2_id, m.winner_team_id
        if t1 not in active or t2 not in active or w not in (t1, t2):
            continue
        loser = t2 if w == t1 else t1
        record[w][0] += 1
        record[loser][1] += 1
        for tid in (w, loser):
            if record[tid][0] >= 3 or record[tid][1] >= 3:
                active.discard(tid)
    return {tid: (record[tid][0], record[tid][1]) for tid in event.team_ids}


def score_ticket_correct(ticket: Ticket, truth: dict[int, tuple[int, int]]) -> int:
    """Count correct picks under Valve scoring (advance must be 3-1 or 3-2)."""
    correct = 0
    for tid in ticket.three_oh:
        if truth.get(tid) == (3, 0):
            correct += 1
    for tid in ticket.zero_three:
        if truth.get(tid) == (0, 3):
            correct += 1
    for tid in ticket.advancers:
        w, l = truth.get(tid, (0, 0))
        if w >= 3 and (w, l) != (3, 0):
            correct += 1
    return correct


def baseline_vrs_ticket(event, strengths: dict[int, TeamStrength]) -> Ticket:
    """Top-2 VRS to 3-0, bottom-2 to 0-3, next-6 to advance."""
    by_vrs = sorted(
        strengths.values(),
        key=lambda s: -(s.vrs_points or s.elo),
    )
    three_oh = tuple(s.team_id for s in by_vrs[:2])
    zero_three = tuple(s.team_id for s in by_vrs[-2:])
    advancers = tuple(s.team_id for s in by_vrs[2:8])
    return Ticket(three_oh=three_oh, advancers=advancers, zero_three=zero_three)


def baseline_rank_ticket(strengths: dict[int, TeamStrength]) -> Ticket:
    """Top-2 by world rank to 3-0, bottom-2 to 0-3, next-6 to advance."""
    by_rank = sorted(
        strengths.values(),
        key=lambda s: (s.world_rank or 999),
    )
    three_oh = tuple(s.team_id for s in by_rank[:2])
    zero_three = tuple(s.team_id for s in by_rank[-2:])
    advancers = tuple(s.team_id for s in by_rank[2:8])
    return Ticket(three_oh=three_oh, advancers=advancers, zero_three=zero_three)


def run_one(
    client: HLTVClient,
    event_id: int,
    name: str,
    n_sims: int,
    blends: dict | None = None,
) -> dict:
    print(f"\n=== {name} ({event_id}) ===")
    event = client.get_event(event_id)
    if len(event.team_ids) != 16:
        print(f"  skip: expected 16 teams, got {len(event.team_ids)}")
        return {}

    cutoff = first_match_cutoff(event)
    all_bo3 = detect_all_bo3(event)
    print(f"  cutoff: {cutoff.isoformat()}  format: {'all BO3' if all_bo3 else 'mixed BO1/BO3'}")

    stage_records = previous_stage_records(client, event.name, event.team_ids)
    if stage_records:
        print(f"  carryover from previous stage: {len(stage_records)} teams")
    strengths = build_team_strengths(
        client, event.team_ids, cutoff,
        stage_entry_records_by_team=stage_records,
    )
    h2h = collect_h2h(client, event.team_ids, cutoff)
    r1 = extract_r1_pairings(client, event_id)

    p_bo1 = None
    if blends is not None:
        def p_bo1(a, b, h):
            return matchup_p_bo1(a, b, h2h=h, **blends)

    records, sims = simulate_swiss_with_sims(
        strengths,
        n_sims=n_sims,
        h2h=h2h,
        seed=42,
        r1_pairings=r1 if len(r1) == 8 else None,
        all_matches_bo3=all_bo3,
        p_bo1=p_bo1,
    )

    truth = swiss_truth(event)

    tops = best_tickets(records, sims=sims, top_k=1, objective="p_at_least_5")
    model_score = score_ticket_correct(tops[0].ticket, truth)
    model_pts = score_against_truth(tops[0].ticket, truth)

    vrs_ticket = baseline_vrs_ticket(event, strengths)
    rank_ticket = baseline_rank_ticket(strengths)
    vrs_score = score_ticket_correct(vrs_ticket, truth)
    rank_score = score_ticket_correct(rank_ticket, truth)

    marg = marginal_probs(records)

    print(f"  Model:    {model_score}/10 correct  {model_pts}/32 pts  P(>=5)={tops[0].p_at_least_5*100:.1f}%")
    print(f"  VRS:      {vrs_score}/10 correct")
    print(f"  Rank:     {rank_score}/10 correct")
    print(f"  Model top ticket:")
    for slot, ids in [("3-0", tops[0].ticket.three_oh), ("adv", tops[0].ticket.advancers), ("0-3", tops[0].ticket.zero_three)]:
        for tid in ids:
            t = truth.get(tid, (0, 0))
            ok = "OK" if (
                (slot == "3-0" and t == (3, 0))
                or (slot == "0-3" and t == (0, 3))
                or (slot == "adv" and t[0] >= 3 and t != (3, 0))
            ) else "  "
            print(f"    {slot:>3} {ok}  {marg[tid]['name']:<22} actual={t[0]}-{t[1]}")

    return {
        "event_id": event_id,
        "name": name,
        "model_correct": model_score,
        "model_p_at_least_5": tops[0].p_at_least_5,
        "vrs_correct": vrs_score,
        "rank_correct": rank_score,
        "truth": truth,
        "ticket": tops[0].ticket,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--n-sims", type=int, default=20_000)
    parser.add_argument("--no-proxy", action="store_true")
    args = parser.parse_args()

    cache = DiskCache(base_dir=args.cache_dir)
    client = HLTVClient(
        cache=cache,
        proxy_pool=False if args.no_proxy else None,
    )

    results = []
    for target in TARGETS:
        r = run_one(client, target["id"], target["name"], args.n_sims)
        if r:
            results.append(r)

    print("\n=== Summary ===")
    print(f"  {'event':<22} {'model':>6} {'vrs':>6} {'rank':>6} {'P>=5':>6}")
    for r in results:
        print(
            f"  {r['name']:<22} {r['model_correct']:>4}/10 {r['vrs_correct']:>4}/10 {r['rank_correct']:>4}/10 {r['model_p_at_least_5']*100:>5.1f}%"
        )
    avg_model = sum(r["model_correct"] for r in results) / max(1, len(results))
    avg_vrs = sum(r["vrs_correct"] for r in results) / max(1, len(results))
    avg_rank = sum(r["rank_correct"] for r in results) / max(1, len(results))
    print(f"  {'AVERAGE':<22} {avg_model:>4.1f}/10 {avg_vrs:>4.1f}/10 {avg_rank:>4.1f}/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
