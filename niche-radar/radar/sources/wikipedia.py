"""Wikimedia pageviews: what people are reading, per language, day by day.

Wikimedia rate-limits clients that do not identify themselves. Set a contact
(e-mail or URL) in radar.toml or RADAR_CONTACT to get the higher tier.
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote, urlencode

from ..score import trend_stats
from . import Signal

_REST = "https://wikimedia.org/api/rest_v1/metrics/pageviews"

# Namespaces and housekeeping pages that are not subjects.
_SKIP_PREFIXES = (
    "special:", "especial:", "wikipedia:", "wikipédia:", "portal:", "file:", "ficheiro:",
    "archivo:", "help:", "ajuda:", "ayuda:", "category:", "categoria:", "categoría:",
    "template:", "predefinição:", "plantilla:", "user:", "usuário", "usuario:", "talk:",
    "anexo:", "list_of", "lista_de",
)
_SKIP_TITLES = {"main_page", "-", "404.php", "index.php", "undefined"}


def _is_subject(article: str) -> bool:
    lowered = article.lower()
    return lowered not in _SKIP_TITLES and not lowered.startswith(_SKIP_PREFIXES)


def top_articles(http, project: str, day: date) -> dict[str, dict]:
    """Most viewed articles of a project on a day: title -> {views, rank}."""
    url = f"{_REST}/top/{project}/all-access/{day:%Y/%m/%d}"
    data = http.get_json(url)
    articles = data["items"][0]["articles"]
    return {
        a["article"]: {"views": a["views"], "rank": a["rank"]}
        for a in articles
        if _is_subject(a["article"])
    }


def risers(http, market, day: date, limit: int = 40, min_ratio: float = 1.5) -> list[Signal]:
    """Articles in the day's top list that grew against the same weekday a week before."""
    current = top_articles(http, market.wiki, day)
    previous = top_articles(http, market.wiki, day - timedelta(days=7))
    signals = []
    for article, info in current.items():
        before = previous.get(article)
        ratio = info["views"] / before["views"] if before and before["views"] else None
        if ratio is not None and ratio < min_ratio:
            continue
        signals.append(
            Signal(
                source="wikipedia",
                market=market.key,
                term=article.replace("_", " "),
                rank=info["rank"],
                value=info["views"],
                label=f"x{ratio:.1f} em 7 dias" if ratio else "novo no top",
                url=f"https://{market.wiki}.org/wiki/{quote(article)}",
                extra={"day": day.isoformat(), "views_week_before": before["views"] if before else None},
            )
        )
    signals.sort(key=lambda s: s.value or 0, reverse=True)
    return signals[:limit]


def find_article(http, market, term: str) -> str | None:
    """Best-matching article title for a term, or None."""
    params = {"action": "query", "list": "search", "srsearch": term, "srlimit": 1, "format": "json"}
    data = http.get_json(f"https://{market.wiki}.org/w/api.php?" + urlencode(params))
    hits = data.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def article_trend(http, market, term: str, end: date, days: int = 63) -> dict | None:
    """Daily human pageviews of the article matching a term, with the trend shape."""
    title = find_article(http, market, term)
    if not title:
        return None
    start = end - timedelta(days=days - 1)
    url = (
        f"{_REST}/per-article/{market.wiki}/all-access/user/"
        f"{quote(title.replace(' ', '_'), safe='')}/daily/{start:%Y%m%d}/{end:%Y%m%d}"
    )
    items = http.get_json(url).get("items", [])
    series = [item["views"] for item in items]
    stats = trend_stats(series) or {}
    return {
        "article": title,
        "url": f"https://{market.wiki}.org/wiki/{quote(title.replace(' ', '_'))}",
        "days": len(series),
        "views_7d": sum(series[-7:]),
        **stats,
    }
