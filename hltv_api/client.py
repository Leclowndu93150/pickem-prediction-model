"""
Python wrapper for the HLTV mobile API.

Reverse-engineered from the official HLTV Android app v3.0.34
(package: org.hltv.android). All endpoints live under
https://www.hltv.org/mobile.

The wrapper uses curl_cffi to impersonate a real mobile-app TLS
fingerprint so requests are not rejected with HTTP 403.
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

try:
    from curl_cffi import requests as _curl_requests
except ImportError as exc:
    raise ImportError(
        "curl_cffi is required. Install with: pip install curl_cffi"
    ) from exc

from .cache import DiskCache
from .proxy import ProxyPool


def _to_int_safe(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


BASE_URL = "https://www.hltv.org/mobile"
WEB_REFERER = "https://www.hltv.org/"
APP_VERSION = "3.0.34"
PACKAGE = "org.hltv.android"

_IOS_VERSIONS = ["14.8", "15.6", "16.5", "17.2", "17.4"]
_ANDROID_DEVICES = [
    "SM-S918B; Android 14",
    "Pixel 8; Android 14",
    "SM-A536B; Android 13",
    "Pixel 7; Android 14",
]


class HLTVError(Exception):
    """Raised when an HLTV mobile API call fails."""

    def __init__(self, status: int, message: str, body: str = ""):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.body = body


class HLTVClient:
    """
    Synchronous client for the HLTV mobile API.

    Parameters
    ----------
    session_id : str, optional
        Existing PHPSESSID cookie value. Supply this to use a logged-in
        session without calling :py:meth:`login`.
    autologin : str, optional
        Existing autologin cookie value (paired with PHPSESSID for
        persistent logins).
    impersonate : str, default ``"chrome120"``
        curl_cffi browser fingerprint to mimic. The mobile app uses an
        OkHttp-on-Conscrypt fingerprint that is closest to recent Chrome
        on Android; ``chrome120`` is the safest default at the time of
        writing.
    user_agent : str, optional
        Override the User-Agent header. By default a User-Agent matching
        the official Android app (``org.hltv.android/3.0.34;release(...)``)
        is generated with a random device tail.
    timeout : float, default ``20.0``
        Request timeout in seconds.

    Attributes
    ----------
    session : curl_cffi.requests.Session
        The underlying HTTP session. Cookies persist on it.
    """

    def __init__(
        self,
        session_id: str | None = None,
        autologin: str | None = None,
        impersonate: str = "chrome120",
        user_agent: str | None = None,
        timeout: float = 20.0,
        cache: DiskCache | bool | None = True,
        max_retries: int = 3,
        retry_base_delay: float = 1.5,
        proxy_pool: ProxyPool | bool | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_base_delay = max(0.0, retry_base_delay)
        if isinstance(proxy_pool, ProxyPool):
            self.proxy_pool = proxy_pool
        elif proxy_pool is False:
            self.proxy_pool = None
        else:
            default_proxy_file = (
                Path.home() / ".config" / "hltv" / "webshare_proxies.txt"
            )
            self.proxy_pool = (
                ProxyPool.from_file(default_proxy_file)
                if default_proxy_file.exists()
                else None
            )
        self.session = _curl_requests.Session(impersonate=impersonate)
        self.session.headers.update(
            {
                "User-Agent": user_agent or self._default_user_agent(),
                "Referer": WEB_REFERER,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        if session_id:
            self.session.cookies.set("PHPSESSID", session_id, domain=".hltv.org")
        if autologin:
            self.session.cookies.set("autologin", autologin, domain=".hltv.org")

        if isinstance(cache, DiskCache):
            self.cache = cache
        elif cache is False or cache is None:
            self.cache = DiskCache(enabled=False)
        else:
            self.cache = DiskCache()

    @staticmethod
    def _default_user_agent() -> str:
        """Build a User-Agent string mimicking the HLTV Android app."""
        device = random.choice(_ANDROID_DEVICES)
        return f"{PACKAGE}/{APP_VERSION};release(Android; {device})"

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        data: Any | None = None,
        use_cache: bool = True,
        ttl: int | None = None,
    ) -> Any:
        """Low-level request. Returns parsed JSON or raises HLTVError.

        Reads cache on GET only (POSTs have side effects and are skipped).
        """
        if method.upper() == "GET" and use_cache:
            hit = self.cache.get(method, path, params, ttl=ttl)
            if hit is not None:
                return hit
        url = f"{BASE_URL}/{path.lstrip('/')}"
        resp = None
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            proxy = self.proxy_pool.acquire() if self.proxy_pool else None
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                    timeout=self.timeout,
                    proxy=proxy,
                )
            except Exception as exc:
                last_error = exc
                if proxy and self.proxy_pool:
                    self.proxy_pool.mark_failed(proxy)
                if attempt >= self.max_retries:
                    raise
                time.sleep(
                    min(
                        10.0,
                        self.retry_base_delay * (2**attempt)
                        + random.uniform(0.0, 0.5),
                    )
                )
                continue
            if resp.status_code not in (403, 407, 429, 500, 502, 503, 504):
                break
            if proxy and self.proxy_pool:
                self.proxy_pool.mark_failed(proxy)
            if attempt >= self.max_retries:
                break
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 0.0
            except ValueError:
                delay = 0.0
            if delay <= 0.0:
                delay = self.retry_base_delay * (2**attempt) + random.uniform(0.0, 0.5)
            time.sleep(min(30.0, delay))
        assert resp is not None
        if resp.status_code >= 400:
            raise HLTVError(
                resp.status_code,
                resp.reason or "request failed",
                resp.text[:500],
            )
        ct = resp.headers.get("Content-Type", "")
        if "json" in ct or resp.text.startswith(("{", "[")):
            try:
                value = resp.json()
            except Exception:
                value = resp.text
        else:
            value = resp.text
        if method.upper() == "GET" and use_cache and isinstance(value, (dict, list)):
            self.cache.set(method, path, params, value)
        return value

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> Any:
        return self._request("GET", path, params=params, ttl=ttl)

    def _post(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        data: Any | None = None,
    ) -> Any:
        return self._request(
            "POST", path, params=params, json=json, data=data, use_cache=False
        )

    # ---------------------------------------------------------------- core

    def startup_check(self) -> Any:
        """
        GET ``/startupCheck`` - application bootstrap payload.

        Returns
        -------
        dict
            Feature toggles, country override, EventHub data and
            platform-specific (Android/iOS) configuration.
            DTO: ``StartupCheck``.
        """
        return self._get("/startupCheck")

    def onboarding(self) -> Any:
        """
        GET ``/Onboarding`` - initial onboarding data shown on first
        launch.

        Returns
        -------
        dict
            DTO: ``OnboardingData``.
        """
        return self._get("Onboarding")

    def mobile_ads(self) -> Any:
        """
        GET ``/ads`` - current mobile-app ad configuration and placements.

        (The decompiled app sources reference ``/v2/ads`` but the live
        mount is ``/ads``; ``/v2/ads`` returns 404.)

        Returns
        -------
        dict
            DTO: ``MobileAds`` (includes ``Configuration`` and ``Ad`` list).
        """
        return self._get("/ads")

    # ---------------------------------------------------------------- frontpage / news

    def frontpage(self) -> Any:
        """
        GET ``/FrontpageV2`` - main app home feed (hero news, featured
        matches, events).

        Returns
        -------
        dict
            DTO: ``FrontpageData``.
        """
        return self._get("/FrontpageV2")

    def article(self, article_id: int) -> Any:
        """
        GET ``/articleScreen?articleId=...`` - full news article.

        Parameters
        ----------
        article_id : int

        Returns
        -------
        dict
            DTO: ``ArticleScreenData``.
        """
        return self._get("/articleScreen", {"articleId": article_id})

    # ---------------------------------------------------------------- matches

    def matches(self) -> Any:
        """
        GET ``/MatchesV4`` - global upcoming + live matches list.

        Returns
        -------
        dict
            DTO: ``GlobalMatches``.
        """
        return self._get("/MatchesV4")

    def match(self, match_id: int) -> Any:
        """
        GET ``/MatchScreen?matchId=...`` - full match detail screen
        (lineups, head-to-head, maps, streams, odds, etc.).

        Parameters
        ----------
        match_id : int

        Returns
        -------
        dict
            DTO: ``MatchScreenData``.
        """
        return self._get("/MatchScreen", {"matchId": match_id})

    def match_subscriptions(self) -> Any:
        """
        GET ``/matches/subscriptions`` - list of matches the current
        session is subscribed to.

        Returns
        -------
        dict
            DTO: ``MatchSubscriptions``.
        """
        return self._get("matches/subscriptions")

    def pick_a_winner(
        self, match_id: int, team_id: int, map_index: int = 0
    ) -> Any:
        """
        POST ``/MatchScreen/pickAWinner`` - submit a pick-a-winner vote
        for a match.

        Parameters
        ----------
        match_id : int
        team_id : int
            The team being picked.
        map_index : int, default ``0``
            Map index inside the match (0 = series winner).

        Returns
        -------
        dict
            DTO: ``PickAWinnerResponse``.
        """
        return self._post(
            "/MatchScreen/pickAWinner",
            json={
                "matchId": match_id,
                "teamId": team_id,
                "mapIndex": map_index,
            },
        )

    # ---------------------------------------------------------------- events

    def events(self) -> Any:
        """
        GET ``/EventsData2`` - list of ongoing and upcoming events.

        Returns
        -------
        dict
            DTO: ``EventsData``.
        """
        return self._get("EventsData2")

    def finished_events(self, offset: int = 0) -> Any:
        """
        GET ``/FinishedEventsData?offset=...`` - completed events feed
        (paginated).

        Parameters
        ----------
        offset : int, default ``0``

        Returns
        -------
        dict
            DTO: ``FinishedEventsData``.
        """
        return self._get("FinishedEventsData", {"offset": offset})

    def event(self, event_id: int) -> Any:
        """
        GET ``/EventDetails2?eventId=...`` - full event detail.

        Parameters
        ----------
        event_id : int

        Returns
        -------
        dict
            DTO: ``EventDetails``.
        """
        return self._get("/EventDetails2", {"eventId": event_id})

    def event_stats(
        self, event_id: int, playoff_only: bool = False
    ) -> Any:
        """
        GET ``/event/stats?event=...&playoffOnly=...`` - aggregated player
        stats for an event.

        Note: the query parameter is ``event`` (not ``eventId``) - this
        is one of the few endpoints with that quirk.

        Parameters
        ----------
        event_id : int
        playoff_only : bool, default ``False``

        Returns
        -------
        dict
            DTO: ``EventStatsScreenData`` (top-level: ``playerStats``).
        """
        return self._get(
            "/event/stats",
            {"event": event_id, "playoffOnly": str(playoff_only).lower()},
        )

    def eventhub(
        self,
        event_id: int,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> Any:
        """
        GET ``/eventhub?eventId=...`` - on-site EventHub screen (check-in,
        program, venue info) for a specific live event.

        Returns 404 unless ``event_id`` corresponds to an event currently
        listed in ``startup_check().eventHubs``.

        Parameters
        ----------
        event_id : int
        latitude, longitude : float, optional
            Approximate user location - used by the app to gate physical
            check-in features.

        Returns
        -------
        dict
            DTO: ``EventHubScreen``.
        """
        params: dict[str, Any] = {"eventId": event_id}
        if latitude is not None:
            params["latitude"] = latitude
        if longitude is not None:
            params["longitude"] = longitude
        return self._get("/eventhub", params)

    def checkin(self, event_id: int, spot_id: int | None = None) -> Any:
        """
        POST ``/checkin`` - perform an on-site EventHub check-in.

        Parameters
        ----------
        event_id : int
        spot_id : int, optional

        Returns
        -------
        dict
            DTO: ``CheckInResult``.
        """
        body: dict[str, Any] = {"eventId": event_id}
        if spot_id is not None:
            body["spotId"] = spot_id
        return self._post("/checkin", json=body)

    # ---------------------------------------------------------------- ranking

    def ranking(self, ranking_id: int | str | None = None) -> Any:
        """
        GET ``/v2/ranking`` - HLTV team ranking snapshot (HLTV ranking +
        Valve ranking).

        Parameters
        ----------
        ranking_id : int or str, optional
            Specific snapshot id (from ``availableDates`` in the
            response). Omit for the current ranking.

        Returns
        -------
        dict
            DTO: ``TeamRanking``. Top-level keys:
            ``teams``, ``availableDates``, ``selectedDate``,
            ``valveTeams``, ``valveSelectedDate``, ``valveRankingStartDate``.
        """
        params = {"rankingId": ranking_id} if ranking_id is not None else None
        return self._get("v2/ranking", params)

    # ---------------------------------------------------------------- search

    def search(self, term: str) -> Any:
        """
        GET ``/v2/search?term=...`` - unified search across teams, players
        and events.

        Parameters
        ----------
        term : str

        Returns
        -------
        dict
            DTO: ``SearchResult``.
        """
        return self._get("v2/search", {"term": term})

    def search_empty_state(self) -> Any:
        """
        GET ``/v2/searchEmptyState`` - content shown on the search
        screen when no query has been entered.

        Returns
        -------
        dict
            DTO: ``SearchEmptyState``.
        """
        return self._get("v2/searchEmptyState")

    def search_events(self, term: str) -> Any:
        """
        GET ``/search/events?term=...``

        Parameters
        ----------
        term : str

        Returns
        -------
        dict
            DTO: ``EventSearch``.
        """
        return self._get("search/events", {"term": term})

    def search_players(self, term: str) -> Any:
        """
        GET ``/search/players?term=...``

        Parameters
        ----------
        term : str

        Returns
        -------
        dict
            DTO: ``PlayerSearch``.
        """
        return self._get("search/players", {"term": term})

    def search_teams(self, term: str) -> Any:
        """
        GET ``/search/teams?term=...``

        Parameters
        ----------
        term : str

        Returns
        -------
        dict
            DTO: ``TeamSearch``.
        """
        return self._get("search/teams", {"term": term})

    # ---------------------------------------------------------------- player / team

    def player(self, player_id: int) -> Any:
        """
        GET ``/PlayerScreen?playerId=...`` - player profile.

        Parameters
        ----------
        player_id : int

        Returns
        -------
        dict
            DTO: ``PlayerScreenData``.
        """
        return self._get("PlayerScreen", {"playerId": player_id})

    def player_compare(self, *player_ids: int) -> Any:
        """
        GET ``/PlayerCompare?playerIds=...&playerIds=...`` - side-by-side
        comparison of two or more players.

        Important: the API expects ``playerIds`` to be **repeated** once
        per id (not comma-joined). The wrapper handles this for you.

        Parameters
        ----------
        *player_ids : int

        Returns
        -------
        dict
            DTO: ``PlayerCompareScreenData`` with ``playersStats`` and
            ``popularPlayers``.
        """
        # Build the URL with repeated query params manually because
        # passing a list to ``params`` produces ``playerIds=1%2C2``.
        ids = "&".join(f"playerIds={p}" for p in player_ids)
        return self._get(f"PlayerCompare?{ids}")

    def team(self, team_id: int) -> Any:
        """
        GET ``/TeamScreen?teamId=...`` - team profile.

        Parameters
        ----------
        team_id : int

        Returns
        -------
        dict
            DTO: ``TeamInfo``.
        """
        return self._get("TeamScreen", {"teamId": team_id})

    def top_player_fans(self, offset: int = 0) -> Any:
        """
        GET ``/topPlayersByFanCount?offset=...`` - leaderboard of players
        sorted by number of fans.

        Parameters
        ----------
        offset : int, default ``0``

        Returns
        -------
        dict
            DTO: ``TopPlayerFansData``.
        """
        return self._get("topPlayersByFanCount", {"offset": offset})

    def top_team_fans(self, offset: int = 0) -> Any:
        """
        GET ``/topTeamsByFanCount?offset=...`` - leaderboard of teams
        sorted by number of fans.

        Parameters
        ----------
        offset : int, default ``0``

        Returns
        -------
        dict
            DTO: ``TopTeamFansData``.
        """
        return self._get("topTeamsByFanCount", {"offset": offset})

    # ---------------------------------------------------------------- forum

    def forum_threads(
        self,
        forum_types: str = "all",
        offset: int = 0,
        all_threads: bool = False,
    ) -> Any:
        """
        GET ``/ForumContent`` - list of forum threads.

        Parameters
        ----------
        forum_types : str, default ``"all"``
            Comma-separated forum-type ids, or ``"all"``.
        offset : int, default ``0``
        all_threads : bool, default ``False``
            If True, include all threads regardless of subscription.

        Returns
        -------
        dict
            DTO: ``ForumThreadData``.
        """
        return self._get(
            "ForumContent",
            {
                "forumTypes": forum_types,
                "offset": offset,
                "all": str(bool(all_threads)).lower(),
            },
        )

    def forum_thread(self, thread_id: int) -> Any:
        """
        GET ``/ForumThread?threadId=...`` - single thread with its posts.

        Parameters
        ----------
        thread_id : int

        Returns
        -------
        dict
            DTO: ``ForumThreadPost``.
        """
        return self._get("ForumThread", {"threadId": thread_id})

    def forum_init_all_types(
        self, notification_thread_ids: list[int] | None = None
    ) -> Any:
        """
        GET ``/ForumInitAllTypes`` - list of all forum categories plus
        notification topic content.

        Parameters
        ----------
        notification_thread_ids : list[int], optional

        Returns
        -------
        dict
            DTO: ``ForumTopicContent``.
        """
        params = {}
        if notification_thread_ids:
            params["notificationThreadIds"] = ",".join(
                str(i) for i in notification_thread_ids
            )
        return self._get("ForumInitAllTypes", params or None)

    def forum_create_topic_data(self) -> Any:
        """
        GET ``/ForumCreateTopicData`` - form metadata for creating a new
        forum thread (forum types, max lengths, ...).

        Returns
        -------
        dict
            DTO: ``ForumCreateTopicData``.
        """
        return self._get("ForumCreateTopicData")

    def forum_create_thread(
        self, forum_type: int, subject: str, title: str, message: str
    ) -> Any:
        """
        POST ``/ForumCreateTopicAction`` - create a new forum thread.

        Parameters
        ----------
        forum_type : int
        subject : str
        title : str
        message : str

        Returns
        -------
        dict
            DTO: ``ForumCreateThreadResponse``.
        """
        return self._post(
            "ForumCreateTopicAction",
            json={
                "forumType": forum_type,
                "subject": subject,
                "title": title,
                "message": message,
            },
        )

    def forum_reply(self, thread_id: int, message: str) -> Any:
        """
        POST ``/forum/post`` - reply to a forum thread.

        Parameters
        ----------
        thread_id : int
        message : str

        Returns
        -------
        dict
            DTO: ``PostReplyResponse``.
        """
        return self._post(
            "forum/post", json={"threadId": thread_id, "message": message}
        )

    def forum_plus_reply(self, reply_id: int, vote: int = 1) -> Any:
        """
        POST ``/forum/plusReply`` - toggle a +1 on a forum reply.

        Parameters
        ----------
        reply_id : int
        vote : int, default ``1``
            ``1`` to plus, ``0`` to remove.

        Returns
        -------
        dict
            DTO: ``TogglePlusResponse``.
        """
        return self._post(
            "forum/plusReply", params={"replyId": reply_id, "vote": vote}
        )

    def forum_plus_thread(self, thread_id: int, vote: int = 1) -> Any:
        """
        POST ``/forum/plusThread`` - toggle a +1 on a forum thread.

        Parameters
        ----------
        thread_id : int
        vote : int, default ``1``

        Returns
        -------
        dict
            DTO: ``TogglePlusThreadResponse``.
        """
        return self._post(
            "forum/plusThread", params={"threadId": thread_id, "vote": vote}
        )

    def forum_notifications(
        self,
        notification_thread_ids: list[int] | None = None,
        post_reply_ids: list[int] | None = None,
    ) -> Any:
        """
        GET ``/ForumNotifications`` - unread-state info for the given
        threads and replies.

        Parameters
        ----------
        notification_thread_ids : list[int], optional
        post_reply_ids : list[int], optional

        Returns
        -------
        dict
            DTO: ``ForumNotificationInfo``.
        """
        params: dict[str, Any] = {}
        if notification_thread_ids:
            params["notificationThreadIds"] = ",".join(
                str(i) for i in notification_thread_ids
            )
        if post_reply_ids:
            params["postReplyIds"] = ",".join(str(i) for i in post_reply_ids)
        return self._get("ForumNotifications", params or None)

    # ---------------------------------------------------------------- fantasy

    def fantasy_overview(self) -> Any:
        """
        GET ``/fantasy/overview`` - fantasy hub for the current user.

        Returns
        -------
        dict
            DTO: ``FantasyOverviewData``.
        """
        return self._get("fantasy/overview")

    def fantasy_past_overview(self) -> Any:
        """
        GET ``/fantasy/overview/past`` - completed fantasy seasons.

        Returns
        -------
        dict
            DTO: ``FantasyPastOverviewData``.
        """
        return self._get("fantasy/overview/past")

    def fantasy_menu_notifications(self) -> Any:
        """
        POST ``/fantasy/MenuNotifications`` - menu-level notification
        badges for fantasy.

        Returns
        -------
        dict
            DTO: ``MenuNotificationData``.
        """
        return self._post("fantasy/MenuNotifications")

    def fantasy_redirect(self, fantasy_id: int) -> Any:
        """
        GET ``/fantasy/redirect?fantasyId=...``

        Parameters
        ----------
        fantasy_id : int

        Returns
        -------
        dict
            DTO: ``FantasyRedirectData``.
        """
        return self._get("fantasy/redirect", {"fantasyId": fantasy_id})

    def fantasy_create_team_data(self, fantasy_id: int) -> Any:
        """
        GET ``/fantasy/create?fantasyId=...`` - team-creation screen data.

        Parameters
        ----------
        fantasy_id : int

        Returns
        -------
        dict
            DTO: ``FantasyCreateTeamData``.
        """
        return self._get("fantasy/create", {"fantasyId": fantasy_id})

    def fantasy_create_team(self, payload: dict[str, Any]) -> Any:
        """
        POST ``/fantasy/create`` - submit a new fantasy team.

        Parameters
        ----------
        payload : dict
            Matches the ``FantasyCreateTeamPostData`` DTO: ``fantasyId``,
            ``leagueId``, ``teamName``, ``playerIds``, ``captainId``, ....

        Returns
        -------
        dict
            DTO: ``FantasyCreateTeamPostResponse``.
        """
        return self._post("fantasy/create", json=payload)

    def fantasy_edit_team(self, payload: dict[str, Any]) -> Any:
        """
        POST ``/fantasy/edit`` - update an existing fantasy team.

        Parameters
        ----------
        payload : dict
            Matches ``FantasyEditTeamPostData``.

        Returns
        -------
        dict
            DTO: ``FantasyPostResponse``.
        """
        return self._post("fantasy/edit", json=payload)

    def fantasy_delete_team(
        self, fantasy_id: int, league_id: int, team_id: int
    ) -> Any:
        """
        POST ``/fantasy/delete`` - delete a fantasy team.

        Parameters
        ----------
        fantasy_id : int
        league_id : int
        team_id : int

        Returns
        -------
        dict
            DTO: ``FantasyPostResponse``.
        """
        return self._post(
            "fantasy/delete",
            json={
                "fantasyId": fantasy_id,
                "leagueId": league_id,
                "teamId": team_id,
            },
        )

    def fantasy_team_overview(self, fantasy_id: int) -> Any:
        """
        GET ``/fantasy/teamOverview?fantasyId=...``

        Returns
        -------
        dict
            DTO: ``FantasyTeamOverviewData``.
        """
        return self._get("fantasy/teamOverview", {"fantasyId": fantasy_id})

    def fantasy_player_overview(
        self, fantasy_id: int, player_id: int
    ) -> Any:
        """
        GET ``/fantasy/playerOverview?fantasyId=...&playerId=...``

        Parameters
        ----------
        fantasy_id : int
        player_id : int

        Returns
        -------
        dict
            DTO: ``FantasyPlayerOverviewData``.
        """
        return self._get(
            "fantasy/playerOverview",
            {"fantasyId": fantasy_id, "playerId": player_id},
        )

    def fantasy_boosters(self, fantasy_id: int) -> Any:
        """
        GET ``/fantasy/boosters?fantasyId=...`` - booster picker data.

        Returns
        -------
        dict
            DTO: ``FantasySetBoosterData``.
        """
        return self._get("fantasy/boosters", {"fantasyId": fantasy_id})

    def fantasy_roles(self, fantasy_id: int) -> Any:
        """
        GET ``/fantasy/roles?fantasyId=...`` - role-assignment data.

        Returns
        -------
        dict
            DTO: ``FantasySetRoleData``.
        """
        return self._get("fantasy/roles", {"fantasyId": fantasy_id})

    def fantasy_assign_boosters(self, payload: dict[str, Any]) -> Any:
        """
        POST ``/fantasy/assign/boosters`` - submit selected boosters.

        Parameters
        ----------
        payload : dict
            Matches ``FantasySetBoosterData``.

        Returns
        -------
        dict
            DTO: ``FantasyPostBoosterResponse``.
        """
        return self._post("fantasy/assign/boosters", json=payload)

    def fantasy_assign_roles(self, payload: dict[str, Any]) -> Any:
        """
        POST ``/fantasy/assign/roles`` - submit player roles.

        Parameters
        ----------
        payload : dict
            Matches ``FantasySetRoleData``.

        Returns
        -------
        dict
            DTO: ``FantasyPostRoleResponse``.
        """
        return self._post("fantasy/assign/roles", json=payload)

    def fantasy_game_leaderboard(
        self, fantasy_id: int, offset: int = 0
    ) -> Any:
        """
        GET ``/fantasy/game/leaderboard?fantasyId=...&offset=...``

        Returns
        -------
        dict
            DTO: ``FantasyGameLeaderboardData``.
        """
        return self._get(
            "fantasy/game/leaderboard",
            {"fantasyId": fantasy_id, "offset": offset},
        )

    def fantasy_season_leaderboard(self, offset: int = 0) -> Any:
        """
        GET ``/fantasy/season/leaderboard?offset=...``

        Returns
        -------
        dict
            DTO: ``FantasySeasonLeaderboardData``.
        """
        return self._get("fantasy/season/leaderboard", {"offset": offset})

    # ---------------------------------------------------------------- user / auth

    def login(self, username: str, password: str) -> Any:
        """
        POST ``/Login`` - authenticate and store the resulting cookies on
        :attr:`session`.

        Parameters
        ----------
        username : str
        password : str

        Returns
        -------
        dict
            DTO: ``NewLoginResponse``.
        """
        return self._post(
            "Login", json={"username": username, "password": password}
        )

    def logout(self, session_id: str | None = None) -> None:
        """
        POST ``/Logout`` - end the current session.

        Parameters
        ----------
        session_id : str, optional
            Defaults to the cookie currently on :attr:`session`.

        Returns
        -------
        None
        """
        sid = session_id or self.session.cookies.get("PHPSESSID")
        self._post("Logout", json={"sessionId": sid})
        return None

    def user_page(self, user_id: int) -> Any:
        """
        GET ``/UserInfoData?userId=...`` - public user profile page.

        Parameters
        ----------
        user_id : int

        Returns
        -------
        dict
            DTO: ``UserPage``.
        """
        return self._get("UserInfoData", {"userId": user_id})

    def user_in_app_notifications(self) -> Any:
        """
        GET ``/UserInAppNotification`` - in-app notification feed.

        Returns
        -------
        dict
            DTO: ``UserInAppNotificationData``.
        """
        return self._get("UserInAppNotification")

    def user_notification_settings(self) -> Any:
        """
        GET ``/UserNotificationSettings`` - current notification
        preferences.

        Returns
        -------
        dict
            DTO: ``UserNotificationSettingsData``.
        """
        return self._get("UserNotificationSettings")

    def user_settings(self) -> Any:
        """
        GET ``/UserSettingsOrDefault`` - user app settings (or defaults
        for anonymous sessions).

        Requires authentication (PHPSESSID); returns 404 anonymously.

        Returns
        -------
        dict
            DTO: ``UserSettingsOrDefault``.
        """
        return self._get("UserSettingsOrDefault")

    def request_delete_account(self) -> Any:
        """
        POST ``/requestDeleteAccount`` - request deletion of the
        currently authenticated account.

        Returns
        -------
        dict
            DTO: ``RequestDeleteAccountResponse``.
        """
        return self._post("requestDeleteAccount")

    # ---------------------------------------------------------------- settings

    def change_favorites(
        self,
        add_players: list[int] | None = None,
        add_teams: list[int] | None = None,
        remove_players: list[int] | None = None,
        remove_teams: list[int] | None = None,
    ) -> Any:
        """
        POST ``/settings/changeFavorites`` - add/remove favourite teams
        and players.

        Returns
        -------
        dict
            DTO: ``ChangeFavoritesResponse``.
        """
        return self._post(
            "settings/changeFavorites",
            json={
                "addPlayers": add_players or [],
                "addTeams": add_teams or [],
                "removePlayers": remove_players or [],
                "removeTeams": remove_teams or [],
            },
        )

    def event_subscription_status(self, event_id: int) -> Any:
        """
        POST ``/settings/EventSubscription`` - current subscription
        state for an event.

        Returns
        -------
        dict
            DTO: ``SubscriptionStatus``.
        """
        return self._post(
            "settings/EventSubscription", json={"eventId": event_id}
        )

    def event_subscribe(self, event_id: int) -> None:
        """POST ``/settings/EventSubscribe`` - subscribe to an event."""
        self._post("settings/EventSubscribe", json={"eventId": event_id})

    def event_unsubscribe(self, event_id: int) -> None:
        """POST ``/settings/EventUnsubscribe`` - unsubscribe from an event."""
        self._post("settings/EventUnsubscribe", json={"eventId": event_id})

    def match_subscribe(self, match_id: int) -> None:
        """POST ``/settings/MatchSubscribe`` - subscribe to a match."""
        self._post("settings/MatchSubscribe", json={"matchId": match_id})

    def match_unsubscribe(self, match_id: int) -> None:
        """POST ``/settings/MatchUnsubscribe`` - unsubscribe from a match."""
        self._post("settings/MatchUnsubscribe", json={"matchId": match_id})

    def update_anonymous_settings(self, settings: dict[str, Any]) -> None:
        """
        POST ``/settings/UpdateAnonymousSettings`` - push settings for an
        unauthenticated session.

        Parameters
        ----------
        settings : dict
        """
        self._post("settings/UpdateAnonymousSettings", json=settings)

    def update_user_settings(
        self,
        season_fantasy: bool | None = None,
        partner_fantasy: bool | None = None,
        **other: Any,
    ) -> None:
        """
        POST ``/settings/UpdateUserSettings`` - push settings for an
        authenticated session.

        Parameters
        ----------
        season_fantasy : bool, optional
        partner_fantasy : bool, optional
        **other
            Extra setting keys forwarded as-is.
        """
        payload: dict[str, Any] = dict(other)
        if season_fantasy is not None:
            payload["seasonFantasy"] = season_fantasy
        if partner_fantasy is not None:
            payload["partnerFantasy"] = partner_fantasy
        self._post("settings/UpdateUserSettings", json=payload)

    # ====================================================================
    # Typed accessors - return domain objects (cache-backed).
    # ====================================================================

    def get_team(self, team_id: int) -> "Team":
        """Return a :class:`hltv_api.models.Team` for the given team id."""
        from .models import Team

        return Team(self.team(team_id), self)

    def get_player(self, player_id: int) -> "Player":
        """Return a :class:`hltv_api.models.Player`."""
        from .models import Player

        return Player(self.player(player_id), self)

    def get_match(self, match_id: int) -> "Match":
        """Return a :class:`hltv_api.models.Match` wrapping ``matchData``."""
        from .models import Match

        data = self.match(match_id)
        return Match(data.get("matchData", data), self)

    def get_event(self, event_id: int) -> "Event":
        """Return a :class:`hltv_api.models.Event`."""
        from .models import Event

        return Event(self.event(event_id), self)

    def get_article(self, article_id: int) -> "Article":
        """Return a :class:`hltv_api.models.Article`. The article id is
        attached to the wrapper since the API response itself does not
        echo it back."""
        from .models import Article

        raw = self.article(article_id)
        # Inject the id so Article.id is non-None.
        ad = raw.get("articleData") or {}
        if "articleId" not in ad and "id" not in ad and "newsId" not in ad:
            ad["articleId"] = article_id
            raw["articleData"] = ad
        return Article(raw, self)

    # ---------------------------------------------------------------- bulk

    def bulk_teams(
        self, team_ids: list[int], max_workers: int | None = None
    ) -> list["Team"]:
        """
        Fetch many teams in parallel. Cache-aware: hits return instantly,
        misses run on a thread pool.

        Parameters
        ----------
        team_ids : list[int]
        max_workers : int, default ``8``

        Returns
        -------
        list[Team]
            In the same order as ``team_ids``.
        """
        from concurrent.futures import ThreadPoolExecutor

        workers = max_workers or (24 if self.proxy_pool else 8)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(self.get_team, team_ids))
        return results

    def bulk_players(
        self, player_ids: list[int], max_workers: int | None = None
    ) -> list["Player"]:
        """Fetch many players in parallel; see :py:meth:`bulk_teams`."""
        from concurrent.futures import ThreadPoolExecutor

        workers = max_workers or (24 if self.proxy_pool else 8)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(self.get_player, player_ids))

    def bulk_matches(
        self, match_ids: list[int], max_workers: int | None = None
    ) -> list["Match"]:
        """Fetch many full match payloads in parallel."""
        from concurrent.futures import ThreadPoolExecutor

        workers = max_workers or (24 if self.proxy_pool else 8)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(self.get_match, match_ids))

    # ---------------------------------------------------------------- compound queries

    def warm_event(self, event_id: int) -> "Event":
        """
        Prefetch everything needed for offline analysis of an event:
        the event detail, every participating team (with rosters),
        every full match payload, and every player's full profile.

        Returns the resolved :class:`hltv_api.models.Event`.
        """
        ev = self.get_event(event_id)
        teams = self.bulk_teams(ev.team_ids)
        # Pull every roster player
        player_ids: list[int] = []
        for t in teams:
            for p in t.roster:
                if p.id is not None and p.id not in player_ids:
                    player_ids.append(p.id)
        self.bulk_players(player_ids)
        # Pull every match the event references
        match_ids = [m.id for m in ev.matches if m.id is not None]
        if match_ids:
            self.bulk_matches(match_ids)
        return ev

    def head_to_head_full(
        self,
        team_a: int,
        team_b: int,
        recent_only: bool = True,
    ) -> dict[str, Any]:
        """
        Aggregate H2H between two teams using the cached/fetched
        ``recentMatches`` of *both* teams, deduped by match id.

        Parameters
        ----------
        team_a, team_b : int
        recent_only : bool, default ``True``
            If True, only uses each team's ``recentMatches`` window
            (~10 series). If False, also pulls each team's events list
            and walks the matches per event (slower; many more requests).

        Returns
        -------
        dict
            ``{"team_a_wins": int, "team_b_wins": int,
              "series": [Match, ...]}``
        """
        from .models import Match

        a = self.get_team(team_a)
        b = self.get_team(team_b)
        seen: set[int] = set()
        series: list[Match] = []
        for source_team in (a, b):
            for m in source_team.recent_matches:
                if m.id is None or m.id in seen:
                    continue
                if team_a in (m.team1_id, m.team2_id) and team_b in (
                    m.team1_id,
                    m.team2_id,
                ):
                    seen.add(m.id)
                    series.append(m)
        a_wins = sum(1 for m in series if m.winner_team_id == team_a)
        b_wins = sum(1 for m in series if m.winner_team_id == team_b)
        return {
            "team_a_wins": a_wins,
            "team_b_wins": b_wins,
            "series": series,
        }

    # ---------------------------------------------------------------- search

    def find_team(self, name: str) -> "Team | None":
        """
        Resolve a free-text team name to a :class:`Team`. Returns the
        first hit from ``/v2/search``, or None if no team is found.
        """
        from .models import Team

        result = self.search(name)
        teams = result.get("teams") or []
        if not teams:
            return None
        tid = teams[0].get("teamId") or teams[0].get("id")
        if tid is None:
            return None
        return self.get_team(int(tid))

    def find_player(self, name: str) -> "Player | None":
        """Same as :py:meth:`find_team` but for players."""
        from .models import Player

        result = self.search(name)
        players = result.get("players") or []
        if not players:
            return None
        pid = players[0].get("playerId") or players[0].get("id")
        if pid is None:
            return None
        return self.get_player(int(pid))

    # ---------------------------------------------------------------- top-level feeds

    def upcoming_matches(self) -> list["Match"]:
        """All upcoming + live matches as :class:`Match` objects."""
        from .models import Match

        data = self.matches()
        return [Match(m, self) for m in (data.get("matches") or [])]

    def frontpage_matches(self) -> list["Match"]:
        """Featured matches from the frontpage feed."""
        from .models import Match

        data = self.frontpage()
        return [Match(m, self) for m in (data.get("matches") or [])]

    def frontpage_articles(self) -> list[dict]:
        """News article summaries from the frontpage. Each has
        ``id`` (use with :py:meth:`get_article`), ``title``, ``created``,
        ``author``, ``event``."""
        return list(self.frontpage().get("articles") or [])

    def hero_news(self) -> list[dict]:
        """The 5 hero-news items shown on top of the frontpage."""
        return list(self.frontpage().get("heroNews") or [])

    def featured_events(self) -> list[dict]:
        """``featuredEvents[]`` from the frontpage - each has
        ``eventId``, ``name``, ``teams``, ``hasPickem``."""
        return list(self.frontpage().get("featuredEvents") or [])

    def pickem_events(self) -> list["Event"]:
        """All featured events that currently host a pickem."""
        from .models import Event

        out: list[Event] = []
        for ev in self.featured_events():
            if ev.get("hasPickem"):
                eid = int(ev["eventId"])
                out.append(self.get_event(eid))
        return out

    def world_ranking(self) -> list["Team"]:
        """
        HLTV team ranking as :class:`Team` objects. Note: the ranking
        endpoint returns rank summaries (rankPoints, rankChange, roster)
        - each Team's full profile is fetched lazily on attribute access
        of fields not in the summary (e.g. ``team.recent_matches``).
        """
        from .models import Team

        data = self.ranking()
        out: list[Team] = []
        for entry in data.get("teams") or []:
            # Normalise to the team-profile shape so Team properties work.
            normalised = {
                **entry,
                "teamName": entry.get("teamId"),
                "name": entry.get("teamName"),
                "logo": entry.get("teamLogo"),
                "worldRank": entry.get("teamRank"),
                "vrsRank": _to_int_safe(entry.get("rankPoints")),
                "activeLineup": entry.get("players") or [],
            }
            out.append(Team(normalised, self))
        return out

    def valve_ranking(self) -> list[dict]:
        """Raw Valve Regional Standings list (``valveTeams``)."""
        return list(self.ranking().get("valveTeams") or [])

    # ---------------------------------------------------------------- deep helpers

    # ---------------------------------------------------------------- forum (typed)

    def get_forum_threads(
        self,
        forum_types: str = "all",
        offset: int = 0,
        all_threads: bool = False,
    ) -> list["ForumThread"]:
        """Forum thread list as typed :class:`ForumThread` objects."""
        from .models import ForumThread

        data = self.forum_threads(forum_types, offset, all_threads)
        return [ForumThread(t, self) for t in data.get("forumThreads") or []]

    def get_forum_thread(self, thread_id: int) -> "ForumPost":
        """Full :class:`ForumPost` (with replies)."""
        from .models import ForumPost

        return ForumPost(self.forum_thread(thread_id), self)

    # ---------------------------------------------------------------- search (typed)

    def get_search(self, term: str) -> "SearchResult":
        """Wrap ``/v2/search`` response in :class:`SearchResult`."""
        from .models import SearchResult

        return SearchResult(self.search(term), self)

    def get_search_empty_state(self) -> "SearchResult":
        """Empty-state recommendations as :class:`SearchResult`."""
        from .models import SearchResult

        return SearchResult(self.search_empty_state(), self)

    # ---------------------------------------------------------------- fan rankings (typed)

    def get_top_player_fans(self, offset: int = 0) -> list["FanEntry"]:
        """Top players by fan count as :class:`FanEntry` list."""
        from .models import FanEntry

        data = self.top_player_fans(offset)
        return [FanEntry(e, self) for e in data.get("entries") or []]

    def get_top_team_fans(self, offset: int = 0) -> list["FanEntry"]:
        """Top teams by fan count as :class:`FanEntry` list."""
        from .models import FanEntry

        data = self.top_team_fans(offset)
        return [FanEntry(e, self) for e in data.get("entries") or []]

    # ---------------------------------------------------------------- event stats (typed)

    def get_event_stats(
        self, event_id: int, playoff_only: bool = False
    ) -> list["PlayerEventStats"]:
        """Per-player stats for an event as :class:`PlayerEventStats` list."""
        from .models import PlayerEventStats

        data = self.event_stats(event_id, playoff_only)
        return [
            PlayerEventStats(p, self)
            for p in data.get("playerStats") or []
        ]

    # ---------------------------------------------------------------- events feed (typed)

    def ongoing_events(self) -> list["Event"]:
        """Currently-running events as :class:`Event` objects."""
        from .models import Event

        block = self.events().get("ongoing") or {}
        return [Event({"event": e, **e}, self) for e in block.get("events") or []]

    def upcoming_events(self) -> list["Event"]:
        """Future events as :class:`Event` objects."""
        from .models import Event

        block = self.events().get("upcoming") or {}
        return [Event({"event": e, **e}, self) for e in block.get("events") or []]

    def completed_events(self) -> list["Event"]:
        """Recently-completed events as :class:`Event` objects."""
        from .models import Event

        block = self.events().get("completed") or {}
        return [Event({"event": e, **e}, self) for e in block.get("events") or []]

    def get_finished_events(self, offset: int = 0) -> list["Event"]:
        """Paginated finished events as :class:`Event` objects."""
        from .models import Event

        data = self.finished_events(offset)
        block = data.get("events") or {}
        # On this endpoint ``events`` wraps another ``events`` list,
        # so unwrap once if needed.
        if isinstance(block, dict):
            block = block.get("events") or []
        return [Event({"event": e, **e}, self) for e in block]

    # ---------------------------------------------------------------- player compare (typed)

    def compare_players(self, *player_ids: int) -> list["PlayerComparison"]:
        """
        Compare 2-5 players and return :class:`PlayerComparison` objects
        carrying both raw stat values and the cross-player percentiles
        (``ratingPercentage`` etc.) that HLTV uses to render the bars
        in the mobile compare view.
        """
        from .models import PlayerComparison

        data = self.player_compare(*player_ids)
        return [PlayerComparison(p, self) for p in data.get("playersStats") or []]

    def popular_players(self) -> list["Player"]:
        """The ``popularPlayers`` list bundled with every PlayerCompare call."""
        from .models import Player

        data = self.player_compare()  # empty call gives just popular
        return [Player(p, self) for p in data.get("popularPlayers") or []]

    # ---------------------------------------------------------------- subscription helpers (typed)

    def get_subscription_status(self, event_id: int) -> "SubscriptionStatus":
        """Event subscription status as :class:`SubscriptionStatus`."""
        from .models import SubscriptionStatus

        return SubscriptionStatus(self.event_subscription_status(event_id), self)

    # ---------------------------------------------------------------- bootstrap (typed)

    def get_startup_check(self) -> "StartupCheck":
        """Bootstrap config as :class:`StartupCheck`."""
        from .models import StartupCheck

        return StartupCheck(self.startup_check(), self)

    # ---------------------------------------------------------------- event search

    def find_event(self, name: str) -> "Event | None":
        """Resolve a free-text event name to an :class:`Event`."""
        sr = self.get_search(name)
        eid = sr.first_event_id()
        return self.get_event(eid) if eid is not None else None

    # ---------------------------------------------------------------- deep helpers

    def team_form_via_match(self, team_id: int) -> list["Match"]:
        """
        Return ~20 recent form matches for a team by leveraging the
        team's most recent finished match's ``team1FormMatches`` /
        ``team2FormMatches`` block. Much deeper than the 10 in
        ``team.recent_matches`` and **free** (it's already in the cached
        match payload).

        Returns an empty list if no recent finished match is cached.
        """
        from .models import Match

        team = self.get_team(team_id)
        for series in team.recent_matches:
            if not series.is_finished or series.id is None:
                continue
            full = series.full
            if full.team1_id == team_id:
                return full.team1_form_matches
            if full.team2_id == team_id:
                return full.team2_form_matches
        return []
