import io
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar import config
from radar.config import YouTubeCfg
from radar.http import Http, SourceError
from radar.score import (
    momentum, opportunity_index, source_momentum, trend_label, trend_stats, youtube_metrics,
)
from radar.sources import Signal
from radar.store import Store
from radar.textutil import iso_duration_seconds, norm, parse_count, recurring_terms

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)


class TextTests(unittest.TestCase):
    def test_norm_strips_accents_and_punctuation(self):
        self.assertEqual(norm("  Inteligência  Artificial! "), "inteligencia artificial")

    def test_parse_count(self):
        self.assertEqual(parse_count("200+"), 200)
        self.assertEqual(parse_count("2K+"), 2000)
        self.assertEqual(parse_count("1.5M+"), 1_500_000)
        self.assertIsNone(parse_count(""))
        self.assertIsNone(parse_count("muitos"))

    def test_iso_duration(self):
        self.assertEqual(iso_duration_seconds("PT1H2M3S"), 3723)
        self.assertEqual(iso_duration_seconds("PT45S"), 45)
        self.assertEqual(iso_duration_seconds("P1DT1S"), 86401)
        self.assertEqual(iso_duration_seconds("P0D"), 0)
        self.assertEqual(iso_duration_seconds("lixo"), 0)

    def test_recurring_terms_counts_each_title_once(self):
        terms = dict(recurring_terms(["Gato gato gato dorme", "O gato dorme muito", "Cachorro late"]))
        self.assertEqual(terms["gato"], 2)
        self.assertEqual(terms["gato dorme"], 2)
        self.assertNotIn("cachorro", terms)
        self.assertNotIn("muito", terms)


class TrendTests(unittest.TestCase):
    def test_needs_five_weeks(self):
        self.assertIsNone(trend_stats([1.0] * 34))

    def test_sustained_growth(self):
        stats = trend_stats([100.0] * 28 + [130.0] * 28 + [140.0] * 7)
        self.assertEqual(stats["ratio_long"], 1.3)
        self.assertEqual(stats["label"], "alta sustentada")

    def test_spike_is_not_sustained(self):
        stats = trend_stats([100.0] * 56 + [400.0] * 7)
        self.assertEqual(stats["ratio_long"], 1.0)
        self.assertEqual(stats["ratio_short"], 4.0)
        self.assertEqual(stats["label"], "pico recente")

    def test_labels(self):
        self.assertEqual(trend_label(0.5, 1.1), "em queda")
        self.assertEqual(trend_label(1.3, None), "subindo")
        self.assertEqual(trend_label(1.0, 1.0), "estável")
        self.assertEqual(trend_label(0.97, 1.24), "alta sustentada")
        self.assertEqual(trend_label(None, None), "sem base")

    def test_zero_baseline_has_no_ratio(self):
        stats = trend_stats([0.0] * 56 + [5.0] * 7)
        self.assertIsNone(stats["ratio_short"])
        self.assertEqual(stats["label"], "sem base")

    def test_momentum_rewards_lasting_growth_over_a_spike(self):
        lasting = source_momentum(1.2, 1.5)
        spike = source_momentum(4.0, 1.0)
        self.assertGreater(lasting, spike)
        self.assertEqual(source_momentum(0.5, 0.9), 0.0)
        self.assertIsNone(source_momentum(None, None))

    def test_momentum_ignores_sources_without_data(self):
        self.assertIsNone(momentum([(None, None), (None, None)]))
        self.assertEqual(momentum([(None, 1.5), (None, None)]), 0.6)

    def test_a_spike_without_history_is_capped(self):
        self.assertEqual(source_momentum(50.0, None), 0.4)
        self.assertEqual(source_momentum(3.0, 1.5), 1.0)


class YouTubeMetricTests(unittest.TestCase):
    def setUp(self):
        self.cfg = YouTubeCfg()
        self.channels = {
            "small": {"subs": 1_000, "created_at": "2026-08-01T00:00:00Z"},
            "big": {"subs": 2_000_000, "created_at": "2015-01-01T00:00:00Z"},
            "hidden": {"subs": None, "created_at": "2026-10-01T00:00:00Z"},
        }
        self.videos = [
            {"video_id": "a", "channel_id": "small", "views": 100_000, "duration_s": 600},
            {"video_id": "b", "channel_id": "big", "views": 300_000, "duration_s": 60},
            {"video_id": "c", "channel_id": "hidden", "views": 100_000, "duration_s": 900},
            {"video_id": "d", "channel_id": "small", "views": 1_000, "duration_s": 600},
        ]

    def test_metrics(self):
        m = youtube_metrics(self.videos, self.channels, self.cfg, NOW)
        self.assertEqual(m["n_videos"], 4)
        self.assertEqual(m["n_channels"], 3)
        self.assertEqual(m["views_total"], 501_000)
        self.assertEqual(m["small_share"], 0.5)
        self.assertEqual(m["small_views_share"], round(101_000 / 501_000, 3))
        # The hidden-subscriber channel is new but cannot be called small.
        self.assertEqual(m["new_share"], 0.75)
        self.assertEqual(m["short_share"], 0.25)
        self.assertEqual(m["breakouts"], ["a"])
        self.assertEqual(m["top3_views_share"], 1.0)

    def test_empty(self):
        self.assertEqual(youtube_metrics([], {}, self.cfg, NOW), {"n_videos": 0})
        self.assertIsNone(opportunity_index({"n_videos": 0}, None, config.DEFAULT_WEIGHTS)["index"])

    def test_index_rescales_weights_without_momentum(self):
        m = youtube_metrics(self.videos, self.channels, self.cfg, NOW)
        without = opportunity_index(m, None, config.DEFAULT_WEIGHTS)
        with_zero = opportunity_index(m, 0.0, config.DEFAULT_WEIGHTS)
        with_full = opportunity_index(m, 1.0, config.DEFAULT_WEIGHTS)
        self.assertNotIn("momentum", without["components"])
        self.assertLess(with_zero["index"], without["index"])
        self.assertGreater(with_full["index"], without["index"])
        self.assertTrue(0 <= without["index"] <= 100)


def _http_error(code, headers=None, body=b"nope"):
    return urllib.error.HTTPError("https://x.test/", code, "err", headers or {}, io.BytesIO(body))


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class HttpTests(unittest.TestCase):
    def make(self, outcomes, **kwargs):
        self.sleeps = []
        self.requests = []
        queue = list(outcomes)

        def opener(request, timeout):
            self.requests.append(request)
            outcome = queue.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return _Response(outcome)

        return Http(sleep=self.sleeps.append, clock=lambda: 0.0, opener=opener, **kwargs)

    def test_retries_429_and_honours_retry_after(self):
        http = self.make([_http_error(429, {"Retry-After": "7"}), b"ok"])
        self.assertEqual(http.get("https://x.test/a"), b"ok")
        self.assertIn(8.0, self.sleeps)

    def test_rate_limit_is_retried_only_once(self):
        http = self.make([_http_error(429, {"Retry-After": "1"})] * 3 + [b"ok"], max_retries=5)
        with self.assertRaises(SourceError):
            http.get("https://x.test/a")
        self.assertEqual(len(self.requests), 2)

    def test_gives_up_when_asked_to_wait_too_long(self):
        http = self.make([_http_error(429, {"Retry-After": "3600"}), b"ok"])
        with self.assertRaises(SourceError) as caught:
            http.get("https://x.test/a")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(len(self.requests), 1)

    def test_does_not_retry_client_errors(self):
        http = self.make([_http_error(403), b"ok"])
        with self.assertRaises(SourceError) as caught:
            http.get("https://x.test/a")
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(len(self.requests), 1)

    def test_stops_after_max_retries(self):
        http = self.make([_http_error(503)] * 3, max_retries=2)
        with self.assertRaises(SourceError):
            http.get("https://x.test/a")
        self.assertEqual(len(self.requests), 3)

    def test_network_errors_are_retried(self):
        http = self.make([ConnectionResetError("reset"), b"ok"])
        self.assertEqual(http.get("https://x.test/a"), b"ok")

    def test_paces_calls_to_the_same_host(self):
        http = self.make([b"1", b"2"])
        http.get("https://x.test/a")
        http.get("https://x.test/b")
        self.assertIn(0.5, self.sleeps)

    def test_contact_goes_into_user_agent(self):
        http = self.make([b"ok"], contact="https://example.org/me")
        http.get("https://x.test/a")
        self.assertIn("https://example.org/me", self.requests[0].get_header("User-agent"))

    def test_non_json_body_is_a_source_error(self):
        http = self.make([b"<html>limit</html>"])
        with self.assertRaises(SourceError):
            http.get_json("https://x.test/a")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.run = self.store.start_run("test")

    def test_days_seen_matches_normalised_terms(self):
        self.store.add_signals(self.run, [Signal("google_trends", "br", "Tema Çom Acento")])
        self.assertEqual(self.store.days_seen("tema com acento", "google_trends"), 1)
        self.assertEqual(self.store.days_seen("tema com acento", "wikipedia"), 0)

    def test_quota_accumulates_per_day(self):
        self.store.add_quota("2026-11-26", 100)
        self.store.add_quota("2026-11-26", 2)
        self.assertEqual(self.store.quota_used("2026-11-26"), 102)
        self.assertEqual(self.store.quota_used("2026-11-27"), 0)

    def test_purge_removes_only_old_youtube_rows(self):
        video = {"video_id": "v", "title": "t", "channel_id": "c", "published_at": None,
                 "duration_s": 1, "views": 1, "likes": 0, "comments": 0}
        self.store.add_measure(self.run, "br", "query", "q", {"n_videos": 1}, [video], [])
        old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.store.db.execute(
            "INSERT INTO yt_videos(video_id, collected_at) VALUES('old', ?)", (old,)
        )
        self.assertEqual(self.store.purge_youtube(30), 1)
        left = [row[0] for row in self.store.db.execute("SELECT video_id FROM yt_videos")]
        self.assertEqual(left, ["v"])


class ConfigTests(unittest.TestCase):
    def write(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "radar.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_defaults_without_file(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = config.load(None) if not Path("radar.toml").exists() else config.load(self.write(""))
        self.assertEqual(set(cfg.markets), {"br", "us", "mx"})
        self.assertEqual(cfg.youtube.daily_quota, 10_000)

    def test_seeds_markets_and_overrides(self):
        cfg = config.load(self.write(
            '[markets.mx]\nenabled = false\n'
            '[youtube]\nsmall_subs = 5000\n'
            '[weights]\nmomentum = 0.5\n'
            '[[seeds]]\nid = "a"\nbr = "termo"\nus = "term"\nmx = "termino"\n'
            '[[seeds]]\nall = "global"\n'
        ))
        self.assertEqual(set(cfg.markets), {"br", "us"})
        self.assertEqual(cfg.youtube.small_subs, 5000)
        self.assertEqual(cfg.weights["momentum"], 0.5)
        self.assertEqual(cfg.weights["demand"], 0.35)
        self.assertEqual(cfg.seeds[0], {"id": "a", "terms": {"br": "termo", "us": "term"}})
        self.assertEqual(cfg.seeds[1], {"id": "global", "terms": {"br": "global", "us": "global"}})

    def test_unknown_youtube_key_is_rejected(self):
        with self.assertRaises(ValueError):
            config.load(self.write("[youtube]\nwindow = 3\n"))

    def test_missing_explicit_file(self):
        with self.assertRaises(FileNotFoundError):
            config.load("/nonexistent/radar.toml")


if __name__ == "__main__":
    unittest.main()
