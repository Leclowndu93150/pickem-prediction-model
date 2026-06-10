"""Predict IEM Cologne Major 2026 Stage 3 ticket.

Stage 3 has no dedicated event id yet; the 16 participants and R1
pairings are exposed in the parent Major event (8301) as upcoming
matches.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from hltv_api import DiskCache, HLTVClient
from pickem.model import build_team_strengths
from pickem.optimize import best_tickets, explain_ticket, marginal_probs
from pickem.run import collect_h2h, previous_stage_records
from pickem.swiss import simulate_swiss_with_sims


MAJOR_EVENT_ID = 8301
STAGE_2_EVENT_ID = 9029


def stage3_team_ids_and_pairings(event) -> tuple[list[int], list[tuple[int, int]]]:
    pairings: list[tuple[int, int]] = []
    team_ids: list[int] = []
    seen: set[int] = set()
    for m in event.matches:
        if m.team1_id is None or m.team2_id is None:
            continue
        a, b = m.team1_id, m.team2_id
        if len(pairings) >= 8:
            break
        pairings.append((a, b))
        for tid in (a, b):
            if tid not in seen:
                seen.add(tid)
                team_ids.append(tid)
    return team_ids, pairings


def _previous_stage_name_simple(name: str) -> str:
    return name.replace("Major", "Major Stage 2") if "Stage" not in name else name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--n-sims", type=int, default=20_000)
    args = parser.parse_args()

    cache = DiskCache(base_dir=args.cache_dir)
    client = HLTVClient(cache=cache, proxy_pool=False)

    major = client.get_event(MAJOR_EVENT_ID)
    team_ids, r1 = stage3_team_ids_and_pairings(major)
    if len(team_ids) != 16:
        print(f"could not read 16 Stage 3 teams (got {len(team_ids)})")
        return 1

    # cutoff = earliest upcoming match in Stage 3
    starts = sorted(m.start_time for m in major.matches if m.start_time is not None)
    cutoff = starts[0] if starts else datetime.now(timezone.utc)

    print(f"IEM Cologne Major 2026 Stage 3")
    print(f"  cutoff: {cutoff.isoformat()}")
    print(f"  teams:  {len(team_ids)}")

    # carryover: 8 teams that advanced from Stage 2 keep their qualifying record
    stage2 = client.get_event(STAGE_2_EVENT_ID)
    stage2_records: dict[int, list[int]] = {tid: [0, 0] for tid in stage2.team_ids}
    active = set(stage2.team_ids)
    matches = sorted(
        stage2.results,
        key=lambda m: (m.start_time.timestamp() if m.start_time else 1e12, m.id or 0),
    )
    for m in matches:
        t1, t2, w = m.team1_id, m.team2_id, m.winner_team_id
        if t1 not in active or t2 not in active or w not in (t1, t2):
            continue
        loser = t2 if w == t1 else t1
        stage2_records[w][0] += 1
        stage2_records[loser][1] += 1
        for tid in (w, loser):
            if stage2_records[tid][0] >= 3 or stage2_records[tid][1] >= 3:
                active.discard(tid)
    stage2_finals = {tid: (rec[0], rec[1]) for tid, rec in stage2_records.items()}

    stage_carryover = {
        tid: stage2_finals[tid]
        for tid in team_ids
        if tid in stage2_finals and stage2_finals[tid][0] >= 3
    }
    print(f"  carryover from Stage 2: {len(stage_carryover)} teams")
    for tid, rec in sorted(stage_carryover.items(), key=lambda kv: (-kv[1][0], kv[1][1])):
        print(f"    {client.get_team(tid).name:<22} {rec[0]}-{rec[1]}")

    strengths = build_team_strengths(
        client, team_ids, cutoff,
        stage_entry_records_by_team=stage_carryover,
    )
    h2h = collect_h2h(client, team_ids, cutoff)

    print(f"\n  Round 1 pairings (from HLTV):")
    names = {tid: strengths[tid].name for tid in team_ids}
    for a, b in r1:
        print(f"    {names.get(a, a):<22} vs  {names.get(b, b)}")

    print(f"\n  Team strengths (sorted by Elo):")
    print(f"    {'team':<22} #rank  base   elo  vrs_pts  carry")
    for s in sorted(strengths.values(), key=lambda x: -x.elo):
        rank = str(s.world_rank or "?")
        carry = f"{s.stage_entry_record[0]}-{s.stage_entry_record[1]}" if s.stage_entry_record else "-"
        vrs = f"{s.vrs_points:.0f}" if s.vrs_points else "-"
        print(
            f"    {s.name:<22} #{rank:<3}   {s.base_elo:>4.0f}  {s.elo:>4.0f}  {vrs:>6}    {carry}"
        )

    print(f"\nSimulating Swiss x {args.n_sims:,} (all matches BO3)...")
    records, sims = simulate_swiss_with_sims(
        strengths,
        n_sims=args.n_sims,
        h2h=h2h,
        seed=42,
        r1_pairings=r1,
        all_matches_bo3=True,
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

    print("\n=== Top tickets by P(>=5 correct) ===")
    tops = best_tickets(records, sims=sims, top_k=3, objective="p_at_least_5")
    for i, score in enumerate(tops):
        print(f"\n#{i+1}  P(>=5 correct) = {score.p_at_least_5*100:.1f}%")
        print(explain_ticket(score.ticket, records, sims=sims, score=score))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
