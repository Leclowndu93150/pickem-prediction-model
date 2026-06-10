"""JSON CLI for the pickem prediction service."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from pickem.service import PredictionError, PredictionOptions, parse_cutoff, predict_event


def _record_arg(value: str) -> tuple[int, tuple[int, int]]:
    try:
        team_raw, record_raw = value.split(":", 1)
        wins_raw, losses_raw = record_raw.replace("/", "-").split("-", 1)
        return int(team_raw), (int(wins_raw), int(losses_raw))
    except Exception as exc:
        raise argparse.ArgumentTypeError(
            "stage records must look like TEAM_ID:W-L, e.g. 12774:3-2"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a pickem prediction and emit JSON.")
    parser.add_argument("--event-id", type=int, required=True)
    parser.add_argument("--n-sims", type=int, default=30_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--cutoff", default=None, help="ISO datetime, defaults to event deadline/first match.")
    parser.add_argument("--force-team-id", type=int, action="append", default=[])
    parser.add_argument(
        "--force-stage-record",
        type=_record_arg,
        action="append",
        default=[],
        help="Override previous-stage record as TEAM_ID:W-L.",
    )
    parser.add_argument("--allow-random-r1", action=argparse.BooleanOptionalAction, default=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    options = PredictionOptions(
        n_sims=args.n_sims,
        seed=args.seed,
        top_k=args.top_k,
        cutoff=parse_cutoff(args.cutoff),
        force_team_ids=tuple(dict.fromkeys(args.force_team_id)),
        force_stage_records=dict(args.force_stage_record),
        allow_random_r1=args.allow_random_r1,
    )
    try:
        result = predict_event(args.event_id, options)
    except PredictionError as exc:
        payload: dict[str, Any] = {
            "status": exc.status,
            "error": {"message": str(exc), "details": exc.details},
        }
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 2
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
