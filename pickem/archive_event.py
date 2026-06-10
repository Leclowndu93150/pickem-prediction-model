"""Archive a completed event's immutable results locally."""

from __future__ import annotations

import argparse

from hltv_api import HLTVClient
from pickem.history import archive_completed_event, archive_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("event_id", type=int)
    args = parser.parse_args()
    payload = archive_completed_event(HLTVClient(), args.event_id)
    print(
        f"archived {payload['event_name']}: "
        f"{payload['match_count']} matches, "
        f"{len(payload['qualifier_team_ids'])} qualifiers -> "
        f"{archive_path(args.event_id)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
