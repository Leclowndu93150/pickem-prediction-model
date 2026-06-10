"""
Probe every public, non-auth-required endpoint of the HLTV mobile API.

Auto-discovers live match / event / player / team / article IDs from
no-arg endpoints, then exercises ID-required endpoints with those.

Run: python probe.py [report.json]
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from typing import Any

from hltv_api import HLTVClient, HLTVError


def shape(value: Any, depth: int = 0, max_items: int = 3) -> Any:
    """Return a compact shape descriptor instead of the full payload."""
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        keys = list(value.keys())
        out = {}
        for k in keys[:25]:
            out[k] = shape(value[k], depth + 1)
        if len(keys) > 25:
            out["__more_keys__"] = len(keys) - 25
        return out
    if isinstance(value, list):
        if not value:
            return []
        sample = [shape(v, depth + 1) for v in value[:max_items]]
        return {"__list__": len(value), "sample": sample}
    if isinstance(value, str):
        return f"str({len(value)})" if len(value) > 80 else value
    return value


def dig_first(obj: Any, *path_options: list[str]) -> Any:
    """Walk ``obj`` along the first matching key path that yields something
    truthy, return that value. Each option is a list of keys to descend."""
    for path in path_options:
        cur = obj
        ok = True
        for k in path:
            if isinstance(cur, list):
                cur = cur[0] if cur else None
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if ok and cur:
            return cur
    return None


def find_id(obj: Any, key_names: tuple[str, ...]) -> Any:
    """BFS through nested dicts/lists for the first matching key."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in key_names and isinstance(v, (int, str)) and v:
                    return v
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def call(label: str, fn, *args, **kwargs) -> dict[str, Any]:
    print(f"  -> {label}", flush=True)
    started = time.monotonic()
    try:
        result = fn(*args, **kwargs)
        elapsed = round(time.monotonic() - started, 2)
        return {
            "label": label,
            "ok": True,
            "elapsed_s": elapsed,
            "shape": shape(result),
            "sample": result if not isinstance(result, (dict, list)) else None,
        }
    except HLTVError as e:
        return {
            "label": label,
            "ok": False,
            "elapsed_s": round(time.monotonic() - started, 2),
            "status": e.status,
            "body_preview": e.body,
        }
    except Exception as e:
        return {
            "label": label,
            "ok": False,
            "elapsed_s": round(time.monotonic() - started, 2),
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc(limit=2),
        }


def main(out_path: str = "probe_report.json") -> int:
    c = HLTVClient()
    report: list[dict[str, Any]] = []

    print("== Core / discovery ==")
    for label, fn in [
        ("startup_check", c.startup_check),
        ("onboarding", c.onboarding),
        ("mobile_ads", c.mobile_ads),
        ("frontpage", c.frontpage),
        ("matches", c.matches),
        ("events", c.events),
        ("finished_events(offset=0)", lambda: c.finished_events(0)),
        ("ranking()", c.ranking),
        ("search_empty_state", c.search_empty_state),
        ("top_player_fans(0)", lambda: c.top_player_fans(0)),
        ("top_team_fans(0)", lambda: c.top_team_fans(0)),
        ("forum_create_topic_data", c.forum_create_topic_data),
        ("forum_threads('all', 0)", lambda: c.forum_threads("all", 0)),
        ("user_settings (expect 404 anon)", c.user_settings),
    ]:
        report.append(call(label, fn))

    # ----- discover IDs from the responses we just got -----
    by_label = {r["label"]: r for r in report if r["ok"]}

    # We saved shapes only; rerun the ones we need raw for ID extraction.
    print("\n== Discovering live IDs ==")
    raw_frontpage = c.frontpage()
    raw_matches = c.matches()
    raw_events = c.events()
    raw_ranking = c.ranking()
    raw_forum = c.forum_threads("all", 0)

    match_id = find_id(raw_matches, ("matchId",)) or find_id(
        raw_frontpage, ("matchId",)
    )
    event_id = find_id(raw_events, ("eventId",)) or find_id(
        raw_frontpage, ("eventId",)
    )
    article_id = find_id(raw_frontpage, ("articleId", "newsId"))
    player_id = find_id(raw_ranking, ("playerId",))
    team_id = find_id(raw_ranking, ("teamId",))
    thread_id = find_id(raw_forum, ("threadId",))

    discovered = {
        "match_id": match_id,
        "event_id": event_id,
        "article_id": article_id,
        "player_id": player_id,
        "team_id": team_id,
        "thread_id": thread_id,
    }
    print("  discovered:", discovered)
    report.append({"label": "__discovered_ids__", "ok": True, "shape": discovered})

    print("\n== ID-required endpoints ==")
    if match_id:
        report.append(call(f"match({match_id})", c.match, match_id))
    if event_id:
        report.append(call(f"event({event_id})", c.event, event_id))

    # event_stats needs a major/featured completed event
    raw_events = c.events()
    stats_event = None
    for src in ["ongoing", "completed"]:
        for ev in raw_events.get(src, {}).get("events", []):
            if ev.get("featured") or ev.get("isBig") or ev.get("isMajor"):
                stats_event = int(ev["eventId"])
                break
        if stats_event:
            break
    if stats_event:
        report.append(
            call(f"event_stats({stats_event})", c.event_stats, stats_event)
        )

    # eventhub: only available for an active EventHub
    sc = c.startup_check()
    eh_list = sc.get("eventHubs") or []
    if eh_list:
        eh_eid = eh_list[0].get("eventId") if isinstance(eh_list[0], dict) else None
        if eh_eid:
            report.append(call(f"eventhub({eh_eid})", c.eventhub, int(eh_eid)))
    else:
        report.append(
            {
                "label": "eventhub",
                "ok": True,
                "shape": "(skipped: no active EventHubs)",
            }
        )
    if article_id:
        report.append(call(f"article({article_id})", c.article, article_id))
    if player_id:
        report.append(call(f"player({player_id})", c.player, player_id))
    if team_id:
        report.append(call(f"team({team_id})", c.team, team_id))
    if thread_id:
        report.append(
            call(f"forum_thread({thread_id})", c.forum_thread, thread_id)
        )

    print("\n== Search ==")
    for term in ["faze", "s1mple", "iem"]:
        report.append(call(f"search('{term}')", c.search, term))

    print(f"\nWriting report to {out_path} ...")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    ok = sum(1 for r in report if r.get("ok"))
    fail = len(report) - ok
    print(f"\nDone. {ok} OK / {fail} failed across {len(report)} probes.")
    if fail:
        print("Failures:")
        for r in report:
            if not r.get("ok"):
                msg = r.get("error") or f"HTTP {r.get('status')}"
                print(f"  - {r['label']}: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(
        main(sys.argv[1] if len(sys.argv) > 1 else "probe_report.json")
    )
