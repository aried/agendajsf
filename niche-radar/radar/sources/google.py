"""Google's public feeds: Trends "Trending now" RSS, News search RSS and YouTube autocomplete.

The two RSS feeds are published by Google. The autocomplete endpoint is the one
browsers use for search suggestions; it is not a documented API and returns no
search volume.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from ..http import SourceError
from ..textutil import parse_count
from . import Signal

_HT = {"ht": "https://trends.google.com/trending/rss"}


def _parse_xml(body: bytes, what: str) -> ET.Element:
    try:
        return ET.fromstring(body)
    except ET.ParseError as error:
        raise SourceError(f"{what}: XML inválido ({error})") from None


def trending_now(http, market) -> list[Signal]:
    """Searches spiking right now in a country. Mostly news, sports and celebrities."""
    url = f"https://trends.google.com/trending/rss?geo={market.geo}"
    root = _parse_xml(http.get(url), "google trends")
    signals = []
    for rank, item in enumerate(root.iter("item"), 1):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        traffic = (item.findtext("ht:approx_traffic", default="", namespaces=_HT) or "").strip()
        news = [
            (n.findtext("ht:news_item_title", default="", namespaces=_HT) or "").strip()
            for n in item.findall("ht:news_item", _HT)
        ]
        signals.append(
            Signal(
                source="google_trends",
                market=market.key,
                term=title,
                rank=rank,
                value=parse_count(traffic),
                label=traffic or None,
                url=f"https://trends.google.com/trends/explore?geo={market.geo}&"
                + urlencode({"q": title}),
                extra={"news": [n for n in news if n][:3], "published": item.findtext("pubDate")},
            )
        )
    return signals


def news_coverage(http, market, term: str, now: datetime | None = None) -> dict:
    """Count news items about a term in the last 30 days and how many fall in the last 7.

    The feed returns at most about 100 items, so a capped result only says "a lot".
    """
    now = now or datetime.now(timezone.utc)
    params = {
        "q": f'"{term}" when:30d',
        "hl": market.news_hl,
        "gl": market.geo,
        "ceid": market.news_ceid,
    }
    root = _parse_xml(http.get("https://news.google.com/rss/search?" + urlencode(params)), "google news")
    dates = []
    for item in root.iter("item"):
        try:
            dates.append(parsedate_to_datetime(item.findtext("pubDate") or ""))
        except (TypeError, ValueError):
            continue
    week_ago = now - timedelta(days=7)
    last7 = sum(1 for d in dates if d >= week_ago)
    older = len(dates) - last7
    ratio = (last7 / 7) / (older / 23) if older else None
    return {
        "items_30d": len(dates),
        "items_7d": last7,
        "capped": len(dates) >= 100,
        "ratio_short": round(ratio, 2) if ratio is not None else None,
    }


def youtube_suggestions(http, market, query: str) -> list[str]:
    """What YouTube's search box suggests for a query, in YouTube's own order."""
    params = {"client": "firefox", "ds": "yt", "hl": market.lang, "gl": market.geo, "q": query}
    body = http.get("https://suggestqueries.google.com/complete/search?" + urlencode(params))
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("latin-1")
    try:
        data = json.loads(text)
        return [str(s) for s in data[1]]
    except (json.JSONDecodeError, LookupError, TypeError):
        raise SourceError("youtube suggest: formato inesperado") from None


def expand_suggestions(http, market, term: str, suffixes: str = "abcdefghijklmnopqrstuvwxyz") -> list[str]:
    """Suggestions for a term and for the term followed by each letter, without repeats.

    This is how keyword tools build long-tail lists. It shows what people type,
    in YouTube's order, and says nothing about how often.
    """
    found: dict[str, None] = {}
    for query in [term] + [f"{term} {letter}" for letter in suffixes]:
        for suggestion in youtube_suggestions(http, market, query):
            found.setdefault(suggestion, None)
    return list(found)
