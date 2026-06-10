"""Prefetch event data into ./cache so it can ship with the repo."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from hltv_api import DiskCache, HLTVClient


CURRENT_MAJOR_STAGES = [9028, 9029]
CURRENT_MAJOR_EVENT = 8301
# Confirmed Swiss(16) backtest targets: PGL Astana 2026, PGL Bucharest 2025,
# PGL Cluj-Napoca 2025, Austin Major 2025 Stages 1+2, PGL Astana 2025.
BACKTEST_EVENTS = [8049, 8044, 8043, 8436, 8437, 8045]


def warm(client: HLTVClient, event_id: int, workers: int) -> None:
    started = time.monotonic()
    ev = client.get_event(event_id)
    print(f"  {ev.name}: {len(ev.team_ids)} teams, {len(ev.matches)} upcoming, {len(ev.results)} results")
    teams = client.bulk_teams(ev.team_ids, max_workers=workers)
    player_ids: list[int] = []
    for t in teams:
        for p in t.roster:
            if p.id is not None and p.id not in player_ids:
                player_ids.append(p.id)
    if player_ids:
        client.bulk_players(player_ids, max_workers=workers)
    # event matches
    match_ids = [m.id for m in ev.results + ev.matches if m.id is not None]
    # per-team recent matches and form-via-match deep block
    for t in teams:
        for series in t.recent_matches:
            if series.id is not None:
                match_ids.append(series.id)
    match_ids = list(dict.fromkeys(match_ids))
    if match_ids:
        client.bulk_matches(match_ids, max_workers=workers)
    elapsed = time.monotonic() - started
    print(f"  done in {elapsed:.1f}s ({len(match_ids)} matches). cache stats: {client.cache.stats}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--workers", type=int, default=128)
    args = parser.parse_args()

    cache = DiskCache(base_dir=args.cache_dir)
    client = HLTVClient(cache=cache)
    print(f"cache dir: {args.cache_dir}")
    print(f"workers:   {args.workers}")
    print(f"proxies:   {client.proxy_pool.size if client.proxy_pool else 'none'}")

    targets = list(set([CURRENT_MAJOR_EVENT] + CURRENT_MAJOR_STAGES + BACKTEST_EVENTS))
    for eid in targets:
        print(f"\n=== event {eid} ===")
        try:
            warm(client, eid, args.workers)
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")

    print("\nfinal cache stats:", client.cache.stats)
    cache_files = list(Path(args.cache_dir).glob("*.json"))
    total_mb = sum(f.stat().st_size for f in cache_files) / 1e6
    print(f"cache files: {len(cache_files)}, total {total_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
