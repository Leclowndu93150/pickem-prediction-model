"""Authoritative Swiss configuration extracted from HLTV's web simulator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONFIG_DIR = Path(__file__).resolve().parent.parent / "data" / "simulators"


def simulator_config_path(event_id: int) -> Path:
    return CONFIG_DIR / f"{event_id}.json"


def load_simulator_config(event_id: int) -> dict[str, Any] | None:
    path = simulator_config_path(event_id)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def simulator_initial_seeds(
    event_id: int,
    team_ids: set[int] | None = None,
) -> dict[int, int]:
    config = load_simulator_config(event_id)
    if not config:
        return {}
    seeds = {
        int(entry["team_id"]): int(entry["seed"])
        for entry in config.get("initial_seedings") or []
    }
    if len(seeds) != 16 or set(seeds.values()) != set(range(1, 17)):
        return {}
    if team_ids is not None and set(seeds) != team_ids:
        return {}
    return seeds


def simulator_round_one_pairings(
    event_id: int,
    team_ids: set[int] | None = None,
) -> list[tuple[int, int]]:
    config = load_simulator_config(event_id)
    if not config:
        return []
    try:
        pairings = [
            (int(pair[0]), int(pair[1]))
            for pair in config.get("round_one_pairings") or []
        ]
    except (IndexError, TypeError, ValueError):
        return []
    flattened = [team_id for pair in pairings for team_id in pair]
    if len(pairings) != 8 or len(set(flattened)) != 16:
        return []
    if team_ids is not None and set(flattened) != team_ids:
        return []
    return pairings
