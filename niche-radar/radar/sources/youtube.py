"""YouTube Data API v3 client with quota accounting.

Costs follow Google's published table: search.list is 100 units, videos.list
and channels.list are 1 unit per call. The default allowance is 10,000 units a
day and resets at midnight Pacific Time.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ..http import SourceError
from ..textutil import iso_duration_seconds

BASE = "https://www.googleapis.com/youtube/v3"
COSTS = {"search": 100, "videos": 1, "channels": 1}
_PACIFIC = ZoneInfo("America/Los_Angeles")


class QuotaExceeded(SourceError):
    """The run's own budget would be exceeded; nothing was sent."""


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _chunks(items: list[str], size: int = 50):
    for start in range(0, len(items), size):
        yield items[start : start + size]


class YouTube:
    def __init__(self, http, key: str, store, cfg):
        self.http = http
        self.key = key
        self.store = store
        self.cfg = cfg
        self.spent = 0

    @staticmethod
    def quota_day() -> str:
        return datetime.now(_PACIFIC).date().isoformat()

    def remaining(self) -> int:
        used = self.store.quota_used(self.quota_day())
        return self.cfg.daily_quota - self.cfg.reserve - used

    def _call(self, resource: str, params: dict) -> dict:
        cost = COSTS[resource]
        if self.remaining() < cost:
            raise QuotaExceeded(f"youtube: orçamento diário esgotado antes de {resource}.list")
        # Charged up front: Google bills failed requests as well.
        self.store.add_quota(self.quota_day(), cost)
        self.spent += cost
        url = f"{BASE}/{resource}?" + urlencode(params)
        return self.http.get_json(url, {"X-goog-api-key": self.key})

    def search(
        self,
        market,
        query: str | None,
        published_after: datetime,
        order: str = "viewCount",
        page_token: str | None = None,
    ) -> tuple[list[str], str | None]:
        """Video ids for a query (or for no query at all), most viewed first."""
        params = {
            "part": "id",
            "type": "video",
            "order": order,
            "maxResults": 50,
            "regionCode": market.geo,
            "relevanceLanguage": market.lang,
            "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        data = self._call("search", params)
        ids = [item["id"]["videoId"] for item in data.get("items", []) if item.get("id", {}).get("videoId")]
        return ids, data.get("nextPageToken")

    @staticmethod
    def _video(item: dict) -> dict:
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        return {
            "video_id": item["id"],
            "title": snippet.get("title", ""),
            "channel_id": snippet.get("channelId", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "published_at": snippet.get("publishedAt"),
            "language": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
            "category_id": snippet.get("categoryId"),
            "duration_s": iso_duration_seconds(item.get("contentDetails", {}).get("duration", "")),
            "views": _int(stats.get("viewCount")),
            "likes": _int(stats.get("likeCount")),
            "comments": _int(stats.get("commentCount")),
        }

    def videos(self, ids: list[str]) -> list[dict]:
        found = []
        for chunk in _chunks(list(dict.fromkeys(ids))):
            data = self._call(
                "videos",
                {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk), "maxResults": 50},
            )
            found.extend(self._video(item) for item in data.get("items", []))
        return found

    def most_popular(self, market, pages: int = 1) -> list[dict]:
        """The region's most popular videos chart. One unit per page of 50."""
        found, token = [], None
        for _ in range(pages):
            params = {
                "part": "snippet,statistics,contentDetails",
                "chart": "mostPopular",
                "regionCode": market.geo,
                "maxResults": 50,
            }
            if token:
                params["pageToken"] = token
            data = self._call("videos", params)
            found.extend(self._video(item) for item in data.get("items", []))
            token = data.get("nextPageToken")
            if not token:
                break
        return found

    def channels(self, ids: list[str]) -> dict[str, dict]:
        found = {}
        for chunk in _chunks(list(dict.fromkeys(i for i in ids if i))):
            data = self._call(
                "channels", {"part": "snippet,statistics", "id": ",".join(chunk), "maxResults": 50}
            )
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                hidden = stats.get("hiddenSubscriberCount", False)
                found[item["id"]] = {
                    "channel_id": item["id"],
                    "title": snippet.get("title", ""),
                    "created_at": snippet.get("publishedAt"),
                    "country": snippet.get("country"),
                    "subs": None if hidden else _int(stats.get("subscriberCount")),
                    "views": _int(stats.get("viewCount")),
                    "videos": _int(stats.get("videoCount")),
                }
        return found
