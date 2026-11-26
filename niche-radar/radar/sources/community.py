"""Open community sources: Hacker News, GitHub, Mastodon and Bluesky.

All are read without an account. Hacker News and GitHub lean heavily towards
technology, so their silence on a subject outside it means nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from ..http import SourceError
from . import Signal


# Hacker News ------------------------------------------------------------------
def hn_front_page(http, limit: int = 30) -> list[Signal]:
    url = f"https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage={limit}"
    hits = http.get_json(url).get("hits", [])
    return [
        Signal(
            source="hackernews",
            market=None,
            term=hit.get("title") or "",
            rank=rank,
            value=hit.get("points"),
            label=f"{hit.get('points', 0)} pontos, {hit.get('num_comments', 0)} comentários",
            url=f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
        )
        for rank, hit in enumerate(hits, 1)
        if hit.get("title")
    ]


def hn_story_count(http, term: str, start: datetime, end: datetime) -> int:
    params = {
        "query": f'"{term}"',
        "tags": "story",
        "hitsPerPage": 0,
        "numericFilters": f"created_at_i>{int(start.timestamp())},created_at_i<{int(end.timestamp())}",
    }
    data = http.get_json("https://hn.algolia.com/api/v1/search_by_date?" + urlencode(params))
    return int(data.get("nbHits", 0))


def hn_trend(http, term: str, now: datetime | None = None) -> dict:
    """Stories mentioning a term in the last 30 days against the 30 before."""
    now = now or datetime.now(timezone.utc)
    recent = hn_story_count(http, term, now - timedelta(days=30), now)
    before = hn_story_count(http, term, now - timedelta(days=60), now - timedelta(days=30))
    return {
        "stories_30d": recent,
        "stories_prev_30d": before,
        "ratio_long": round(recent / before, 2) if before else None,
    }


# GitHub -----------------------------------------------------------------------
def github_new_repos(http, days: int = 30, limit: int = 20, now: datetime | None = None) -> list[Signal]:
    """Repositories created in the last `days`, most starred first."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).date().isoformat()
    params = {"q": f"created:>{since}", "sort": "stars", "order": "desc", "per_page": limit}
    data = http.get_json(
        "https://api.github.com/search/repositories?" + urlencode(params),
        {"Accept": "application/vnd.github+json"},
    )
    return [
        Signal(
            source="github",
            market=None,
            term=repo["full_name"],
            rank=rank,
            value=repo.get("stargazers_count"),
            label=f"{repo.get('stargazers_count', 0)} estrelas",
            url=repo.get("html_url"),
            extra={"description": repo.get("description"), "language": repo.get("language")},
        )
        for rank, repo in enumerate(data.get("items", []), 1)
    ]


# Mastodon ---------------------------------------------------------------------
def mastodon_trends(http, limit: int = 20) -> list[Signal]:
    """Hashtags trending on mastodon.social, with uses today and over the week."""
    tags = http.get_json(f"https://mastodon.social/api/v1/trends/tags?limit={limit}")
    if not isinstance(tags, list):
        raise SourceError("mastodon: formato inesperado")
    signals = []
    for rank, tag in enumerate(tags, 1):
        history = tag.get("history") or []
        week = sum(int(day.get("uses", 0)) for day in history)
        today = int(history[0].get("uses", 0)) if history else 0
        signals.append(
            Signal(
                source="mastodon",
                market=None,
                term="#" + tag.get("name", ""),
                rank=rank,
                value=today,
                label=f"{today} usos hoje, {week} na semana",
                url=tag.get("url"),
            )
        )
    return signals


# Bluesky ----------------------------------------------------------------------
def bluesky_trends(http, limit: int = 25) -> list[Signal]:
    """Topics Bluesky lists as trending. English-heavy and without counts."""
    url = f"https://public.api.bsky.app/xrpc/app.bsky.unspecced.getTrendingTopics?limit={limit}"
    topics = http.get_json(url).get("topics", [])
    return [
        Signal(
            source="bluesky",
            market=None,
            term=topic.get("displayName") or topic.get("topic") or "",
            rank=rank,
            label=topic.get("description"),
            url="https://bsky.app" + quote(topic.get("link", "")),
        )
        for rank, topic in enumerate(topics, 1)
        if topic.get("displayName") or topic.get("topic")
    ]
