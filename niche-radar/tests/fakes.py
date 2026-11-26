"""Test doubles. Payloads are synthetic and shaped like each source's real response."""

from __future__ import annotations

import json

from radar.http import Http, SourceError


class FakeHttp(Http):
    """Routes a URL to a canned body by substring; first match wins."""

    def __init__(self, routes: dict):
        super().__init__(sleep=lambda seconds: None)
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, headers=None):
        self.calls.append((url, headers or {}))
        for fragment, body in self.routes.items():
            if fragment in url:
                if isinstance(body, Exception):
                    raise body
                if callable(body):
                    body = body(url)
                if isinstance(body, (dict, list)):
                    return json.dumps(body).encode()
                return body if isinstance(body, bytes) else str(body).encode()
        raise SourceError(f"sem rota para {url}", 404)


TRENDS_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>tema um</title><ht:approx_traffic>2K+</ht:approx_traffic>
<pubDate>Thu, 26 Nov 2026 09:40:00 -0800</pubDate>
<ht:news_item><ht:news_item_title>Manchete A</ht:news_item_title></ht:news_item>
<ht:news_item><ht:news_item_title>Manchete B</ht:news_item_title></ht:news_item></item>
<item><title>tema dois</title><ht:approx_traffic>200+</ht:approx_traffic></item>
<item><title></title><ht:approx_traffic>100+</ht:approx_traffic></item>
</channel></rss>"""


def news_rss(dates: list[str]) -> bytes:
    items = "".join(f"<item><title>n</title><pubDate>{d}</pubDate></item>" for d in dates)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'.encode()


def wiki_top(articles: dict[str, int]) -> dict:
    ranked = sorted(articles.items(), key=lambda pair: pair[1], reverse=True)
    return {"items": [{"articles": [
        {"article": name, "views": views, "rank": rank} for rank, (name, views) in enumerate(ranked, 1)
    ]}]}


def yt_video(video_id, channel_id, views, duration="PT10M", published="2026-11-20T00:00:00Z", title=None):
    return {
        "id": video_id,
        "snippet": {"title": title or f"video {video_id}", "channelId": channel_id,
                    "channelTitle": f"canal {channel_id}", "publishedAt": published, "categoryId": "27"},
        "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "2"},
        "contentDetails": {"duration": duration},
    }


def yt_channel(channel_id, subs, created="2026-06-01T00:00:00Z", hidden=False):
    stats = {"viewCount": "1000", "videoCount": "12", "hiddenSubscriberCount": hidden}
    if not hidden:
        stats["subscriberCount"] = str(subs)
    return {"id": channel_id, "snippet": {"title": f"canal {channel_id}", "publishedAt": created, "country": "BR"},
            "statistics": stats}
