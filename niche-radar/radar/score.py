"""Pure calculations: trend shape, YouTube supply metrics and the opportunity index.

Every threshold here is a heuristic chosen by the author. None has been
validated against real channel outcomes, so read the components, not only
the final index.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import mean, median


def trend_stats(series: list[float]) -> dict | None:
    """Compare the last 7 days with the 28 before, and those with the 28 before that.

    `series` is daily values, oldest first. Returns None under 35 days of data.
    """
    if len(series) < 35:
        return None
    recent = mean(series[-7:])
    base = mean(series[-35:-7])
    older = mean(series[-63:-35]) if len(series) >= 63 else None
    short = recent / base if base > 0 else None
    long = base / older if older else None
    return {
        "recent_daily": round(recent, 4),
        "base_daily": round(base, 4),
        "ratio_short": round(short, 2) if short is not None else None,
        "ratio_long": round(long, 2) if long is not None else None,
        "label": trend_label(short, long),
    }


def trend_label(short: float | None, long: float | None) -> str:
    """Name the shape. A news spike is not the same as sustained growth."""
    if short is None:
        return "sem base"
    if long is not None and long >= 1.15 and short >= 0.95:
        return "alta sustentada"
    if short >= 2.0:
        return "pico recente"
    if short >= 1.2:
        return "subindo"
    if short < 0.85:
        return "em queda"
    return "estável"


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def source_momentum(short: float | None, long: float | None) -> float | None:
    """Growth of one source on a 0..1 scale.

    Month over month is worth up to 0.6 and saturates at +50%. The last week
    against its baseline is worth up to 0.4 and saturates at three times. A
    missing ratio adds nothing, so a spike with no history behind it cannot
    score above 0.4, and anything flat or falling scores 0.
    """
    if short is None and long is None:
        return None
    score = 0.0
    if long is not None:
        score += 0.6 * _clamp((long - 1.0) / 0.5)
    if short is not None:
        score += 0.4 * _clamp((short - 1.0) / 2.0)
    return score


def momentum(pairs: list[tuple[float | None, float | None]]) -> float | None:
    """Average momentum over the sources that had data; None when none did."""
    known = [m for m in (source_momentum(short, long) for short, long in pairs) if m is not None]
    return round(mean(known), 3) if known else None


def _age_days(created_at: str | None, now: datetime) -> float | None:
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created).total_seconds() / 86400


def youtube_metrics(videos: list[dict], channels: dict[str, dict], cfg, now: datetime) -> dict:
    """Describe who is getting the views among the videos a query returned.

    `videos` need video_id, channel_id, views, duration_s. `channels` maps
    channel_id to subs (None when hidden) and created_at.
    """
    if not videos:
        return {"n_videos": 0}
    total = sum(v["views"] for v in videos)
    by_channel: dict[str, int] = {}
    small_views = new_views = 0
    small_n = new_n = short_n = 0
    breakouts = []
    for video in videos:
        channel = channels.get(video["channel_id"], {})
        subs = channel.get("subs")
        age = _age_days(channel.get("created_at"), now)
        is_small = subs is not None and subs < cfg.small_subs
        is_new = age is not None and age < cfg.new_days
        by_channel[video["channel_id"]] = by_channel.get(video["channel_id"], 0) + video["views"]
        if video["duration_s"] <= 180:
            short_n += 1
        if is_small:
            small_n += 1
            small_views += video["views"]
        if is_new:
            new_n += 1
            new_views += video["views"]
        ratio = video["views"] / max(subs, 1) if subs is not None else None
        if (
            is_small
            and video["views"] >= cfg.breakout_views
            and ratio is not None
            and ratio >= cfg.breakout_ratio
        ):
            breakouts.append(video["video_id"])
    top3 = sum(sorted(by_channel.values(), reverse=True)[:3])
    known_subs = [c["subs"] for c in channels.values() if c.get("subs") is not None]
    count = len(videos)
    return {
        "n_videos": count,
        "n_channels": len(by_channel),
        "views_total": total,
        "views_median": int(median(v["views"] for v in videos)),
        "subs_median": int(median(known_subs)) if known_subs else None,
        "small_share": round(small_n / count, 3),
        "small_views_share": round(small_views / total, 3) if total else 0.0,
        "new_share": round(new_n / count, 3),
        "new_views_share": round(new_views / total, 3) if total else 0.0,
        "top3_views_share": round(top3 / total, 3) if total else 0.0,
        "short_share": round(short_n / count, 3),
        "breakouts": breakouts,
    }


def opportunity_index(metrics: dict, momentum_value: float | None, weights: dict) -> dict:
    """Weighted 0..100 index plus its components, so the ranking can be audited.

    demand: views of recent videos on a log scale (100 million saturates).
    openness: share of those views that went to small channels.
    breakouts: small-channel videos far above their size (10 saturates).
    momentum: growth outside YouTube; left out, with weights rescaled, when unknown.
    """
    if not metrics.get("n_videos"):
        return {"index": None, "components": {}}
    components = {
        "demand": min(1.0, math.log10(1 + metrics["views_total"]) / 8.0),
        "openness": metrics["small_views_share"],
        "breakouts": min(1.0, len(metrics["breakouts"]) / 10.0),
    }
    if momentum_value is not None:
        components["momentum"] = momentum_value
    weight_sum = sum(weights[name] for name in components)
    index = 100 * sum(weights[name] * value for name, value in components.items()) / weight_sum
    return {
        "index": round(index, 1),
        "components": {name: round(value, 3) for name, value in components.items()},
    }
