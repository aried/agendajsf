"""The run: discover signals, pick candidates, cross-check them, measure YouTube."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

from .http import SourceError
from .score import momentum, opportunity_index, youtube_metrics
from .sources import community, google, manual, wikipedia
from .sources.youtube import QuotaExceeded
from .textutil import norm

# Wikimedia publishes a day's pageviews with a delay, so the run looks two days back.
WIKI_LAG_DAYS = 2
# After this many failures in a row a source rests for the cooldown, in seconds.
BREAKER_LIMIT = 2
BREAKER_COOLDOWN = 120.0
PAUSED = "fonte em pausa após falhas seguidas"
# Below this many news items in 30 days the weekly share is noise, not a trend.
MIN_NEWS_ITEMS = 10


class Breaker:
    """Pauses a source that keeps failing, so one outage cannot stall a run.

    After `limit` failures in a row the source rests for `cooldown` seconds.
    It is then tried again, and a single new failure sends it back to rest.
    """

    def __init__(self, limit: int = BREAKER_LIMIT, cooldown: float = BREAKER_COOLDOWN, clock=time.monotonic):
        self.limit = limit
        self.cooldown = cooldown
        self._clock = clock
        self.failures: dict[str, int] = {}
        self.paused_until: dict[str, float] = {}

    def open(self, source: str) -> bool:
        until = self.paused_until.get(source)
        return until is not None and self._clock() < until

    def record(self, source: str, ok: bool) -> None:
        if ok:
            self.failures[source] = 0
            self.paused_until.pop(source, None)
            return
        self.failures[source] = self.failures.get(source, 0) + 1
        if self.failures[source] >= self.limit:
            self.paused_until[source] = self._clock() + self.cooldown


def discover(http, store, cfg, run_id: int, today: date, breaker: Breaker | None = None) -> list:
    """Collect every discovery feed. A failing source is logged and skipped."""
    breaker = breaker or Breaker()
    collected = []

    def attempt(name, market_key, fetch):
        if breaker.open(name):
            store.log_source(run_id, name, market_key, False, 0, PAUSED)
            return
        try:
            signals = fetch()
        except SourceError as error:
            breaker.record(name, False)
            store.log_source(run_id, name, market_key, False, 0, str(error))
            return
        breaker.record(name, True)
        store.add_signals(run_id, signals)
        store.log_source(run_id, name, market_key, True, len(signals))
        collected.extend(signals)

    wiki_day = today - timedelta(days=WIKI_LAG_DAYS)
    for market in cfg.markets.values():
        attempt("google_trends", market.key, lambda m=market: google.trending_now(http, m))
        attempt("wikipedia", market.key, lambda m=market: wikipedia.risers(http, m, wiki_day))
    attempt("hackernews", None, lambda: community.hn_front_page(http))
    attempt("github", None, lambda: community.github_new_repos(http))
    attempt("mastodon", None, lambda: community.mastodon_trends(http))
    attempt("bluesky", None, lambda: community.bluesky_trends(http))
    attempt("manual", None, lambda: manual.load(cfg.manual_csv))
    return collected


def pick_candidates(cfg, signals: list, limit: int | None = None) -> list[dict]:
    """Seeds first, then the strongest search and reading spikes of each market."""
    candidates, seen = [], set()

    def add(market_key, term, origin):
        key = (market_key, norm(term))
        if not key[1] or key in seen:
            return False
        seen.add(key)
        candidates.append({"market": market_key, "term": term, "origin": origin})
        return True

    for seed in cfg.seeds:
        for market_key, term in seed["terms"].items():
            add(market_key, term, f"seed:{seed['id']}")
    for signal in signals:
        if signal.source.startswith("manual:") and signal.market in cfg.markets:
            add(signal.market, signal.term, signal.source)
    for market_key in cfg.markets:
        for source, count in (("google_trends", cfg.trends_per_market), ("wikipedia", cfg.wiki_per_market)):
            ranked = sorted(
                (s for s in signals if s.source == source and s.market == market_key),
                key=lambda s: s.value or 0,
                reverse=True,
            )
            added = 0
            for signal in ranked:
                if added >= count:
                    break
                # A term already listed must not use up one of the source's slots.
                added += add(market_key, signal.term, source)
    return candidates[:limit] if limit else candidates


def _wikipedia_trend(http, store, market, term: str, end: date) -> dict | None:
    """Article trend, remembering between runs which article a term resolved to.

    An empty remembered title means the term has no article of its own.
    """
    known = store.wiki_title(market.key, term)
    if known == "":
        return dict(wikipedia.NO_MATCH)
    result = wikipedia.article_trend(http, market, term, end, title=known)
    if result and known is None:
        store.save_wiki_title(market.key, term, result.get("article", ""))
    return result


def summarise(data: dict) -> dict:
    """Add the momentum and the trend shape derived from the lookups in `data`."""
    wiki = data.get("wikipedia") or {}
    news = data.get("news") or {}
    hn = data.get("hackernews") or {}
    # A capped or nearly empty news feed and a handful of Hacker News stories carry no trend.
    news_usable = not news.get("capped") and news.get("items_30d", 0) >= MIN_NEWS_ITEMS
    news_short = news.get("ratio_short") if news_usable else None
    hn_long = hn.get("ratio_long") if hn.get("stories_30d", 0) + hn.get("stories_prev_30d", 0) >= 10 else None
    data["momentum"] = momentum([
        (wiki.get("ratio_short"), wiki.get("ratio_long")),
        (news_short, None),
        (None, hn_long),
    ])
    data["shape"] = wiki.get("label") if wiki.get("matched") else None
    return data


def check_term(http, store, market, term: str, today: date, breaker: Breaker | None = None) -> dict:
    """Look a term up on every validation source and summarise its momentum."""
    breaker = breaker or Breaker()
    wiki_end = today - timedelta(days=WIKI_LAG_DAYS)
    lookups = {
        "suggest": lambda: {"suggestions": google.youtube_suggestions(http, market, term)},
        "news": lambda: google.news_coverage(http, market, term),
        "wikipedia": lambda: _wikipedia_trend(http, store, market, term, wiki_end),
        "hackernews": lambda: community.hn_trend(http, term),
    }
    data: dict = {}
    for name, fetch in lookups.items():
        if breaker.open(name):
            data[name] = {"error": PAUSED}
            continue
        try:
            data[name] = fetch()
            breaker.record(name, True)
        except SourceError as error:
            data[name] = {"error": str(error)}
            breaker.record(name, False)

    data["trending_days"] = store.days_seen(term, "google_trends")
    return summarise(data)


def _measure(yt, store, cfg, run_id, market, kind, query, videos, now, momentum_value=None) -> dict:
    channels = yt.channels([v["channel_id"] for v in videos])
    metrics = youtube_metrics(videos, channels, cfg.youtube, now)
    metrics.update(opportunity_index(metrics, momentum_value, cfg.weights))
    metrics["momentum"] = momentum_value
    store.add_measure(run_id, market.key, kind, query, metrics, videos, list(channels.values()))
    return metrics


def measure_query(yt, store, cfg, run_id, market, query: str, momentum_value=None, now=None) -> dict:
    """Who got the views on videos about `query` published inside the window."""
    now = now or datetime.now(timezone.utc)
    after = now - timedelta(days=cfg.youtube.window_days)
    ids, token = [], None
    for _ in range(cfg.youtube.pages):
        page, token = yt.search(market, query, after, page_token=token)
        ids.extend(page)
        if not token:
            break
    return _measure(yt, store, cfg, run_id, market, "query", query, yt.videos(ids), now, momentum_value)


def measure_scan(yt, store, cfg, run_id, market, now=None) -> dict:
    """Most viewed videos of the window in a market, with no query at all."""
    now = now or datetime.now(timezone.utc)
    ids, _ = yt.search(market, None, now - timedelta(days=cfg.youtube.window_days))
    return _measure(yt, store, cfg, run_id, market, "scan", None, yt.videos(ids), now)


def measure_popular(yt, store, cfg, run_id, market, now=None) -> dict:
    """YouTube's own most-popular chart for a market. Costs 2 units."""
    now = now or datetime.now(timezone.utc)
    return _measure(yt, store, cfg, run_id, market, "popular", None, yt.most_popular(market), now)


def run(http, store, cfg, yt=None, today: date | None = None, max_candidates: int | None = None,
        progress=print) -> dict:
    """Full run. Returns a summary that is also stored with the run."""
    today = today or datetime.now(timezone.utc).date()
    run_id = store.start_run("run")
    summary: dict = {"run_id": run_id, "youtube": "sem chave" if yt is None else "ok"}

    progress("1/3 coletando sinais das fontes abertas")
    breaker = Breaker()
    signals = discover(http, store, cfg, run_id, today, breaker)
    candidates = pick_candidates(cfg, signals, max_candidates)
    summary["signals"] = len(signals)
    summary["candidates"] = len(candidates)

    progress(f"2/3 validando {len(candidates)} candidatos entre as fontes")
    checks = []
    for candidate in candidates:
        market = cfg.markets[candidate["market"]]
        data = check_term(http, store, market, candidate["term"], today, breaker)
        store.add_check(run_id, market.key, candidate["term"], candidate["origin"], data)
        checks.append({**candidate, "momentum": data.get("momentum")})

    if yt is not None:
        progress("3/3 medindo oferta e demanda no YouTube")
        measured = 0
        try:
            for market in cfg.markets.values():
                measure_popular(yt, store, cfg, run_id, market)
                if cfg.youtube.scan:
                    measure_scan(yt, store, cfg, run_id, market)
            # Seeds are the user's own hypotheses, so they are measured first.
            ordered = sorted(
                checks,
                key=lambda c: (not c["origin"].startswith("seed:"), -(c["momentum"] or 0)),
            )
            for candidate in ordered[: cfg.youtube.max_queries_per_run]:
                measure_query(
                    yt, store, cfg, run_id, cfg.markets[candidate["market"]],
                    candidate["term"], candidate["momentum"],
                )
                measured += 1
        except QuotaExceeded as error:
            summary["youtube"] = str(error)
        except SourceError as error:
            summary["youtube"] = f"falhou: {error}"
            store.log_source(run_id, "youtube", None, False, measured, str(error))
        else:
            store.log_source(run_id, "youtube", None, True, measured)
        summary["youtube_queries"] = measured
        summary["youtube_units"] = yt.spent
        store.purge_youtube(cfg.youtube.retention_days)
    else:
        progress("3/3 YouTube pulado: defina YOUTUBE_API_KEY para medir oferta e demanda")

    summary["http_requests"] = dict(http.requests)
    store.finish_run(run_id, summary)
    return summary
