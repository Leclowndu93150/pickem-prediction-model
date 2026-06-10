"""Web-facing pickem prediction service.

Wraps the simulator with stable JSON-ish dicts and explicit
readiness/warning states. Synchronous; the FastAPI layer runs it in
a background executor for web requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from hltv_api import HLTVClient
from hltv_api.models import Match, Player
from pickem.model import build_team_strengths
from pickem.optimize import TicketScore, best_tickets, marginal_probs
from pickem.run import collect_h2h, extract_r1_pairings, previous_stage_records
from pickem.simulator_config import (
    simulator_initial_seeds,
    simulator_round_one_pairings,
)
from pickem.swiss import FinalRecord, simulate_swiss_with_sims


class PredictionError(Exception):
    """User-facing prediction failure."""

    def __init__(self, message: str, *, status: str = "error", details: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class PredictionOptions:
    n_sims: int = 30_000
    seed: int = 42
    top_k: int = 5
    cutoff: datetime | None = None
    force_team_ids: tuple[int, ...] = ()
    force_stage_records: dict[int, tuple[int, int]] | None = None
    allow_random_r1: bool = True


def parse_cutoff(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _match_sort_key(match) -> float:
    return match.start_time.timestamp() if match.start_time is not None else float("inf")


def _parse_percent(value) -> float:
    if value is None:
        return 0.5
    if isinstance(value, str) and value.endswith("%"):
        try:
            return float(value.rstrip("%")) / 100.0
        except ValueError:
            return 0.5
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.5
    return f / 100.0 if f > 1.0 else f


def _map_pool_from_match(match: Match, team_id: int) -> dict[str, dict[str, float]]:
    stats = match.map_team_stats or {}
    side = "team1MapStats" if match.team1_id == team_id else "team2MapStats"
    infos = stats.get("mapInfos") or {}
    out: dict[str, dict[str, float]] = {}
    for map_id, entry in (stats.get(side) or {}).items():
        info = infos.get(str(map_id)) or infos.get(int(map_id)) or {}
        name = info.get("mapName")
        if not name:
            continue
        out[name] = {
            "win_rate": _parse_percent(entry.get("winRate")),
            "ct_win_rate": _parse_percent(entry.get("ctWinRate")),
            "t_win_rate": _parse_percent(entry.get("tWinRate")),
            "played": float(entry.get("played") or 0),
            "pick_pct": _parse_percent(entry.get("pickPercentage")),
            "ban_pct": _parse_percent(entry.get("banPercentage")),
        }
    return out


def _event_world_ranks(event) -> dict[int, int]:
    out: dict[int, int] = {}
    for entry in event.raw.get("teams") or []:
        try:
            tid = int(entry.get("teamId"))
        except (TypeError, ValueError):
            continue
        rank = entry.get("hltvRank")
        if rank is None:
            continue
        try:
            out[tid] = int(rank)
        except (TypeError, ValueError):
            continue
    return out


def _opening_snapshot_matches(event, r1_pairings: list[tuple[int, int]]) -> list[Match]:
    matches = sorted(event.results + event.matches, key=_match_sort_key)
    out: list[Match] = []
    if r1_pairings:
        r1_ids = {tuple(sorted(pair)) for pair in r1_pairings}
        for m in matches:
            if m.team1_id is None or m.team2_id is None:
                continue
            if tuple(sorted((m.team1_id, m.team2_id))) not in r1_ids:
                continue
            out.append(m.full)
            if len(out) >= 8:
                break
        return out

    # Fallback for historical/event pages where extract_r1_pairings cannot
    # identify seeds but the earliest BO1 rows are already available.
    seen: set[tuple[int, int]] = set()
    for m in matches:
        if m.team1_id is None or m.team2_id is None:
            continue
        block = m.raw.get("match", m.raw) if isinstance(m.raw, dict) else {}
        if block.get("minMaps") != 1:
            continue
        key = tuple(sorted((m.team1_id, m.team2_id)))
        if key in seen:
            continue
        seen.add(key)
        out.append(m.full)
        if len(out) >= 8:
            break
    return out


def _collect_event_snapshots(
    event,
    r1_pairings: list[tuple[int, int]],
) -> tuple[
    dict[int, list[Player]],
    dict[int, list[Match]],
    dict[int, dict[str, dict[str, float]]],
    dict[int, int],
    dict[int, float],
    list[str],
]:
    lineups: dict[int, list[Player]] = {}
    form: dict[int, list[Match]] = {}
    maps: dict[int, dict[str, dict[str, float]]] = {}
    vrs_points: dict[int, float] = {}
    warnings: list[str] = []
    world_ranks = _event_world_ranks(event)

    opening = _opening_snapshot_matches(event, r1_pairings)
    if not opening:
        warnings.append("opening MatchScreen snapshots unavailable; using current team endpoints for lineup/form/map data")
        return lineups, form, maps, world_ranks, vrs_points, warnings

    for m in opening:
        forecast = m.vrs_forecast or {}
        if m.team1_id is not None:
            lineups[m.team1_id] = m.expected_lineup_players_team1 or lineups.get(m.team1_id, [])
            form[m.team1_id] = m.team1_form_matches
            maps[m.team1_id] = _map_pool_from_match(m, m.team1_id)
            if forecast.get("team1CurrentPoints") is not None:
                vrs_points[m.team1_id] = float(forecast["team1CurrentPoints"])
        if m.team2_id is not None:
            lineups[m.team2_id] = m.expected_lineup_players_team2 or lineups.get(m.team2_id, [])
            form[m.team2_id] = m.team2_form_matches
            maps[m.team2_id] = _map_pool_from_match(m, m.team2_id)
            if forecast.get("team2CurrentPoints") is not None:
                vrs_points[m.team2_id] = float(forecast["team2CurrentPoints"])

    if len(opening) < 8:
        warnings.append(f"only {len(opening)} opening match snapshots found; missing teams use current team endpoints")
    return lineups, form, maps, world_ranks, vrs_points, warnings


def _team_name_map(client: HLTVClient, event, team_ids: list[int]) -> dict[int, str]:
    names: dict[int, str] = {}
    for m in event.results + event.matches:
        if m.team1_id and m.team1_name:
            names[m.team1_id] = m.team1_name
        if m.team2_id and m.team2_name:
            names[m.team2_id] = m.team2_name
    for tid in team_ids:
        if tid not in names:
            try:
                t = client.get_team(tid)
                if t.name:
                    names[tid] = t.name
            except Exception:
                names[tid] = str(tid)
    return names


def _event_cutoff(event, explicit: datetime | None) -> datetime:
    if explicit is not None:
        return explicit
    pickems = event.pickem_info
    for p in pickems:
        raw = p.get("deadlineTime")
        if raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    starts = [m.start_time for m in event.results + event.matches if m.start_time is not None]
    if starts:
        return min(starts)
    return datetime.now(timezone.utc)


def _team_ids_with_forces(event, force_team_ids: tuple[int, ...]) -> list[int]:
    out: list[int] = []
    for tid in list(event.team_ids) + list(force_team_ids):
        if tid and tid not in out:
            out.append(tid)
    return out


def _readiness(event, team_ids: list[int], r1_pairings: list[tuple[int, int]], allow_random_r1: bool) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if len(team_ids) < 16:
        warnings.append(f"event has only {len(team_ids)} team ids; add force_team_ids or wait for HLTV")
        return "not_ready", warnings
    if len(team_ids) > 16:
        warnings.append(f"event has {len(team_ids)} team ids; expected 16")
        return "not_ready", warnings
    if len(r1_pairings) < 8:
        if allow_random_r1:
            warnings.append("round 1 pairings are incomplete; using simulated/random R1 pairings")
            return "provisional", warnings
        warnings.append("round 1 pairings are incomplete")
        return "not_ready", warnings
    return "ready", warnings


def _all_matches_bo3(event) -> bool:
    known: list[int] = []
    for match in event.raw.get("results", []) + event.raw.get("matches", []):
        if (match.get("team1") or {}).get("teamId") and (match.get("team2") or {}).get("teamId"):
            min_maps = match.get("minMaps")
            if min_maps is not None:
                known.append(int(min_maps))
    return bool(known) and all(value >= 2 for value in known)


def _initial_seeds(event) -> dict[int, int]:
    """Return only verified simulator seeds.

    ``event.raw[groupings].swissGroup.teams`` is roster ordering, not
    seeding. Treating it as seeds silently changes Buchholz pairings.
    """
    return simulator_initial_seeds(event.id, set(event.team_ids))


def _ticket_to_dict(score: TicketScore, marg: dict[int, dict[str, float]]) -> dict[str, Any]:
    def team_slot(tid: int, slot: str) -> dict[str, Any]:
        m = marg[tid]
        return {
            "team_id": tid,
            "name": m["name"],
            "slot": slot,
            "p_3_0": m["3-0"],
            "p_advance": m["advance"],
            "p_advance_slot": m["3-1"] + m["3-2"],
            "p_0_3": m["0-3"],
        }

    return {
        "three_oh": [team_slot(tid, "3-0") for tid in score.ticket.three_oh],
        "advance": [team_slot(tid, "advance") for tid in score.ticket.advancers],
        "zero_three": [team_slot(tid, "0-3") for tid in score.ticket.zero_three],
        "p_at_least_5": score.p_at_least_5,
        "expected_correct": score.expected_correct,
        "expected_points": score.expected_points,
        "p_perfect_10": score.p_perfect_10,
        "correct_distribution": list(score.correct_dist),
    }


def _teams_to_dict(records: dict[int, FinalRecord], strengths, stage_records: dict[int, tuple[int, int]]) -> list[dict[str, Any]]:
    marg = marginal_probs(records)
    out: list[dict[str, Any]] = []
    for tid in sorted(marg, key=lambda t: -marg[t]["advance"]):
        s = strengths[tid]
        m = marg[tid]
        out.append({
            "team_id": tid,
            "name": m["name"],
            "world_rank": s.world_rank,
            "vrs_points": s.vrs_points,
            "elo": s.elo,
            "base_elo": s.base_elo,
            "elo_delta": s.elo_delta,
            "recent_form": s.recent_form,
            "sample_n": s.sample_n,
            "map_sample_n": s.map_sample_n,
            "stage_entry_record": list(stage_records[tid]) if tid in stage_records else None,
            "stage_entry_boost": s.stage_entry_boost,
            "big_event_score": s.big_event_score,
            "big_event_maps": s.big_event_maps,
            "p_3_0": m["3-0"],
            "p_3_1": m["3-1"],
            "p_3_2": m["3-2"],
            "p_advance": m["advance"],
            "p_advance_slot": m["3-1"] + m["3-2"],
            "p_2_3": m["2-3"],
            "p_1_3": m["1-3"],
            "p_0_3": m["0-3"],
        })
    return out


def event_status(event_id: int, *, client: HLTVClient | None = None, force_team_ids: tuple[int, ...] = (), allow_random_r1: bool = True) -> dict[str, Any]:
    client = client or HLTVClient()
    event = client.get_event(event_id)
    team_ids = _team_ids_with_forces(event, force_team_ids)
    r1 = extract_r1_pairings(client, event_id)
    simulator_r1 = simulator_round_one_pairings(event_id, set(team_ids))
    if simulator_r1:
        r1 = simulator_r1
    status, warnings = _readiness(event, team_ids, r1, allow_random_r1)
    names = _team_name_map(client, event, team_ids)
    stage_records = previous_stage_records(client, event.name, team_ids)
    return {
        "event_id": event_id,
        "event_name": event.name,
        "status": status,
        "warnings": warnings,
        "team_count": len(team_ids),
        "teams": [{"team_id": tid, "name": names.get(tid, str(tid)), "stage_entry_record": list(stage_records[tid]) if tid in stage_records else None} for tid in team_ids],
        "r1_pairings": [{"team1_id": a, "team1_name": names.get(a, str(a)), "team2_id": b, "team2_name": names.get(b, str(b))} for a, b in r1],
        "pickem_info": event.pickem_info,
    }


def predict_event(event_id: int, options: PredictionOptions | None = None, *, client: HLTVClient | None = None) -> dict[str, Any]:
    options = options or PredictionOptions()
    if options.n_sims < 1 or options.n_sims > 500_000:
        raise PredictionError("n_sims must be between 1 and 500000", status="invalid_request")
    if options.top_k < 1 or options.top_k > 50:
        raise PredictionError("top_k must be between 1 and 50", status="invalid_request")
    client = client or HLTVClient()
    event = client.get_event(event_id)
    team_ids = _team_ids_with_forces(event, options.force_team_ids)
    r1 = extract_r1_pairings(client, event_id)
    simulator_r1 = simulator_round_one_pairings(event_id, set(team_ids))
    if simulator_r1:
        r1 = simulator_r1
    status, warnings = _readiness(event, team_ids, r1, options.allow_random_r1)
    if status == "not_ready":
        raise PredictionError("event is not ready for prediction", status=status, details={"warnings": warnings, "team_count": len(team_ids)})

    cutoff = _event_cutoff(event, options.cutoff)
    stage_records = previous_stage_records(client, event.name, team_ids)
    if options.force_stage_records:
        stage_records.update(options.force_stage_records)
    (
        lineup_players_by_team,
        form_matches_by_team,
        map_pool_by_team,
        world_rank_by_team,
        vrs_points_by_team,
        snapshot_warnings,
    ) = _collect_event_snapshots(event, r1)
    warnings.extend(snapshot_warnings)

    strengths = build_team_strengths(
        client,
        team_ids,
        cutoff,
        lineup_players_by_team=lineup_players_by_team,
        form_matches_by_team=form_matches_by_team,
        map_pool_by_team=map_pool_by_team,
        world_rank_by_team=world_rank_by_team,
        vrs_points_by_team=vrs_points_by_team,
        stage_entry_records_by_team=stage_records,
    )
    h2h = collect_h2h(client, team_ids, cutoff)
    pairings = r1 if len(r1) >= 8 else None
    all_matches_bo3 = _all_matches_bo3(event)
    initial_seeds = _initial_seeds(event)
    if len(initial_seeds) != 16:
        warnings.append("exact initial stage seeds unavailable; pairing ties fall back to model strength")
    elif simulator_initial_seeds(event.id, set(team_ids)):
        warnings.append("initial stage seeds loaded from the HLTV web simulator")
    records, sims = simulate_swiss_with_sims(
        strengths,
        n_sims=options.n_sims,
        h2h=h2h,
        seed=options.seed,
        r1_pairings=pairings,
        all_matches_bo3=all_matches_bo3,
        initial_seeds=initial_seeds or None,
    )
    scores = best_tickets(records, sims=sims, top_k=options.top_k, objective="p_at_least_5")
    marg = marginal_probs(records)
    names = _team_name_map(client, event, team_ids)

    return {
        "event_id": event_id,
        "event_name": event.name,
        "status": status,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": cutoff.isoformat(),
        "n_sims": options.n_sims,
        "seed": options.seed,
        "team_count": len(team_ids),
        "match_format": "all_bo3" if all_matches_bo3 else "mixed_bo1_bo3",
        "initial_seeds": {
            str(tid): seed
            for tid, seed in sorted(initial_seeds.items(), key=lambda item: item[1])
        },
        "snapshot_coverage": {
            "lineups": len(lineup_players_by_team),
            "form_blocks": len(form_matches_by_team),
            "map_pools": len(map_pool_by_team),
            "world_ranks": len(world_rank_by_team),
            "vrs_points": len(vrs_points_by_team),
        },
        "teams": _teams_to_dict(records, strengths, stage_records),
        "r1_pairings": [{"team1_id": a, "team1_name": names.get(a, str(a)), "team2_id": b, "team2_name": names.get(b, str(b))} for a, b in r1],
        "tickets": [_ticket_to_dict(score, marg) for score in scores],
        "best_ticket": _ticket_to_dict(scores[0], marg) if scores else None,
        "cache_stats": client.cache.stats,
    }
