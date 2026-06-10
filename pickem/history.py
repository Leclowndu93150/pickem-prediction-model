"""Persistent archives for completed Swiss stages.

Finished event results are immutable for modeling purposes. This module
stores their ordered series/map results and final records under
``data/events`` so future predictions do not need to refetch them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hltv_api import HLTVClient


ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "events"


def archive_path(event_id: int) -> Path:
    return ARCHIVE_DIR / f"{event_id}.json"


def load_event_archive(event_id: int) -> dict[str, Any] | None:
    path = archive_path(event_id)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def find_event_archive_by_name(name: str) -> dict[str, Any] | None:
    if not ARCHIVE_DIR.exists():
        return None
    for path in ARCHIVE_DIR.glob("*.json"):
        try:
            with path.open(encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("event_name") == name:
            return payload
    return None


def _ordered_records(team_ids: list[int], matches: list[dict[str, Any]]) -> dict[int, tuple[int, int]]:
    records = {tid: [0, 0] for tid in team_ids}
    active = set(team_ids)
    for match in matches:
        t1 = match.get("team1_id")
        t2 = match.get("team2_id")
        winner = match.get("winner_team_id")
        if t1 not in active or t2 not in active or winner not in (t1, t2):
            continue
        loser = t2 if winner == t1 else t1
        records[winner][0] += 1
        records[loser][1] += 1
        for tid in (winner, loser):
            if records[tid][0] >= 3 or records[tid][1] >= 3:
                active.discard(tid)
    return {tid: (value[0], value[1]) for tid, value in records.items()}


def archive_completed_event(client: HLTVClient, event_id: int) -> dict[str, Any]:
    event = client.get_event(event_id)
    team_names: dict[int, str] = {}
    for entry in event.raw.get("teams") or []:
        try:
            tid = int(entry.get("teamId"))
        except (TypeError, ValueError):
            continue
        team_names[tid] = entry.get("teamName") or str(tid)

    ordered = sorted(
        event.results,
        key=lambda match: (
            match.start_time.timestamp() if match.start_time is not None else float("inf"),
            match.id or 0,
        ),
    )
    matches: list[dict[str, Any]] = []
    for index, match in enumerate(ordered, start=1):
        full = match.full
        if match.team1_id is not None and match.team1_name:
            team_names[match.team1_id] = match.team1_name
        if match.team2_id is not None and match.team2_name:
            team_names[match.team2_id] = match.team2_name
        matches.append(
            {
                "order": index,
                "match_id": match.id,
                "start_time": match.start_time.isoformat() if match.start_time else None,
                "team1_id": match.team1_id,
                "team1_name": match.team1_name,
                "team1_score": match.team1_score,
                "team2_id": match.team2_id,
                "team2_name": match.team2_name,
                "team2_score": match.team2_score,
                "winner_team_id": match.winner_team_id,
                "maps": [
                    {
                        "name": map_result.name,
                        "team1_score": map_result.team1_score,
                        "team2_score": map_result.team2_score,
                        "winner_team_id": map_result.winner_team_id,
                    }
                    for map_result in full.maps
                ],
            }
        )

    records = _ordered_records(event.team_ids, matches)
    teams = [
        {
            "team_id": tid,
            "name": team_names.get(tid, str(tid)),
            "record": list(records[tid]),
            "qualified": records[tid][0] >= 3,
        }
        for tid in event.team_ids
    ]
    payload = {
        "schema_version": 1,
        "event_id": event.id,
        "event_name": event.name,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "source": "HLTV mobile API",
        "team_count": len(event.team_ids),
        "match_count": len(matches),
        "teams_advancing": event.teams_advancing,
        "teams": teams,
        "qualifier_team_ids": [
            team["team_id"] for team in teams if team["qualified"]
        ],
        "matches": matches,
    }
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = archive_path(event_id)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)
    return payload


def archived_records(payload: dict[str, Any]) -> dict[int, tuple[int, int]]:
    out: dict[int, tuple[int, int]] = {}
    for team in payload.get("teams") or []:
        record = team.get("record") or []
        if len(record) != 2:
            continue
        out[int(team["team_id"])] = (int(record[0]), int(record[1]))
    return out
