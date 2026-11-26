import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from radar import config, pipeline, report
from radar.config import YouTubeCfg
from radar.http import SourceError
from radar.sources import community, google, manual, wikipedia
from radar.sources.youtube import QuotaExceeded, YouTube
from radar.store import Store

from tests.fakes import FakeHttp, TRENDS_RSS, news_rss, wiki_top, yt_channel, yt_video

NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
TODAY = NOW.date()
CFG = config.load(None) if not Path("radar.toml").exists() else None
BR = config._markets({})["br"]


class GoogleTests(unittest.TestCase):
    def test_trending_now(self):
        signals = google.trending_now(FakeHttp({"trending/rss?geo=BR": TRENDS_RSS}), BR)
        self.assertEqual([s.term for s in signals], ["tema um", "tema dois"])
        self.assertEqual(signals[0].value, 2000)
        self.assertEqual(signals[0].label, "2K+")
        self.assertEqual(signals[0].extra["news"], ["Manchete A", "Manchete B"])
        self.assertEqual(signals[1].rank, 2)

    def test_bad_xml_is_a_source_error(self):
        with self.assertRaises(SourceError):
            google.trending_now(FakeHttp({"trending/rss": b"<html>"}), BR)

    def test_news_coverage_splits_last_week(self):
        dates = [format_datetime(NOW - timedelta(days=d)) for d in (1, 2, 3, 10, 20, 25)]
        http = FakeHttp({"news.google.com": news_rss(dates + ["not a date"])})
        result = google.news_coverage(http, BR, "termo", NOW)
        self.assertEqual(result["items_30d"], 6)
        self.assertEqual(result["items_7d"], 3)
        self.assertFalse(result["capped"])
        self.assertEqual(result["ratio_short"], round((3 / 7) / (3 / 23), 2))
        self.assertIn("%22termo%22+when%3A30d", http.calls[0][0])

    def test_news_coverage_without_older_items(self):
        http = FakeHttp({"news.google.com": news_rss([format_datetime(NOW)])})
        self.assertIsNone(google.news_coverage(http, BR, "t", NOW)["ratio_short"])

    def test_youtube_suggestions(self):
        http = FakeHttp({"suggestqueries": '["q",["q um","q dois"],[],{}]'})
        self.assertEqual(google.youtube_suggestions(http, BR, "q"), ["q um", "q dois"])
        self.assertIn("ds=yt", http.calls[0][0])
        self.assertIn("gl=BR", http.calls[0][0])

    def test_expand_suggestions_dedupes_and_keeps_order(self):
        def answer(url):
            return '["q",["tema base","tema b extra"],[],{}]' if "q=tema+b" in url else '["q",["tema base","tema raiz"],[],{}]'

        http = FakeHttp({"suggestqueries": answer})
        found = google.expand_suggestions(http, BR, "tema", suffixes="ab")
        self.assertEqual(found, ["tema base", "tema raiz", "tema b extra"])
        self.assertEqual(len(http.calls), 3)

    def test_youtube_suggestions_bad_shape(self):
        with self.assertRaises(SourceError):
            google.youtube_suggestions(FakeHttp({"suggestqueries": "{}"}), BR, "q")


class WikipediaTests(unittest.TestCase):
    def test_risers_keeps_growth_and_newcomers_only(self):
        day = date(2026, 11, 24)
        http = FakeHttp({
            "/2026/11/24": wiki_top({"Tema_A": 9000, "Tema_B": 5000, "Tema_C": 4000,
                                     "Especial:Pesquisar": 99999, "Main_Page": 88888}),
            "/2026/11/17": wiki_top({"Tema_A": 8000, "Tema_B": 1000}),
        })
        signals = wikipedia.risers(http, BR, day)
        self.assertEqual([s.term for s in signals], ["Tema B", "Tema C"])
        self.assertEqual(signals[0].label, "x5.0 em 7 dias")
        self.assertEqual(signals[1].label, "novo no top")
        self.assertIn("pt.wikipedia/all-access/2026/11/24", http.calls[0][0])

    def test_article_trend(self):
        series = [100] * 28 + [150] * 28 + [160] * 7
        http = FakeHttp({
            "w/api.php": {"query": {"search": [{"title": "Tema Á"}]}},
            "per-article": {"items": [{"views": v} for v in series]},
        })
        result = wikipedia.article_trend(http, BR, "tema", date(2026, 11, 24))
        self.assertEqual(result["article"], "Tema Á")
        self.assertEqual(result["views_7d"], 1120)
        self.assertEqual(result["label"], "alta sustentada")
        self.assertIn("Tema_%C3%81/daily/20260923/20261124", http.calls[1][0])

    def test_article_trend_without_match(self):
        http = FakeHttp({"w/api.php": {"query": {"search": []}}})
        self.assertIsNone(wikipedia.article_trend(http, BR, "nada", TODAY))


class CommunityTests(unittest.TestCase):
    def test_hn_front_page(self):
        http = FakeHttp({"tags=front_page": {"hits": [
            {"title": "Show HN: x", "points": 120, "num_comments": 30, "objectID": "1"},
            {"title": None, "points": 5, "objectID": "2"},
        ]}})
        signals = community.hn_front_page(http)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].value, 120)
        self.assertTrue(signals[0].url.endswith("id=1"))

    def test_hn_trend(self):
        counts = iter([40, 20])
        http = FakeHttp({"search_by_date": lambda url: {"nbHits": next(counts)}})
        self.assertEqual(
            community.hn_trend(http, "x", NOW),
            {"stories_30d": 40, "stories_prev_30d": 20, "ratio_long": 2.0},
        )

    def test_hn_trend_without_history(self):
        http = FakeHttp({"search_by_date": {"nbHits": 0}})
        self.assertIsNone(community.hn_trend(http, "x", NOW)["ratio_long"])

    def test_github(self):
        http = FakeHttp({"api.github.com": {"items": [
            {"full_name": "a/b", "stargazers_count": 900, "html_url": "https://github.com/a/b",
             "description": "d", "language": "Rust"}]}})
        signals = community.github_new_repos(http, now=NOW)
        self.assertEqual(signals[0].term, "a/b")
        self.assertEqual(signals[0].extra["language"], "Rust")
        self.assertIn("created%3A%3E2026-10-27", http.calls[0][0])

    def test_mastodon(self):
        http = FakeHttp({"mastodon.social": [
            {"name": "tema", "url": "https://mastodon.social/tags/tema",
             "history": [{"uses": "5"}, {"uses": "3"}]}]})
        signal = community.mastodon_trends(http)[0]
        self.assertEqual((signal.term, signal.value, signal.label), ("#tema", 5, "5 usos hoje, 8 na semana"))

    def test_mastodon_error_payload(self):
        with self.assertRaises(SourceError):
            community.mastodon_trends(FakeHttp({"mastodon.social": {"error": "x"}}))

    def test_bluesky(self):
        http = FakeHttp({"bsky.app": {"topics": [
            {"displayName": "Assunto", "description": "resumo", "link": "/profile/x/feed/y"}, {}]}})
        signals = community.bluesky_trends(http)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].label, "resumo")

    def test_manual_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "manual_signals.csv"
            self.assertEqual(manual.load(path), [])
            path.write_text(
                "date,source,market,term,note\n2026-11-26,tiktok,br,termo x,content gap\n,,,,\n",
                encoding="utf-8",
            )
            signals = manual.load(path)
        self.assertEqual(len(signals), 1)
        self.assertEqual((signals[0].source, signals[0].market, signals[0].label),
                         ("manual:tiktok", "br", "content gap"))


def youtube_routes(videos, channels, next_token=None):
    def search(url):
        return {"items": [{"id": {"videoId": v["id"]}} for v in videos],
                **({"nextPageToken": next_token} if next_token and "pageToken" not in url else {})}

    return {
        "youtube/v3/search": search,
        "youtube/v3/videos": {"items": videos},
        "youtube/v3/channels": {"items": channels},
    }


class YouTubeTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.cfg = YouTubeCfg()
        self.videos = [yt_video("v1", "c1", 80_000), yt_video("v2", "c2", 500_000, "PT30S")]
        self.channels = [yt_channel("c1", 2_000), yt_channel("c2", 0, hidden=True)]
        self.http = FakeHttp(youtube_routes(self.videos, self.channels))
        self.yt = YouTube(self.http, "secret-key", self.store, self.cfg)

    def test_search_costs_100_and_key_travels_in_header(self):
        ids, token = self.yt.search(BR, "tema", NOW - timedelta(days=30))
        self.assertEqual(ids, ["v1", "v2"])
        self.assertIsNone(token)
        self.assertEqual(self.yt.spent, 100)
        self.assertEqual(self.store.quota_used(YouTube.quota_day()), 100)
        url, headers = self.http.calls[0]
        self.assertNotIn("secret-key", url)
        self.assertEqual(headers["X-goog-api-key"], "secret-key")
        self.assertIn("regionCode=BR", url)
        self.assertIn("relevanceLanguage=pt", url)
        self.assertIn("publishedAfter=2026-10-27T12%3A00%3A00Z", url)

    def test_query_less_search_sends_no_q(self):
        self.yt.search(BR, None, NOW)
        self.assertNotIn("&q=", self.http.calls[0][0])

    def test_videos_and_channels_parse(self):
        videos = self.yt.videos(["v1", "v2", "v1"])
        self.assertEqual(videos[0]["views"], 80_000)
        self.assertEqual(videos[1]["duration_s"], 30)
        channels = self.yt.channels(["c1", "c2", ""])
        self.assertEqual(channels["c1"]["subs"], 2_000)
        self.assertIsNone(channels["c2"]["subs"])
        self.assertEqual(self.yt.spent, 2)

    def test_ids_are_sent_in_batches_of_50(self):
        self.yt.videos([f"id{i}" for i in range(120)])
        self.assertEqual(len(self.http.calls), 3)
        self.assertEqual(self.yt.spent, 3)

    def test_refuses_before_sending_when_budget_is_gone(self):
        self.store.add_quota(YouTube.quota_day(), self.cfg.daily_quota - self.cfg.reserve - 50)
        with self.assertRaises(QuotaExceeded):
            self.yt.search(BR, "tema", NOW)
        self.assertEqual(self.http.calls, [])
        self.yt.videos(["v1"])  # one unit still fits
        self.assertEqual(len(self.http.calls), 1)

    def test_failed_call_is_still_charged(self):
        yt = YouTube(FakeHttp({"youtube/v3/search": SourceError("HTTP 403", 403)}), "k", self.store, self.cfg)
        with self.assertRaises(SourceError):
            yt.search(BR, "tema", NOW)
        self.assertEqual(self.store.quota_used(YouTube.quota_day()), 100)

    def test_measure_query_follows_pages_and_stores(self):
        cfg = config.load(None) if CFG is None else CFG
        cfg.youtube.pages = 2
        http = FakeHttp(youtube_routes(self.videos, self.channels, next_token="p2"))
        yt = YouTube(http, "k", self.store, cfg.youtube)
        run_id = self.store.start_run("test")
        metrics = pipeline.measure_query(yt, self.store, cfg, run_id, BR, "tema", 0.5, NOW)
        self.assertEqual(sum("v3/search" in url for url, _ in http.calls), 2)
        self.assertEqual(metrics["n_videos"], 2)
        self.assertEqual(metrics["breakouts"], ["v1"])
        self.assertEqual(metrics["momentum"], 0.5)
        self.assertIn("momentum", metrics["components"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM yt_videos").fetchone()[0], 2)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM yt_channels").fetchone()[0], 2)
        cfg.youtube.pages = 1


class PipelineTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "radar.toml"
        path.write_text(
            '[general]\ndata_dir = "%s"\ntrends_per_market = 1\nwiki_per_market = 1\n'
            '[markets.us]\nenabled = false\n[markets.mx]\nenabled = false\n'
            '[[seeds]]\nid = "minha"\nbr = "tema semente"\n' % folder.name,
            encoding="utf-8",
        )
        self.cfg = config.load(path)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        series = [100] * 28 + [150] * 28 + [160] * 7
        self.routes = {
            "trending/rss": TRENDS_RSS,
            "/metrics/pageviews/top/pt.wikipedia/all-access/2026/11/24": wiki_top({"Tema_Wiki": 9000}),
            "/metrics/pageviews/top/pt.wikipedia/all-access/2026/11/17": wiki_top({"Outro": 10}),
            "tags=front_page": {"hits": [{"title": "HN story", "points": 10, "objectID": "1"}]},
            "api.github.com": SourceError("api.github.com: HTTP 403: rate limit", 403),
            "mastodon.social": [],
            "bsky.app": {"topics": []},
            "suggestqueries": '["q",["q a","q b"],[],{}]',
            "news.google.com": news_rss([format_datetime(datetime.now(timezone.utc))]),
            "w/api.php": {"query": {"search": [{"title": "Tema"}]}},
            "per-article": {"items": [{"views": v} for v in series]},
            "search_by_date": {"nbHits": 0},
        }
        videos = [yt_video("v1", "c1", 80_000, title="Tema semente explicado")]
        self.routes.update(youtube_routes(videos, [yt_channel("c1", 2_000)]))

    def test_candidates_put_seeds_first_and_dedupe(self):
        signals = google.trending_now(FakeHttp(self.routes), BR)
        signals.append(type(signals[0])("google_trends", "br", "TEMA SEMENTE", value=999_999))
        picked = pipeline.pick_candidates(self.cfg, signals)
        self.assertEqual(picked[0], {"market": "br", "term": "tema semente", "origin": "seed:minha"})
        self.assertEqual([c["term"] for c in picked], ["tema semente", "tema um"])
        self.assertEqual(len(pipeline.pick_candidates(self.cfg, signals, limit=1)), 1)

    def test_breaker_pauses_a_failing_source(self):
        routes = {**self.routes, "news.google.com": SourceError("news: HTTP 503", 503)}
        http = FakeHttp(routes)
        breaker = pipeline.Breaker()
        for _ in range(2):
            pipeline.check_term(http, self.store, BR, "x", TODAY, breaker)
        before = sum("news.google.com" in url for url, _ in http.calls)
        result = pipeline.check_term(http, self.store, BR, "x", TODAY, breaker)
        self.assertEqual(sum("news.google.com" in url for url, _ in http.calls), before)
        self.assertIn("pausada", result["news"]["error"])
        self.assertIn("suggestions", result["suggest"])

    def test_discovery_and_checks_share_the_breaker(self):
        limited = SourceError("wikimedia.org: HTTP 429", 429)
        routes = {k: v for k, v in self.routes.items() if "pageviews" not in k and k != "per-article"}
        routes.update({"/metrics/pageviews/": limited, "w/api.php": limited})
        http = FakeHttp(routes)
        pipeline.run(http, self.store, self.cfg, None, date(2026, 11, 26), progress=lambda m: None)
        # The top list fails in discovery, the first check fails too, and the
        # second candidate is no longer sent to Wikipedia at all.
        self.assertEqual(sum("wiki" in url for url, _ in http.calls), 2)
        checks = [row["data"] for row in self.store.db.execute("SELECT data FROM checks ORDER BY id")]
        self.assertEqual(len(checks), 2)
        self.assertIn("HTTP 429", checks[0])
        self.assertIn("pausada", checks[1])

    def test_full_run_without_youtube(self):
        http = FakeHttp(self.routes)
        messages = []
        summary = pipeline.run(http, self.store, self.cfg, None, date(2026, 11, 26), progress=messages.append)
        self.assertEqual(summary["youtube"], "sem chave")
        self.assertEqual(summary["candidates"], 3)
        log = {row["source"]: row["ok"] for row in self.store.db.execute("SELECT * FROM source_log")}
        self.assertEqual(log["github"], 0)
        self.assertEqual(log["google_trends"], 1)
        check = self.store.db.execute("SELECT data FROM checks WHERE origin = 'seed:minha'").fetchone()
        self.assertIn("alta sustentada", check["data"])
        html = report.build(self.store, self.cfg)
        self.assertIn("não mediu o YouTube", html)
        self.assertIn("tema semente", html)
        self.assertIn("falhou", html)

    def test_full_run_with_youtube(self):
        http = FakeHttp(self.routes)
        yt = YouTube(http, "k", self.store, self.cfg.youtube)
        summary = pipeline.run(http, self.store, self.cfg, yt, date(2026, 11, 26), progress=lambda m: None)
        self.assertEqual(summary["youtube"], "ok")
        self.assertEqual(summary["youtube_queries"], 3)
        # popular (1 + 1) + scan (100 + 1 + 1) + three queries (102 each)
        self.assertEqual(summary["youtube_units"], 2 + 102 + 3 * 102)
        kinds = [row[0] for row in self.store.db.execute("SELECT kind FROM yt_measures ORDER BY id")]
        self.assertEqual(kinds, ["popular", "scan", "query", "query", "query"])
        first_query = self.store.db.execute(
            "SELECT query FROM yt_measures WHERE kind = 'query' ORDER BY id LIMIT 1").fetchone()[0]
        self.assertEqual(first_query, "tema semente")
        html = report.build(self.store, self.cfg)
        self.assertIn("Tema semente explicado", html)
        self.assertIn("youtube.com/watch?v=v1", html)

    def test_run_stops_cleanly_when_quota_runs_out(self):
        self.store.add_quota(YouTube.quota_day(), self.cfg.youtube.daily_quota - self.cfg.youtube.reserve - 150)
        http = FakeHttp(self.routes)
        yt = YouTube(http, "k", self.store, self.cfg.youtube)
        summary = pipeline.run(http, self.store, self.cfg, yt, date(2026, 11, 26), progress=lambda m: None)
        self.assertIn("esgotado", summary["youtube"])
        self.assertEqual(summary["youtube_queries"], 0)
        self.assertIsNotNone(self.store.latest_run()["finished_at"])


if __name__ == "__main__":
    unittest.main()
