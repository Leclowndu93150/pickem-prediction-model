"""
End-to-end probe of every typed accessor on HLTVClient.

Skips auth-only / POST endpoints. Reports OK/FAIL per call.
"""

from __future__ import annotations

import sys
import time
import traceback

from hltv_api import HLTVClient, HLTVError


def call(label, fn, *args, **kwargs):
    started = time.monotonic()
    try:
        out = fn(*args, **kwargs)
        elapsed = round(time.monotonic() - started, 2)
        desc = ""
        if hasattr(out, "__len__"):
            desc = f"len={len(out)}"
        elif hasattr(out, "id"):
            desc = f"id={out.id} name={getattr(out, 'name', '?')}"
        print(f"  ok   {label:<44} {elapsed:>5.2f}s  {desc}")
        return out
    except HLTVError as e:
        print(f"  fail {label:<44} HTTP {e.status}: {e}")
    except Exception as e:
        print(f"  fail {label:<44} {type(e).__name__}: {e}")
        traceback.print_exc()
    return None


def main():
    c = HLTVClient()

    print("== Typed bootstrap ==")
    sc = call("get_startup_check()", c.get_startup_check)
    if sc:
        print(f"      android min={sc.android_min_version_code} feature_toggles={list(sc.feature_toggles.keys())}")

    print("\n== Typed events ==")
    call("ongoing_events()", c.ongoing_events)
    call("upcoming_events()", c.upcoming_events)
    call("completed_events()", c.completed_events)
    call("get_finished_events(0)", c.get_finished_events, 0)
    ev = call("get_event(9029)", c.get_event, 9029)
    if ev:
        print(f"      id={ev.id} name={ev.name} dateStart={ev.date_start} teams_count={ev.teams_count}")

    print("\n== Typed teams / players ==")
    t = call("get_team(7020)", c.get_team, 7020)
    if t:
        print(f"      {t.name} #{t.world_rank} roster={[p.nick for p in t.roster]} fans={t.num_fans}")
    p = call("get_player(16920)", c.get_player, 16920)
    if p:
        pf = p.full
        print(f"      {pf.nick} ({pf.country}, age {pf.age}) rating={pf.rating} role_scores={pf.stats.role_scores() if pf.stats else None}")
    call("world_ranking()", c.world_ranking)
    call("valve_ranking()", c.valve_ranking)

    print("\n== Typed matches ==")
    call("upcoming_matches()", c.upcoming_matches)
    call("frontpage_matches()", c.frontpage_matches)
    m = call("get_match(2394156)", c.get_match, 2394156)
    if m:
        print(f"      {m.team1_name} {m.team1_score}-{m.team2_score} {m.team2_name}")
        print(f"      maps={len(m.maps)} form={len(m.team1_form_matches)}/{len(m.team2_form_matches)}")
        print(f"      VRS forecast: {m.vrs_forecast}")

    print("\n== Typed search ==")
    sr = call("get_search('vitality')", c.get_search, "vitality")
    if sr:
        print(f"      teams={len(sr.teams)} players={len(sr.players)} events={len(sr.events)}")
    call("get_search_empty_state()", c.get_search_empty_state)
    call("find_team('faze')", c.find_team, "faze")
    call("find_player('s1mple')", c.find_player, "s1mple")
    call("find_event('cologne')", c.find_event, "cologne")
    call("search_events('major')", c.search_events, "major")
    call("search_players('donk')", c.search_players, "donk")
    call("search_teams('spirit')", c.search_teams, "spirit")

    print("\n== Typed fan rankings ==")
    call("get_top_player_fans(0)", c.get_top_player_fans, 0)
    call("get_top_team_fans(0)", c.get_top_team_fans, 0)

    print("\n== Typed forum ==")
    call("get_forum_threads()", c.get_forum_threads)
    ft = call("get_forum_thread(3150219)", c.get_forum_thread, 3150219)
    if ft:
        print(f"      subject={ft.subject!r} replies={len(ft.replies)} total={ft.total_replies}")

    print("\n== Typed event stats / player compare ==")
    pes = call("get_event_stats(8049)", c.get_event_stats, 8049)
    if pes:
        top = max(pes, key=lambda x: x.rating or 0)
        print(f"      top rater: {top.player.nick} ({top.rating:.2f}, {top.maps_played} maps)")
    pc = call("compare_players(21167,16920,12554)", c.compare_players, 21167, 16920, 12554)
    if pc:
        print(f"      winners: {[(p.nick, p.wins_count()) for p in pc]}")
    call("popular_players()", c.popular_players)

    print("\n== Frontpage / feeds ==")
    call("frontpage_articles()", c.frontpage_articles)
    call("hero_news()", c.hero_news)
    call("featured_events()", c.featured_events)
    call("pickem_events()", c.pickem_events)

    print("\n== Article ==")
    a = call("get_article(44718)", c.get_article, 44718)
    if a:
        print(f"      id={a.id} web_url={a.web_url} discussion_id={a.discussion_id}")

    print(f"\n== Cache stats: {c.cache.stats} ==")


if __name__ == "__main__":
    sys.exit(main() or 0)
