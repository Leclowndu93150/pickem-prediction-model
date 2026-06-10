"""Sweep blend weights for matchup_p_bo1 and report average correct."""

from __future__ import annotations

import argparse

from hltv_api import DiskCache, HLTVClient
from backtest import TARGETS, run_one


CONFIGS = [
    {"name": "current_default", "blends": None},
    {"name": "pure_elo", "blends": dict(form_blend=0, h2h_blend=0, vrs_blend=0, map_blend=0)},
    {"name": "vrs_only_heavy", "blends": dict(form_blend=0, h2h_blend=0, vrs_blend=0.40, map_blend=0)},
    {"name": "vrs_only_med", "blends": dict(form_blend=0, h2h_blend=0, vrs_blend=0.25, map_blend=0)},
    {"name": "vrs_plus_form", "blends": dict(form_blend=0.05, h2h_blend=0, vrs_blend=0.25, map_blend=0)},
    {"name": "small_blends", "blends": dict(form_blend=0.04, h2h_blend=0.04, vrs_blend=0.20, map_blend=0.05)},
    {"name": "tiny_blends", "blends": dict(form_blend=0.02, h2h_blend=0.02, vrs_blend=0.15, map_blend=0.03)},
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--n-sims", type=int, default=10_000)
    args = parser.parse_args()

    cache = DiskCache(base_dir=args.cache_dir)
    client = HLTVClient(cache=cache, proxy_pool=False)

    table: list[tuple[str, list[int]]] = []
    for cfg in CONFIGS:
        print(f"\n############### {cfg['name']} ###############")
        scores: list[int] = []
        p_at_least_5_pred: list[float] = []
        for target in TARGETS:
            r = run_one(
                client, target["id"], target["name"], args.n_sims,
                blends=cfg["blends"],
            )
            if r:
                scores.append(r["model_correct"])
                p_at_least_5_pred.append(r["model_p_at_least_5"])
        table.append((cfg["name"], scores, p_at_least_5_pred))

    print("\n=== Sweep Summary ===")
    print(f"  {'config':<22} {'avg':>5} {'>=5 hits':>10} {'predicted P>=5':>15}")
    for name, scores, preds in table:
        avg = sum(scores) / max(1, len(scores))
        hits = sum(1 for s in scores if s >= 5)
        pred = sum(preds) / max(1, len(preds))
        print(f"  {name:<22} {avg:>5.2f} {hits:>6}/{len(scores):<3} {pred*100:>13.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
