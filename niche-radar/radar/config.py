"""Configuration: markets, YouTube budget, seeds and score weights."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass(frozen=True)
class Market:
    key: str
    geo: str  # ISO 3166-1 alpha-2, used by Google and YouTube
    lang: str  # ISO 639-1
    wiki: str  # Wikimedia project, e.g. "pt.wikipedia"
    news_hl: str
    news_ceid: str


DEFAULT_MARKETS = {
    "br": dict(geo="BR", lang="pt", wiki="pt.wikipedia", news_hl="pt-BR",
               news_ceid="BR:pt-419"),
    "us": dict(geo="US", lang="en", wiki="en.wikipedia", news_hl="en-US",
               news_ceid="US:en"),
    "mx": dict(geo="MX", lang="es", wiki="es.wikipedia", news_hl="es-419",
               news_ceid="MX:es-419"),
}


@dataclass
class YouTubeCfg:
    window_days: int = 30  # how far back a video may have been published
    pages: int = 1  # search pages per query; each page costs 100 quota units
    small_subs: int = 20_000  # a channel below this counts as small
    new_days: int = 365  # a channel younger than this counts as new
    breakout_views: int = 50_000
    breakout_ratio: float = 5.0  # views divided by subscribers
    daily_quota: int = 10_000
    reserve: int = 1_000  # units never spent, kept for manual checks
    max_queries_per_run: int = 30
    scan: bool = True  # query-less scan of the most viewed recent videos
    retention_days: int = 30  # YouTube API data is deleted after this


# Heuristic weights of the opportunity index. They are the author's judgment,
# not validated against channel outcomes; change them in radar.toml.
DEFAULT_WEIGHTS = {"demand": 0.35, "openness": 0.30, "breakouts": 0.20, "momentum": 0.15}


@dataclass
class Config:
    markets: dict[str, Market]
    youtube: YouTubeCfg = field(default_factory=YouTubeCfg)
    seeds: list[dict] = field(default_factory=list)  # {"id": str, "terms": {market: term}}
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    contact: str = ""
    data_dir: Path = Path("data")
    trends_per_market: int = 3
    wiki_per_market: int = 3

    @property
    def db_path(self) -> Path:
        return self.data_dir / "radar.sqlite"

    @property
    def manual_csv(self) -> Path:
        return self.data_dir / "manual_signals.csv"


def _markets(raw: dict) -> dict[str, Market]:
    merged = {key: dict(value) for key, value in DEFAULT_MARKETS.items()}
    for key, value in (raw or {}).items():
        merged[key] = {**merged.get(key, {}), **value}
    enabled = {}
    for key, value in merged.items():
        if value.pop("enabled", True):
            enabled[key] = Market(key=key, **value)
    return enabled


def _seeds(raw: list, markets: dict[str, Market]) -> list[dict]:
    seeds = []
    for entry in raw or []:
        everywhere = entry.get("all")
        terms = {
            key: entry.get(key, everywhere)
            for key in markets
            if entry.get(key, everywhere)
        }
        if terms:
            seeds.append({"id": entry.get("id") or next(iter(terms.values())), "terms": terms})
    return seeds


def load(path: str | os.PathLike | None = None) -> Config:
    """Read radar.toml; every key is optional and falls back to the defaults."""
    chosen = Path(path or os.environ.get("RADAR_CONFIG", "radar.toml"))
    raw: dict = {}
    if chosen.exists():
        with chosen.open("rb") as handle:
            raw = tomllib.load(handle)
    elif path:
        raise FileNotFoundError(f"arquivo de configuração não encontrado: {chosen}")

    youtube_raw = raw.get("youtube", {})
    known = {f.name for f in fields(YouTubeCfg)}
    unknown = set(youtube_raw) - known
    if unknown:
        raise ValueError(f"chaves desconhecidas em [youtube]: {sorted(unknown)}")

    markets = _markets(raw.get("markets", {}))
    general = raw.get("general", {})
    return Config(
        markets=markets,
        youtube=YouTubeCfg(**youtube_raw),
        seeds=_seeds(raw.get("seeds", []), markets),
        weights={**DEFAULT_WEIGHTS, **raw.get("weights", {})},
        contact=os.environ.get("RADAR_CONTACT", general.get("contact", "")),
        data_dir=Path(os.environ.get("RADAR_DATA_DIR", general.get("data_dir", "data"))),
        trends_per_market=int(general.get("trends_per_market", 3)),
        wiki_per_market=int(general.get("wiki_per_market", 3)),
    )
